"""Agent 会话服务：管理 checkpointer 生命周期，对外提供 chat / chat_stream。

为什么 checkpointer 要做成单例：
    SqliteSaver 的底层是一个 sqlite3 连接；每个请求新建连接既浪费又容易锁库。
    这里全局一个连接（check_same_thread=False），由 langgraph 负责读写。
    正式部署可换成 PostgresSaver 或用连接池。

thread_id 约定：
    一次行程规划 = 一个 thread_id（例如 f'trip-{user_id}-{日期}'），
    这样用户中断后回来还能接着聊。
"""
import asyncio
import logging
import re

from langgraph.errors import GraphRecursionError

from config.config import settings
from config.database import SessionLocal
from agent.evidence import EvidenceDigest, normalize_citations
from agent.metrics import TurnMetrics, record_turn
from agent.middleware import RETRY_MARK

logger = logging.getLogger('ski.agent')

# 懒加载并缓存的异步 checkpointer（底层是一个 aiosqlite 连接）
_checkpointer = None

# 编译好的 Agent 缓存：键 = (画像签名, 配置指纹)，见 AgentService.aget_agent
_agent_cache: dict = {}
_AGENT_CACHE_MAX = 16

# 触发结构化输出时，langchain 会插入一条内部消息，不能直接给用户看
_INTERNAL_PREFIX = 'Returning structured response'

#: 模型输出为空、或本轮所有 AI 消息都是内部副作用消息时的兜底话术
#
# ⚠️ 原来这里写的是**行程专用**的一句（"这次没能给出可执行的行程方案…补一句条件我再排一次"），
# 但空回答并不只出现在排行程上——2026-10-05 端到端评测里"北大湖有几条高级道？"
# 也走到这条分支，用户会收到一句驴唇不对马嘴的"我再排一次"。
# 所以改成与场景无关的通用话术，并**在统计口径里也算降级**（它确实是体验失败）。
EMPTY_REPLY_FALLBACK = (
    '这次没能整理出可引用的回答（生成的答案缺少依据或为空）。'
    '换个问法，或指明具体雪场/数据项，我再试一次。'
)


class AgentService:
    """Agent 会话服务（静态方法）：记忆单例 + 对话 + 流式对话

    ⚠️ 异步连接不能跨事件循环复用（这条踩过一次，很难查）：
        aiosqlite 连接与 asyncio.Lock 都绑定在创建它们的那个事件循环上。
        FastAPI/uvicorn 全程只有一个长驻循环，所以缓存单例没问题；
        但同步包装 chat() 内部是 asyncio.run()，**每次都新建一个循环**，
        第二次调用拿到上一次缓存的连接/锁就会炸：
            RuntimeError: <asyncio.locks.Lock object ...> is bound to a different event loop
        结论：同一进程内，**同步 chat() 只适合一次性调用**（命令行单问、测试单用例）。
        要多轮对话，请在同一个 async 上下文里 await achat()（chat_cli 已按此改造）。
    """

    @staticmethod
    async def get_checkpointer():
        """构造/缓存**异步** SQLite checkpointer

        为什么必须换成异步版：整条链路已改成异步（外部工具走百度地图 MCP，
        只能异步调用），而同步的 SqliteSaver 不支持 async 方法，
        会在第一轮就抛 "The SqliteSaver does not support async methods"。
        这就是"改异步"的真实代价——不是把 def 改成 async def 就完了，
        记忆层也得跟着换。
        """
        global _checkpointer
        if _checkpointer is not None:
            return _checkpointer

        # 懒加载 aiosqlite 与异步 checkpointer，避免影响无关模块导入
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        # 刻意不加锁：进程内首个请求本来就是串行的，而锁对象
        # 一旦跨事件循环复用反而会直接报错（见类注释）
        conn = await aiosqlite.connect(settings.CHECKPOINT_DB)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()             # 首次运行需要建表
        _checkpointer = saver
        return _checkpointer

    @staticmethod
    def load_profile(user_id: int | None) -> dict:
        """读用户画像（长期记忆）；没有就返回空 dict"""
        if not user_id:
            return {}
        from service.profile_service import ProfileService

        with SessionLocal() as session:
            return ProfileService(session).get_profile(user_id)

    @staticmethod
    def _profile_signature(profile: dict | None) -> tuple:
        """画像里**真正影响提示词**的字段（决定 PromptBuilder 输出哪个版本的提示词）"""
        profile = profile or {}
        return (str(profile.get('level') or ''), str(profile.get('board_type') or ''))

    @staticmethod
    def _config_signature() -> tuple:
        """影响"装配结果"的配置指纹

        ⚠️ 新增会改变中间件栈/模型装配的配置项时，**必须加到这里**，
        否则改了配置却拿到旧 Agent（缓存命中），排查起来会非常费解。
        测试里若临时改这些值，记得调 AgentService.clear_agent_cache()。
        """
        return (settings.USE_MIDDLEWARE,
                settings.USE_SUMMARY_MIDDLEWARE,
                settings.USE_CONTEXT_EDITING,
                settings.USE_TRIM_HISTORY,
                settings.CITATION_RETRY_ON_FAIL,
                settings.CITATION_RETRY_MISSING_ONLY,
                settings.CITATION_LOOKBACK_TURNS,
                settings.AGENT_MAX_MODEL_CALLS,
                settings.AGENT_MAX_TOOL_CALLS,
                settings.LLM_PROVIDER,
                settings.LLM_MODEL,
                settings.CARD_MODE)

    @staticmethod
    def clear_agent_cache() -> None:
        """清空 Agent 缓存（改了配置或画像后，测试里手动调一次）"""
        _agent_cache.clear()

    @staticmethod
    async def aget_agent(user_id: int | None = None):
        """按当前用户画像装配 Agent（异步：要先拿到异步 checkpointer）

        为什么要缓存（2026-10-05 加）：原来**每个请求都重建** Agent——
        重新构造模型客户端、中间件栈、提示词与结构化输出 schema。
        画像只有几档组合（水平 × 单双板），重建纯属浪费，实测每轮几十~几百毫秒。

        缓存的键必须包含**配置指纹**：中间件栈是在装配时定型的，
        改了开关却复用旧 Agent 会让"改了配置不生效"这种问题极难排查。
        checkpointer（会话记忆）是单例、与 Agent 解耦，所以缓存不会串会话。
        """
        from agent.agent import AgentFactory

        profile = AgentService.load_profile(user_id)
        key = (AgentService._profile_signature(profile), AgentService._config_signature())
        cached = _agent_cache.get(key)
        if cached is not None:
            return cached

        checkpointer = await AgentService.get_checkpointer()
        agent = AgentFactory.create(profile=profile, checkpointer=checkpointer)
        # 画像组合有限，但仍设上限：配置指纹参与键，测试里切换配置会不断产生新键
        if len(_agent_cache) >= _AGENT_CACHE_MAX:
            _agent_cache.pop(next(iter(_agent_cache)))
        _agent_cache[key] = agent
        return agent

    @staticmethod
    def get_agent(user_id: int | None = None):
        """同步包装：给命令行/调试用（不能在已有事件循环里调用）"""
        return asyncio.run(AgentService.aget_agent(user_id))

    @staticmethod
    def _text_of(chunk) -> str:
        """从流式 chunk 里取出纯文本（兼容 content 是字符串或内容块列表两种形态）"""
        content = getattr(chunk, 'content', '')
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get('text'), str):
                    parts.append(block['text'])
            return ''.join(parts)
        return ''

    @staticmethod
    def _clean_reply(reply: str, card, messages: list | None = None) -> str:
        """把内部消息换成一句人话；有行程卡时顺便给个摘要

        ⚠️ 内部前缀（Returning structured response…）是结构化输出的副作用消息，
        **永远不能出现在用户面前**。原来这条分支在 card 为空时会把内部文本原样返回，
        而"卡被字段校验打回"之后 card 恰好是 None —— 整段内部文本就漏给了用户
        （2026-10-03 端到端实测）。所以这里退回到"本轮真正的文字回答"。
        """
        text = normalize_citations(reply).strip()
        if text and not text.startswith(_INTERNAL_PREFIX):
            return text
        if card is not None:
            return f'已生成行程卡：{card.summary}'
        # 只看**本轮**的 AI 消息：拿上一轮的回答顶包，用户会看到"答非所问"
        #（2026-10-03 实测：问行程，却把上一轮的雪具价格又念了一遍）
        current: list = []
        for index in range(len(messages or []) - 1, -1, -1):
            if getattr(messages[index], 'type', '') == 'human':
                current = list(messages[index + 1:])
                break
        for message in reversed(current):
            if getattr(message, 'type', '') != 'ai':
                continue
            content = str(getattr(message, 'content', '') or '').strip()
            if content and not content.startswith(_INTERNAL_PREFIX):
                return normalize_citations(content)
        return EMPTY_REPLY_FALLBACK

    @staticmethod
    def _trip_updated_this_turn(messages) -> bool:
        """本轮是否调用过 set_trip_info（决定要不要兜底补行程卡）

        只在"用户刚更新了行程信息"时补卡；否则用户后面问"雪票多少钱"
        也会被硬塞一张行程卡，很突兀。
        """
        last_human = -1
        for index, message in enumerate(messages):
            if getattr(message, 'type', '') == 'human':
                last_human = index
        for message in messages[last_human + 1:]:
            for call in (getattr(message, 'tool_calls', None) or []):
                if call.get('name') == 'set_trip_info':
                    return True
        return False

    @staticmethod
    async def _fallback_card(result: dict, trip: dict, violations: list[str] | None = None):
        """兜底生成行程卡：模型没主动触发结构化输出时，用独立调用补一次

        为什么需要：response_format 默认走 ToolStrategy，需要模型主动调用隐藏工具，
        GLM 有时不调，导致前端"行程"页空着。这里保证"信息齐全就一定出卡"。

        :param violations: 传了就是**重写**而不是新生成——把校验器的违规清单原样喂回去
                           （见 agent/card_check.py，与引用校验"把错因喂回去"同一套路）

        为什么是 async：整条链路已经改成异步（外部工具走 MCP，只能异步调），
        这里再用同步 invoke 会在事件循环里阻塞。
        """
        try:
            from agent.agent import AgentFactory
            from schemas.trip import TripCard

            # 走工厂的 build_model：换模型 / 关思考模式只改一处，这里跟着一起生效
            model = AgentFactory.build_model(temperature=0)

            # 用最近的工具返回与回答作为素材（只截取尾部，控制 token）
            materials: list[str] = []
            for message in result['messages'][-12:]:
                content = str(getattr(message, 'content', '') or '')
                if content and getattr(message, 'type', '') in ('tool', 'ai'):
                    materials.append(content)
            context = '\n\n'.join(materials)[-6000:]

            prompt = (
                '根据下面的资料与行程要素，生成一份结构化行程卡。\n'
                '铁律：只能使用资料里出现过的价格与雪道信息，不许编造；'
                '资料里没出现的来源不要写进 sources。\n\n'
                f'行程要素：{trip}\n\n资料：\n{context}'
            )
            if violations:
                # 把校验器的具体错因放在**最后**（离生成最近，模型最容易照做）
                prompt += (
                    '\n\n上一次生成的行程卡没通过校验，必须逐条修正后重新生成：\n'
                    + '\n'.join(f'- {issue}' for issue in violations)
                )
            # 必须指定 function_calling：GLM 走默认策略时会返回 markdown 文本而不是 JSON，
            # 导致 "Invalid JSON: expected value at line 1 column 1"
            structured = model.with_structured_output(TripCard, method='function_calling')
            try:
                return await structured.ainvoke(prompt)
            except Exception as first:
                # 把校验错误**原样喂回去**再试一次（2026-09-13 实测）：
                #   glm-4-flash 会把 budget 直接填成数字（用户报的 3000），
                #   而 schema 要的是对象 {"items":[...],"total":...,"per_person":...}，
                #   一次类型不符就整张卡放弃太亏——它看到具体错因后基本都能改对。
                # 刻意**不做类型强转**：宁可没有卡，也不能给一张"合计=人均=3000"的错卡
                #（这项目的命题就是不许糊弄数据）。
                print(f'[warn] 行程卡首次生成失败，喂回错误重试：{type(first).__name__}: {first}')
                retry_prompt = (
                    prompt
                    + f'\n\n上一次生成失败，校验器报的错是：\n{first}\n'
                    '注意 budget 必须是一个**对象**，形如：'
                    '{"items":[{"item":"周末全天雪票","amount":560}],'
                    '"total":1120,"per_person":560}；不能直接写一个数字。'
                )
                return await structured.ainvoke(retry_prompt)
        except Exception as exc:  # 兜底失败不能影响主流程
            print(f'[warn] 行程卡兜底生成失败：{type(exc).__name__}: {exc}')
            return None

    @staticmethod
    def _build_data_card(result: dict, trip: dict, user_id: int | None):
        """从**库内数据**渲染行程卡（零模型调用，见 agent/card_builder.py）

        为什么要有这条路径（2026-10-04 实测）：模型生成卡片 + 字段校验 + 不合格重写，
        在弱模型上能把 40 步 recursion_limit 烧完；而且模型会编预算项。改成数据渲染后：
        预算 ← MySQL 价格行、来源 ← 证据信封、行程 ← 模板骨架、装备 ← 规则表。
        """
        from agent.card_builder import build_card
        from agent.evidence import EvidenceDigest
        from config.database import SessionLocal
        from service.resort_service import ResortService

        if not trip.get('ready') or not trip.get('resort'):
            return None            # 要素不齐 / 还没定雪场：让 Agent 先追问或先推荐
        try:
            with SessionLocal() as session:
                service = ResortService(session)
                info = service.resolve(str(trip.get('resort') or '').strip())
                if not info:
                    return None
                prices = service.get_prices(info['resort_key'])
        except Exception as exc:                                # noqa: BLE001
            print(f'[warn] 行程卡数据渲染失败（本轮不出卡）：{type(exc).__name__}: {exc}')
            return None

        profile = AgentService.load_profile(user_id) or {}
        evidences = EvidenceDigest.of_messages(
            AgentService._turn_tool_messages(result['messages']))
        return build_card(
            resort_name=info['name'],
            trip=dict(trip, level=profile.get('level', '')),
            evidences=evidences,
            prices=prices,
            data_status_note=info.get('data_status_note') or '',
        )

    @staticmethod
    async def _resolve_card(result: dict, trip: dict, user_id: int | None):
        """按 CARD_MODE 决定卡片来源：data=数据渲染（默认）/ model=模型生成"""
        if settings.CARD_MODE == 'data':
            card = AgentService._build_data_card(result, trip, user_id)
            if card is None:
                return None
            # 数据渲染的卡不再"喂回模型重写"（那会重新引入模型调用与绕圈）
            return await AgentService._verify_card(card, result, trip, allow_repair=False)

        card = result.get('structured_response')
        if card is None and trip.get('ready') \
                and AgentService._trip_updated_this_turn(result['messages']):
            card = await AgentService._fallback_card(result, trip)
        if card is not None:
            card = await AgentService._verify_card(card, result, trip)
        return card

    @staticmethod
    async def _verify_card(card, result: dict, trip: dict, allow_repair: bool = True):
        """行程卡字段级校验：不合格先喂回重写一次，仍不合格就不出卡

        为什么放在 service 而不是中间件：中间件管的是**消息流**（正文），
        而结构化卡是 response_format 那条路的产物，不经过消息校验。
        放在这里两条出口（一次性 / 流式）都能覆盖，且离线可测（见 tests/test_card_check.py）。

        :param allow_repair: 数据渲染出来的卡传 False——它的金额直接来自 MySQL 价格行，
            不存在"编造"的问题，也就不需要（也不应该）再调一次模型重写。
        """
        if card is None:
            return None

        from agent.card_check import check_card
        from agent.middleware import _evidence_ids, _lookback_tool_messages

        # ⚠️ 必须剔掉结构化输出自己产生的那条工具消息（"Returning structured response: …"）：
        # 它里面就是这张卡的原文，留着等于**自己给自己作证**——卡里的
        # "教练课 300 元"会因为出现在这条消息里而被判成"有依据"（2026-10-03 实测假阴性）。
        def _usable(message) -> bool:
            return not str(getattr(message, 'content', '') or '').lstrip() \
                .startswith(_INTERNAL_PREFIX)

        # 证据窗口与引用校验一致（settings.CITATION_LOOKBACK_TURNS）：
        # 出卡那一轮往往只有 set_trip_info，没有检索证据——价格是**前面几轮**查的。
        # 只认本轮的话，任何 sources 都会被判成"编号不存在"，等于永远出不了卡。
        evidence_messages = [
            message for message in
            (_lookback_tool_messages(result, settings.CITATION_LOOKBACK_TURNS)
             + AgentService._turn_tool_messages(result['messages']))
            if _usable(message)
        ]
        evidence_text = '\n\n'.join(str(getattr(m, 'content', '') or '')
                                    for m in evidence_messages)
        allowed = _evidence_ids(evidence_messages)

        # 数据渲染的卡跳过"预算有据"这一项：金额来自 MySQL 价格行本身，
        # 不是模型编的（那条检查是给模型生成的预算用的）
        issues = check_card(card, evidence_text, allowed,
                            check_budget=settings.CARD_MODE != 'data')
        if not issues:
            return card

        if not allow_repair:
            # 数据渲染却仍然不合格 = 渲染逻辑有 bug（比如来源编号对不上），
            # 这种情况不该偷偷调模型补救，而是明确报出来
            print(f'[warn] 数据渲染的行程卡未通过校验（本轮不出卡）：{issues}')
            return None

        print(f'[warn] 行程卡未通过字段校验（{len(issues)} 项），喂回模型重写：{issues}')
        repaired = await AgentService._fallback_card(result, trip, violations=issues)
        if repaired is not None:
            remaining = check_card(repaired, evidence_text, allowed)
            if not remaining:
                return repaired
            issues = remaining

        # 两次都不合格：宁可没有卡，也不给一张"看着有来源、数字没依据"的卡
        print(f'[warn] 行程卡重写后仍不合格，本轮不出卡：{issues}')
        return None

    @staticmethod
    def _trip_of(state) -> dict:
        """从最终状态里取出行程要素 + 现算 ready"""
        from agent.state import TripHelper

        trip = {key: state.get(key) for key in
                ('resort', 'date', 'days', 'people', 'budget', 'board')
                if state.get(key) is not None}
        trip['ready'] = TripHelper.is_ready(state)
        return trip

    @staticmethod
    async def achat(user_id: int | None, thread_id: str, message: str) -> dict:
        """一轮对话（异步）—— 项目的**主实现**

        为什么必须异步：外部工具走百度地图 MCP，而 MCP 工具只能异步调用
        （langchain-mcp-adapters 只提供 coroutine，没有同步实现）。
        整条链路因此统一成异步，同步入口 chat() 只是它的包装。

        返回：
            reply      最终文字回复
            structured 结构化行程卡（TripCard 对象；没触发时是 None）
            trip       当前行程状态（date/days/people/budget/ready...）
            messages   完整消息列表（调试用）
        """
        from agent.state import UserContext

        agent = await AgentService.aget_agent(user_id)
        metrics = TurnMetrics()
        try:
            result = await agent.ainvoke(
                {'messages': [{'role': 'user', 'content': message}]},
                # recursion_limit 显式给出：LangGraph 默认 25 个 super-step，而本项目
                # 一轮里可能"工具调用 + 引用校验打回重写 + 再合成"，撞上默认值会抛
                # GraphRecursionError——流式路径上它的表现是"卡住不动"，最难排查。
                config={'configurable': {'thread_id': thread_id},
                        'recursion_limit': settings.AGENT_RECURSION_LIMIT,
                        'callbacks': [metrics]},
                context=UserContext(uid=user_id),
            )
        except GraphRecursionError:
            # 不把异常抛给前端：降级成一句人话，并保留会话记忆（下次还能接着问）
            logger.warning('本轮超出 recursion_limit=%s，已降级', settings.AGENT_RECURSION_LIMIT)
            return {
                'reply': '这轮我查得太久了（工具调用次数超上限），先停下。'
                         '把问题说得更具体一点（比如指明雪场或数据项），我再试一次。',
                'structured': None,
                'trip': {},
                'messages': [],
                'evidences': [],
            }
        trip = AgentService._trip_of(result)
        # 卡片来源由 CARD_MODE 决定：data=库内数据渲染（默认）/ model=模型生成
        raw_reply = AgentService._clean_reply(result['messages'][-1].content, None,
                                              result['messages'])
        final_text = AgentService._text_of(result['messages'][-1])
        failed = AgentService._is_degraded(raw_reply) and (
            raw_reply != EMPTY_REPLY_FALLBACK or not final_text.startswith(_INTERNAL_PREFIX))
        card = None if failed else await AgentService._resolve_card(result, trip, user_id)
        reply = AgentService._present(
            AgentService._clean_reply(result['messages'][-1].content, card, result['messages']),
            result['messages'])
        AgentService._record_turn_metrics(metrics, thread_id, reply, card)

        return {
            'reply': reply,
            'guidance': AgentService._guidance(result, reply, user_id),
            'structured': card,
            'trip': trip,
            'messages': result['messages'],
            # 本轮证据摘要（给前端做"来源溯源"）：回答里的 [E201] 从此可以点开看
            # "谁说的、哪天的、几手数据"。只取最后一条 human 之后的工具消息，
            # 避免把前几轮的证据一起端出去（带 checkpointer 时 messages 是整段会话）。
            'evidences': EvidenceDigest.of_messages(AgentService._turn_tool_messages(result['messages'])),
        }

    @staticmethod
    def _guidance(result: dict, reply: str, user_id: int | None) -> dict | None:
        if not reply or AgentService._is_degraded(reply):
            return None
        from service.guidance_service import build_guidance

        try:
            profile = AgentService.load_profile(user_id) if user_id else {}
            return build_guidance(result, profile)
        except Exception:
            logger.warning('情境资源不可用，本轮不附 guidance', exc_info=True)
            return None

    @staticmethod
    def _is_degraded(reply: str) -> bool:
        """这一轮是不是被**引用校验降级**了（不是"合法拒答"）

        为什么不能用"包含'暂无可靠来源'"来判断（2026-10-05 踩到）：
            合法的未收录拒答也是模型用中文说的，文案里同样有"暂无可靠来源"
            （实测："暂无可靠来源。二世谷滑雪场的雪票价格数据未在当前数据库中收录…"）。
            用子串判断会把**正确拒答**记成降级，指标就假了。
            所以只认我们自己写死的降级话术（DEGRADE_REPLY）。
        """
        from agent.middleware import DEGRADE_REPLY

        return bool(reply) and (DEGRADE_REPLY[:12] in reply
                                or EMPTY_REPLY_FALLBACK[:12] in reply)

    @staticmethod
    def _record_turn_metrics(metrics, thread_id: str, reply: str, card) -> None:
        """记一轮账本（见 agent/metrics.py）

        观测是**附属品**：这里出任何问题都不该影响对话，所以最外层由 record_turn 自己兜。
        """
        record_turn(
            metrics,
            thread=thread_id,
            degraded=AgentService._is_degraded(reply),
            card=card is not None,
            citations=len(re.findall(r'\[E\d+\]', reply or '')),
        )

    @staticmethod
    def _present(reply: str, messages: list) -> str:
        """降级回答不再补字。其余回答若漏了第二个口径或把未观看链接说成推荐，补一句档案里已有的话。"""
        if AgentService._is_degraded(reply):
            return reply
        from service.run_brief import present_reply
        evidence = '\n'.join(str(getattr(message, 'content', '') or '')
                             for message in AgentService._turn_tool_messages(messages))
        humans = [str(getattr(message, 'content', '') or '')
                  for message in (messages or []) if getattr(message, 'type', '') == 'human']
        question = humans[-1] if humans else ''
        return present_reply(reply, evidence, question, humans[:-1])

    @staticmethod
    def _turn_tool_messages(messages: list) -> list:
        """取"本轮"的工具消息：最后一条 human 之后的部分

        和中间件的 _turn_messages 是同一个道理——带 checkpointer 时
        result['messages'] 是整段会话，不过滤就会把历史证据当成"本轮依据"。
        """
        last_human = 0
        for index, message in enumerate(messages or []):
            if getattr(message, 'type', '') == 'human':
                last_human = index
        return [m for m in (messages or [])[last_human:]
                if getattr(m, 'type', '') == 'tool']

    @staticmethod
    def chat(user_id: int | None, thread_id: str, message: str) -> dict:
        """一轮对话（同步包装）：给命令行与测试用

        注意：**不能在已有事件循环里调用**（asyncio.run 会报
        "cannot be called from a running event loop"）。
        FastAPI 端点请直接用 await achat()。
        """
        return asyncio.run(AgentService.achat(user_id, thread_id, message))

    # 工具名 -> 用户看得懂的一句"正在做什么"
    #
    # 为什么要把它说出来：一轮对话实测要 30~90 秒，中间全是静默等待，
    # 而这些等待其实每一段都有明确语义（在查价格 / 在查天气）。
    # 不报进度，用户只会以为"卡死了"；报出来，等待就从"黑箱"变成"看得见的工序"——
    # 这正好也是本项目"机制看得见"的一部分（和来源可点开是同一个思路）。
    TOOL_STATUS = {
        'search_resort_kb': '正在检索雪场资料',
        'get_resort_runs': '正在查雪道明细',
        'get_resort_price': '正在查价格',
        'get_weather': '正在查天气',
        'get_route': '正在算交通路线',
        'search_nearby': '正在查周边配套',
        'set_trip_info': '正在记下行程要素',
        'update_profile': '正在更新你的画像',
    }

    @staticmethod
    def _retry_marked(state) -> bool:
        """本轮（最后一条 human 之后）是否已经出现"打回重写"标记

        ⚠️ 只看本轮，不能看整段会话：
            降级那轮留下的 RETRY_MARK 会一直躺在历史里，
            看整段会话的话，**下一轮一开口就会被误判成"正在重写"**，
            前端会莫名其妙清空气泡。
        """
        messages = list((state or {}).get('messages') or [])
        last_human = 0
        for index, message in enumerate(messages):
            if getattr(message, 'type', '') == 'human':
                last_human = index
        return any(RETRY_MARK in AgentService._text_of(m) for m in messages[last_human:])

    @staticmethod
    async def achat_stream(user_id: int | None, thread_id: str, message: str):
        """SSE 流式对话（异步生成器）：逐段产出事件字典

        事件类型：
            {"type": "status", "text": "..."} 进度（正在查价格…），不进入回答正文
            {"type": "text", "text": "..."}  文本增量
            {"type": "reset", "reason": "..."} **清空重来**（引用校验打回重写时）
            {"type": "trip", "trip": {...}}  行程状态
            {"type": "card", "card": {...}}  结构化行程卡
            {"type": "evidences", "evidences": [...]} 本轮证据（来源溯源用）
            {"type": "guidance", "guidance": {...}} 正常完成后的情境交互

        ⚠️ 为什么必须有 reset（流式 + 后置校验的必然冲突）：
            引用校验是 after_model 中间件——它在**整段答案生成完之后**才判断合格与否。
            而流式是在生成过程中就把 token 推出去了，所以"被打回的那一版"已经到用户眼前了。
            没有 reset 的话，用户会看到"被判不合格的回答 + 重写后的回答"叠在一起（实测会很难看）。
            有了 reset，前端清空气泡重写；顺带把"校验真的在工作"这件事露了出来。

        ⚠️ 为什么 reset 要两条腿走路：
            chunk 嗅探依赖"节点输出也会进 messages 流"（langgraph 的
            StreamMessagesHandler 会同时吐 LLM token 与节点产出，本机实测成立），
            但这属于**实现细节**——换个版本就可能变。所以再加一条兜底：
            从 values（整份 state）里数本轮有没有 RETRY_MARK。
            两条路都指向同一次打回，用 reset_sent 保证只通知一次。
        """
        from agent.state import UserContext

        agent = await AgentService.aget_agent(user_id)
        metrics = TurnMetrics()
        final_state = None
        pending = ''
        suppressed = False
        reset_sent = False

        # 先给个起点状态：首字要等模型首 token，这段时间不说明白就是"白屏等 5 秒"
        yield {'type': 'status', 'text': '正在理解你的问题…'}

        # 1.逐块消费模型输出
        async for mode, payload in agent.astream(
            {'messages': [{'role': 'user', 'content': message}]},
            config={'configurable': {'thread_id': thread_id},
                    'recursion_limit': settings.AGENT_RECURSION_LIMIT,
                    'callbacks': [metrics]},
            context=UserContext(uid=user_id),
            stream_mode=['messages', 'values'],
        ):
            if mode == 'messages':
                chunk, _meta = payload
                chunk_text = AgentService._text_of(chunk)
                kind = str(getattr(chunk, 'type', '')).lower()

                # 1.1 打回重写：中间件插入的 SystemMessage 里带 RETRY_MARK。
                #     它本身不会被推给用户（下面只放行 ai 类型），但**必须在这里发 reset**，
                #     否则前面已经流出去的那一版会被留在气泡里。
                if RETRY_MARK in chunk_text or '引用校验未通过' in chunk_text:
                    pending = ''
                    suppressed = False
                    if not reset_sent:
                        reset_sent = True
                        yield {'type': 'reset', 'reason': '引用校验未通过，正在核对重写'}
                    continue

                # 1.2 工具调用：只当作"进度"，绝不当作回答正文
                if kind == 'tool':
                    name = str(getattr(chunk, 'name', '') or '')
                    yield {'type': 'status', 'name': name,
                           'text': AgentService.TOOL_STATUS.get(name, '正在查资料') + '…'}
                    continue

                # 1.3 模型输出
                if suppressed or not kind.startswith('ai'):
                    continue
                pending += chunk_text
                stripped = pending.lstrip()
                # 先攒一小段：如果开头是内部消息前缀，就整段丢掉，不要吐给前端
                if len(stripped) < len(_INTERNAL_PREFIX) and _INTERNAL_PREFIX.startswith(stripped):
                    continue
                if stripped.startswith(_INTERNAL_PREFIX):
                    suppressed = True
                    pending = ''
                    continue
                if pending:
                    yield {'type': 'text', 'text': pending}
                    pending = ''
            else:
                final_state = payload
                # 1.4 兜底：从整份 state 里发现"打回"（见方法注释）
                if not reset_sent and AgentService._retry_marked(payload):
                    reset_sent = True
                    pending = ''
                    suppressed = False
                    yield {'type': 'reset', 'reason': '引用校验未通过，正在核对重写'}

        if pending and not suppressed:
            yield {'type': 'text', 'text': pending}

        # 2.收尾：行程状态 + 结构化行程卡
        if final_state:
            trip = AgentService._trip_of(final_state)
            yield {'type': 'trip', 'trip': trip}
            final_text = AgentService._text_of(final_state['messages'][-1])
            reply = AgentService._clean_reply(final_text, None, final_state['messages'])
            failed = AgentService._is_degraded(reply) and (
                reply != EMPTY_REPLY_FALLBACK or not final_text.startswith(_INTERNAL_PREFIX))
            if failed:
                card = None
                # 校验中间件直接写入的降级消息未必经过模型 token 流。
                # 用最终校验结果覆盖气泡，清掉已流出的失败回答及重写残片。
                yield {'type': 'replace', 'text': reply}
            elif settings.CARD_MODE == 'data':
                # 数据渲染：**零模型调用**，所以不需要"正在整理行程卡…"的等待提示
                card = await AgentService._resolve_card(final_state, trip, user_id)
            else:
                card = final_state.get('structured_response')
                if card is None and trip.get('ready') \
                        and AgentService._trip_updated_this_turn(final_state['messages']):
                    # 兜底出卡要**再单独调一次模型**（实测 10~30 秒）。不提前说一声，
                    # 用户会看到回答已经流完了却还在转圈，以为卡住了。
                    yield {'type': 'status', 'text': '正在整理行程卡…'}
                    card = await AgentService._fallback_card(final_state, trip)
                if card is not None:
                    # 与一次性路径同一个校验器：卡不能因为走流式就少一道闸
                    card = await AgentService._verify_card(card, final_state, trip)
            # 来源溯源：流式路径也把本轮证据端出去（浏览器演示走这条）
            yield {'type': 'evidences',
                   'evidences': EvidenceDigest.of_messages(
                       AgentService._turn_tool_messages(final_state['messages']))}
            shown = AgentService._clean_reply(final_text, None, final_state['messages'])
            reply = AgentService._present(shown, final_state['messages'])
            grounded_intro = final_state['messages'][-1].additional_kwargs.get('grounded_intro')
            if grounded_intro and not failed:
                # 裸雪场介绍由本轮证据渲染，覆盖可能已经流出的模型扩写。
                yield {'type': 'replace', 'text': reply}
            elif normalize_citations(final_text) != final_text and not failed:
                yield {'type': 'replace', 'text': reply}
            elif reply != shown and not AgentService._is_degraded(shown):
                if reply.startswith(shown):
                    yield {'type': 'text', 'text': reply[len(shown):]}
                else:
                    # 整段换成档案开头。必须先换掉气泡，再补行程卡摘要，否则替换会把摘要清掉。
                    yield {'type': 'replace', 'text': reply}
            if card is not None:
                yield {'type': 'text', 'text': f'已生成行程卡：{card.summary}'}
                yield {'type': 'card', 'card': card.model_dump()}
            guidance = AgentService._guidance(final_state, reply, user_id)
            if guidance is not None:
                yield {'type': 'guidance', 'guidance': guidance}
            # 账本：流式路径的"最终回答"取最后一条消息（降级话术也在那里）
            AgentService._record_turn_metrics(
                metrics, thread_id,
                reply, card)


# ---------------- 兼容旧调用 ----------------
get_checkpointer = AgentService.get_checkpointer
load_profile = AgentService.load_profile
get_agent = AgentService.get_agent
aget_agent = AgentService.aget_agent
chat = AgentService.chat
achat = AgentService.achat
chat_stream = AgentService.achat_stream
achat_stream = AgentService.achat_stream
