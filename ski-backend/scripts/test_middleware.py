"""引用校验中间件的离线验证：用假模型驱动完整 Agent 图，不需要 API key。

用法（在 ski-backend 目录下）：
    python -m scripts.test_middleware

⚠️ 需要 MySQL（但不联网、不调真模型，2026-10-03 实测确认）：
    假模型只负责"说错话"，而**工具是真的**——get_resort_price / get_resort_runs
    会真查滑雪库。库没起时这里会报 OperationalError: Access denied，
    看着像项目坏了，其实只是没准备好数据库（跑 python -m scripts.check_env 确认）。
    pytest 里这一套接了 requires_db：没库就跳过，不误报成失败。

为什么要有这个脚本：
    引用校验是"机制"不是"提示词"，机制就必须能被确定性地验证。
    但真模型输出不稳定，没法拿它做回归——所以这里用 GenericFakeChatModel
    按剧本喂消息，把"模型编数字 / 引用不存在的编号 / 死活不改"这几种情况
    逐一复现，确认中间件真的拦得住。

它测什么：
    1. 证据编号是否被平移到全轮唯一的编号段（E1xx / E2xx / E3xx）
    2. 模型编造数字（35 条）时是否被打回重写
    3. 模型引用不存在的编号时是否被打回
    4. 两次都不改时是否降级为"暂无可靠来源"而非把假数据给用户
    5. 历史裁剪会不会制造"孤儿工具消息"（那会让模型 API 直接报错）
    6. 跨轮引用上一轮的编号时**不该**误杀（实测踩到的误伤），但仍拦得住编造内容
"""
import asyncio
import sys

# 允许直接以脚本方式运行（python scripts/test_middleware.py）
if __package__ in (None, ''):
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from contextlib import contextmanager

from agent.middleware import (CUSTOM_MIDDLEWARE, DEGRADE_REPLY, EVIDENCE_BLOCK,
                              MiddlewareFactory, RETRY_MARK, _check_facts,
                              _evidence_ids, _lookback_tool_messages,
                              _shift_indices, trim_history)
from agent.state import TripState, UserContext
from agent.tools import TOOLS

PASSED = 0
FAILED = 0


class ScriptedToolModel(GenericFakeChatModel):
    """剧本化假模型，补上 bind_tools

    GenericFakeChatModel 默认没实现 bind_tools，而 create_agent 装配时一定会调它，
    直接用它会在图的第一跳就 NotImplementedError。这里补一个空实现即可——
    反正剧本里已经写好了要调哪个工具，不需要真的绑定。
    """

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self


def check(name: str, ok: bool, detail: str = '') -> None:
    """打印一条断言结果"""
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f'  ✅ {name}')
    else:
        FAILED += 1
        print(f'  ❌ {name}  {detail}')


async def run_agent(scripted: list, question: str, history: list | None = None,
                    tools: list | None = None, full_stack: bool = False) -> dict:
    """用剧本化的假模型跑完整 Agent 图

    这里只挂自研件（CUSTOM_MIDDLEWARE）：摘要中间件会拿假模型去生成摘要，
    把剧本里的消息提前吃掉。官方件的装配正确性由 test_stack() 单独验证。

    history 用来伪造"前面已经聊过几轮"的会话（跨轮引用那组用例需要）：
    带 checkpointer 时 state 里本来就有历史，这里直接把历史喂进 messages 更直接。
    """
    model = ScriptedToolModel(messages=iter(scripted))
    agent = create_agent(model=model, tools=TOOLS if tools is None else tools, state_schema=TripState,
                         context_schema=UserContext,
                         middleware=MiddlewareFactory.build(model) if full_stack else CUSTOM_MIDDLEWARE,
                         system_prompt='你是滑雪情报官，必须标注来源编号。')
    messages = list(history or []) + [{'role': 'user', 'content': question}]
    return await agent.ainvoke({'messages': messages}, context=UserContext(uid=1))


def tool_call(name: str, args: dict, call_id: str = 'c1') -> AIMessage:
    """构造一条"模型要调工具"的消息"""
    return AIMessage(content='', tool_calls=[
        {'name': name, 'args': args, 'id': call_id, 'type': 'tool_call'}])


# ---------------- 1. 编号平移 ----------------

def test_shift():
    print('\n[1] 证据编号平移：保证全轮唯一')
    text = '[E1] (A 官方|某来源)\n内容\n\n[E2] (A 官方|某来源)\n内容二'
    outs = {name: _shift_indices(text, block).split('\n')[0]
            for name, block in EVIDENCE_BLOCK.items()}
    check('搜索类 -> E101 起', outs['search_resort_kb'].startswith('[E101]'), outs['search_resort_kb'])
    check('统计类 -> E201 起', outs['get_resort_runs'].startswith('[E201]'), outs['get_resort_runs'])
    check('价格类 -> E301 起', outs['get_resort_price'].startswith('[E301]'), outs['get_resort_price'])
    check('各工具编号段互不重叠', len(set(outs.values())) == len(outs))


# ---------------- 2. 编造数字被打回 ----------------

def _blocked(contents: list[str]) -> bool:
    """这次回答有没有被引用校验拦下

    默认路径是**直接降级**（`DEGRADE_REPLY`）；把 `CITATION_RETRY_ON_FAIL`
    打开时则是"打回重写"（出现 `RETRY_MARK`）。两种都算"拦下"，
    这样断言不会因为配置切换而整片碎掉（2026-10-05 换默认行为时踩到过）。
    """
    return (any(DEGRADE_REPLY[:12] in c for c in contents)
            or any(RETRY_MARK in c for c in contents))


@contextmanager
def retry_enabled():
    """临时打开"打回重写"开关——备用路径也要有覆盖，不能只测默认路径"""
    from config.config import settings

    original = settings.CITATION_RETRY_ON_FAIL
    settings.CITATION_RETRY_ON_FAIL = True
    try:
        yield
    finally:
        settings.CITATION_RETRY_ON_FAIL = original


async def test_fabricated_number():
    """编造数字（35 条）必须被拦下，用户看不到假数据

    2026-10-05 起默认**直接降级**（依据见 `config.CITATION_RETRY_ON_FAIL` 注释：
    20 轮模型对比里只有最弱的 glm-4-flash 触发过打回，重写收益小、成本明确）。
    这里同时验证备用路径：打开开关后"重写"确实能把 35 改回 32。
    """
    print('\n[2] 模型编造数字（35 条）-> 默认直接拦下（降级）')
    scripted = [
        tool_call('get_resort_runs', {'resort': 'wanlong'}),
        AIMessage(content='万龙一共有 35 条雪道，其中高级道 15 条。[E201]'),
        AIMessage(content='万龙官方公布 32 条雪道，其中高级道 19 条。[E201]'),
    ]
    result = await run_agent(list(scripted), '万龙有几条雪道')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('被拦下', _blocked(contents))
    check('用户没有看到编造的 35 条',
          '35 条' not in str(result['messages'][-1].content))

    with retry_enabled():
        result = await run_agent(list(scripted), '万龙有几条雪道')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('开着重写开关时：触发重写（备用路径仍可用）',
          any(RETRY_MARK in c for c in contents))
    check('开着重写开关时：模型改用正确数字 32',
          '32' in str(result['messages'][-1].content))


# ---------------- 3. 引用不存在的编号被打回 ----------------

async def test_bad_citation():
    print('\n[3] 模型引用不存在的编号（E999）-> 默认直接拦下')
    scripted = [
        tool_call('get_resort_runs', {'resort': 'wanlong'}),
        AIMessage(content='万龙有 32 条雪道。[E999]'),
        AIMessage(content='万龙官方公布 32 条雪道。[E201]'),
    ]
    result = await run_agent(list(scripted), '万龙有几条雪道')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('被拦下', _blocked(contents))

    with retry_enabled():
        result = await run_agent(list(scripted), '万龙有几条雪道')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('开着重写开关时：提示里指出了不存在的编号',
          any('E999' in c for c in contents if RETRY_MARK in c))


# ---------------- 4. 屡教不改 -> 降级 ----------------

async def test_degrade():
    print('\n[4] 模型坚持不改 -> 降级，不把假数据给用户')
    result = await run_agent([
        tool_call('get_resort_runs', {'resort': 'wanlong'}),
        AIMessage(content='万龙一共有 35 条雪道。[E201]'),
        AIMessage(content='万龙一共有 35 条雪道。[E201]'),
        AIMessage(content='万龙一共有 35 条雪道。[E201]'),
    ], '万龙有几条雪道')
    final = str(result['messages'][-1].content)
    check('最终回复是降级话术', DEGRADE_REPLY[:12] in final, final[:60])
    check('用户没有看到编造的 35 条', '35' not in final, final[:60])


# ---------------- 5. 正常回答不被误伤 ----------------

async def test_happy_path():
    print('\n[5] 正常回答（引用正确、数字来自证据）-> 不被打回')
    result = await run_agent([
        tool_call('get_resort_runs', {'resort': 'wanlong'}),
        AIMessage(content='万龙官方公布 32 条雪道，其中高级道 19 条。[E201]'),
    ], '万龙有几条雪道')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('没有触发重写', not any(RETRY_MARK in c for c in contents))
    check('回答原样返回',
          '32' in str(result['messages'][-1].content))


async def test_markdown_citations_keep_fact_and_id_guards():
    print('\n[5b] Markdown 来源链接只做格式归一，不放宽编号和数字校验')
    for answer, should_pass in (
        ('万龙共 32 条雪道。[资料](E201)', True),
        ('万龙共 32 条雪道。[资料](E999)', False),
        ('万龙共 777 条雪道。[资料](E201)', False),
    ):
        # 固定为直接降级，避免受本机重试开关影响耗尽假模型剧本。
        from config.config import settings
        original = settings.CITATION_RETRY_ON_FAIL
        settings.CITATION_RETRY_ON_FAIL = False
        try:
            result = await run_agent([
                tool_call('get_resort_runs', {'resort': 'wanlong'}),
                AIMessage(content=answer),
            ], '万龙有几条雪道')
        finally:
            settings.CITATION_RETRY_ON_FAIL = original
        degraded = DEGRADE_REPLY in str(result['messages'][-1].content)
        check(answer, degraded != should_pass)


# ---------------- 6. 调了工具但没拿到证据 -> 仍要拦 ----------------

async def test_no_evidence_guard():
    """工具返回"未收录"（不带任何 [Ex]）时，回答里的事实数字仍属无据可依

    这是跑真模型才暴露的缝隙：原来的实现写的是
        if not allowed: return None
    即"本轮没有证据就整段跳过校验"。可模型完全可以无视工具那句
    "本库暂未收录"，转头凭记忆编一个价格讲给用户——而这条路径无人看守。
    所以必须区分"压根没调工具"（真寒暄，放行）和"调了但空手而归"（要拦）。
    """
    print('\n[6] 调了工具但没拿到证据 -> 编造的数字仍要被拦')
    result = await run_agent([
        tool_call('get_resort_price', {'resort': '二世谷'}),
        AIMessage(content='二世谷全天雪票大概 500 元，比崇礼便宜。[E301]'),
        AIMessage(content='抱歉，本库暂未收录二世谷，我没有它的价格资料。'),
    ], '二世谷雪票多少钱')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('被拦下（调了工具却空手而归）', _blocked(contents))
    with retry_enabled():
        result = await run_agent([
            tool_call('get_resort_price', {'resort': '二世谷'}),
            AIMessage(content='二世谷全天雪票大概 500 元，比崇礼便宜。[E301]'),
            AIMessage(content='抱歉，本库暂未收录二世谷，我没有它的价格资料。'),
        ], '二世谷雪票多少钱')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('开着重写开关时：指出本轮没有可用资料',
          any('没有取得任何可用资料' in c for c in contents if RETRY_MARK in c))
    check('最终回复不再出现编造的 500 元',
          '500 元' not in str(result['messages'][-1].content))


# ---------------- 7. 一个工具都没调 -> 也不许凭空输出具体事实 ----------------

async def test_no_tool_guard():
    """没调工具时：寒暄放行，但只要出现"具体事实痕迹"就必须拦

    这是用户明确要求的一条：不知道的就说不知道，不许在没有任何数据与
    事实依据的情况下输出。注意判断标准是**有没有具体事实**，不是有没有调工具——
    否则模型只要绕开工具，就能凭记忆随便讲。
    """
    print('\n[7] 一个工具都没调 -> 凭空输出具体事实要拦，寒暄要放行')

    # 7a 复现真模型跑出来的那一次：推荐电影，没调任何工具
    result = await run_agent([
        AIMessage(content='推荐你看《The Art of FLIGHT》（2011），单板玩家必看。'),
        AIMessage(content='这个我没法推荐——我的资料库里没有影视相关内容，'
                          '只能帮你查雪场信息。'),
    ], '推荐一部滑雪电影')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('推荐电影被拦下', _blocked(contents))
    check('最终回复不再出现片名',
          'The Art of FLIGHT' not in str(result['messages'][-1].content))

    # 7b 没调工具也没编具体事实 -> 该放行
    result = await run_agent([
        AIMessage(content='你好！我是滑雪情报官，可以帮你查雪场、排行程。'
                          '你打算去哪个雪场、滑几天？'),
    ], '你好')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('纯寒暄/反问不被误伤', not _blocked(contents))

    # 7c 复述用户自己给的数字，不算编造
    result = await run_agent([
        AIMessage(content='预算 3000 元我记下了，你打算哪几天去？'),
    ], '我预算 3000')
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('复述用户给的预算不被误伤', not _blocked(contents))


# ---------------- 8. 推算规则：同单位可算，跨单位绝不合并 ----------------

def test_derive_rules():
    """事实校验的推算边界

    这组用例把四次真实踩坑固化成断言：
      1. 漏报：证据有 16 度、19 条，早期允许任意两数相加 -> 16+19=35，
         编造的"35 条雪道"被放行
      2. 误报：模型算昼夜温差 19℃-4℃=15℃，因为没有"度"的推算规则，
         被打回、最后降级成拒答
      3. 误报：POI 人均 166.8 元被模型四舍五入成 167 元，被判成编造
      4. 误报（2026-10-05 修）：预算多步推算「2 人 × 2 天 × 650 = 2600 元」
         以及「2600 + 800 = 3400 元」合计——旧规则只支持"单价×单个数量"，
         推导不出来 → 整条回答降级成拒答（用户看到"明明算对了却说没来源"）

    最终规则：**同单位内允许加减乘（含单价×人数×天数、两项乘积之和）；
    跨单位绝不合并；条数不参与推算。**
    """
    print('\n[8] 推算规则：同单位可算，跨单位不合并')
    evidence = ('\n'.join([
        '[E401] (A 官方｜百度地图 MCP·天气｜2026-09-12)',
        '实时：晴，气温 22℃',
        '  - 2026-09-13 星期日：白天 晴 19℃ / 夜间 多云 4℃',
        '[E201] (A 官方｜参数表)',
        '万龙滑雪场官方公布雪道共 32 条（初级 4 条、中级 9 条、高级 19 条）。',
        '[E202] 银龙道长 1300 米，最大坡度 15 度',
        '[E301] 全天雪票·自带板·平日 560 元；雪具租赁 200 元；人均 166.8 元',
    ]))
    turn = [HumanMessage(content='三个人去，预算 3000')]

    cases = [
        ('昼夜温差 19-4=15 度。[E401]', False, '同单位温差（曾误报）'),
        ('白天 19 度、夜间 4 度。[E401]', False, '证据原文'),
        ('雪票 560 + 租赁 200 = 760 元。[E301]', False, '同单位合计'),
        ('三人合计 1680 元。[E301]', False, '单价×中文人数'),
        ('人均 167 元。[E301]', False, '四舍五入（曾误报）'),
        ('万龙共 32 条，高级 19 条。[E201]', False, '证据原文'),
        ('万龙共 35 条雪道。[E201]', True, '跨单位凑数（曾漏报）'),
        ('高级道 15 条。[E201]', True, '条数不参与推算'),
        ('最大坡度 45 度。[E202]', True, '编造坡度'),
        ('气温 28 度。[E401]', True, '编造温度'),
    ]
    jpy_evidence = '[E302] 机场接驳 5000 JPY/单程每座'
    jpy_turn = [HumanMessage(content='接驳多少钱')]
    jpy_cases = [
        ('接驳 5000日元。[E302]', False, 'JPY 与日元是同一个金额'),
        ('接驳 5000元。[E302]', True, '日元不能写成人民币'),
        ('接驳往返 10000日元。[E302]', True, '日元不参与加减，不能自行加总'),
    ]
    for answer, should_flag, why in jpy_cases:
        problems = _check_facts(answer, jpy_evidence, jpy_turn)
        check(why, bool(problems) == should_flag,
              f'{answer[:28]} -> {problems}')
    for answer, should_flag, why in cases:
        problems = _check_facts(answer, evidence, turn)
        check(why, bool(problems) == should_flag,
              f'{answer[:28]} -> {problems}')

    # ---- 预算多步推算（2026-10-05 补：单价×人数×天数、两项乘积之和）----
    budget_evidence = ('\n'.join([
        '[E301] 全天雪票·自带板·周末 650 元/人/天；雪具租赁 200 元/人/天',
    ]))
    budget_turn = [HumanMessage(content='2 人玩两天，人均预算 2000')]
    budget_cases = [
        ('2 人 × 2 天 × 650 = 2600 元，租赁 2 人 × 2 天 × 200 = 800 元，'
         '合计 3400 元。[E301]', False, '单价×人数×天数 + 两项乘积之和（曾误报）'),
        ('2 人 2 天雪票合计 2600 元。[E301]', False, '单价×人数×天数'),
        ('雪票 650 元、租赁 200 元。[E301]', False, '证据原文'),
        ('雪票 777 元。[E301]', True, '编造单价'),
        ('2 人 2 天合计 9999 元。[E301]', True, '编造合计（推导不得放大巧合）'),
    ]
    for answer, should_flag, why in budget_cases:
        problems = _check_facts(answer, budget_evidence, budget_turn)
        check(why, bool(problems) == should_flag,
              f'{answer[:30]} -> {problems}')


# ---------------- 9. 中间件栈装配（官方件 + 自研件） ----------------

def test_stack():
    """装配顺序：官方通用件在外层，自研校验件在内层，裁剪兜底在最后

    ⚠️ 刻意**不**断言"共 N 件"：中间件栈会随需求增长（2026-10-04 加了单轮上限
    ModelCallLimit / ToolCallLimit 与 ModelRetry，从 5 件变成 8 件），
    写死数量会让每次合理扩展都要改测试——那种测试只会被当成噪音删掉。
    这里断言的是**结构性契约**：该有的都在、自研件在内层、兜底在最后。
    """
    print('\n[9] 中间件栈装配')
    model = ScriptedToolModel(messages=iter([AIMessage(content='占位')]))
    stack = MiddlewareFactory.build(model)
    names = [type(m).__name__ if not callable(m) else getattr(m, 'name', '?')
             for m in stack]
    check('装配了中间件', len(stack) >= 5, str(names))
    check('含单轮工具调用上限', any('ToolCallLimit' in n for n in names), str(names))
    check('含官方 ToolRetryMiddleware',
          any('ToolRetry' in n for n in names), str(names))
    # 上限与摘要这两件的"在不在栈里"由配置决定，所以断言**配置与栈一致**，
    # 而不是断言"必须有"——写死存在性的测试在合理调整配置时只会变成噪音。
    from config.config import settings
    check('ModelCallLimit 与配置一致（默认 0=不挂：实测它与自研件组合会让模型节点空转）',
          any('ModelCallLimit' in n for n in names)
          == (settings.AGENT_MAX_MODEL_CALLS > 0),
          f'AGENT_MAX_MODEL_CALLS={settings.AGENT_MAX_MODEL_CALLS}｜{names}')
    check('摘要件与配置一致（默认关：关掉后仍超限，证明它不是绕圈根因）',
          any('Summarization' in n for n in names) == settings.USE_SUMMARY_MIDDLEWARE,
          f'USE_SUMMARY_MIDDLEWARE={settings.USE_SUMMARY_MIDDLEWARE}｜{names}')
    check('含自研引用校验', any('citation_check' in n for n in names), str(names))
    # 上下文清理改用官方的 ContextEditingMiddleware（2026-10-04）：
    # 它清空旧工具输出、不动消息结构，比自研"删消息"安全（无孤儿工具消息问题）
    check('含官方 ContextEditingMiddleware',
          any('ContextEditing' in n for n in names), str(names))
    # 结构性契约：自研件必须排在所有官方件之后（官方件管通用工程，自研件管"引用站不站得住"）
    custom = [i for i, n in enumerate(names)
              if n in ('evidence_envelope', 'citation_check', 'trim_history')]
    check('自研件都在官方件内层',
          bool(custom) and all('Middleware' not in n for n in names[min(custom):]),
          str(names))
    check('引用校验排在最后（它要读所有证据）',
          names[-1] == 'citation_check', str(names[-1]))

    # 两级上下文管理的阈值必须分开：裁剪是保险丝，不该抢在摘要前面动作
    from config.config import settings
    check('裁剪阈值远高于摘要保留条数',
          settings.HISTORY_HARD_LIMIT > settings.SUMMARY_KEEP_MESSAGES * 2,
          f'{settings.HISTORY_HARD_LIMIT} vs {settings.SUMMARY_KEEP_MESSAGES}')


# ---------------- 8. 历史裁剪不制造孤儿工具消息 ----------------

async def test_trim():
    print('\n[10] 历史裁剪：不能切出"孤儿工具消息"')
    # 造到超过硬上限为止（它是保险丝，正常情况不该触发）
    from config.config import settings
    rounds = settings.HISTORY_HARD_LIMIT // 3 + 4
    messages = []
    for index in range(rounds):
        messages.append(HumanMessage(content=f'第{index}个问题', id=f'h{index}'))
        messages.append(AIMessage(content='', id=f'a{index}',
                                  tool_calls=[{'name': 'get_resort_runs',
                                               'args': {'resort': 'wanlong'},
                                               'id': f'call{index}', 'type': 'tool_call'}]))
        messages.append(ToolMessage(content='工具返回', tool_call_id=f'call{index}',
                                    id=f't{index}'))
    out = await trim_history.abefore_model({'messages': messages}, None)
    check('超过硬上限后确实裁剪', out is not None and len(out['messages']) > 0)
    kept = messages[len(out['messages']):] if out else messages
    check('裁剪后第一条不是工具消息',
          getattr(kept[0], 'type', '') != 'tool', getattr(kept[0], 'type', ''))
    check('裁剪后不超过硬上限', len(kept) <= settings.HISTORY_HARD_LIMIT + 3,
          str(len(kept)))
    # 没到硬上限时不该动手（否则会把摘要中间件要用的历史提前删掉）
    short = messages[:9]
    check('未超上限时不裁剪',
          await trim_history.abefore_model({'messages': short}, None) is None)


# ---------------- 11. 跨轮引用：不误杀，也不放水 ----------------

def turn_one_history() -> list:
    """伪造"第一轮问雪道"的历史：一条工具消息（E201）+ 一条回答

    用来复现实测抓到的那条误伤路径：
        第一轮问「万龙有几条高级道」→ 第二轮追问「那价格呢」，
        第二轮的回答里带上了上一轮的编号 E201。
    """
    return [
        HumanMessage(content='万龙有几条高级道？', id='h1'),
        AIMessage(content='', id='a1',
                  tool_calls=[{'name': 'get_resort_runs', 'args': {'resort': 'wanlong'},
                               'id': 'call1', 'type': 'tool_call'}]),
        ToolMessage(content='[E201] (A 官方｜参数表)\n'
                            '万龙滑雪场官方公布雪道共 32 条（初级 4 条、中级 9 条、高级 19 条）。',
                    tool_call_id='call1', id='t1'),
        AIMessage(content='万龙共 32 条雪道，高级道 19 条。[E201]', id='a2'),
    ]


async def test_cross_turn_citation():
    """跨轮沿用上一轮的编号，不该被判成"引用了不存在的编号"

    这是 2026-09-12 实测抓到的误伤：先问高级道、再追问价格，第二轮被打回两次，
    最后降级成拒答——**最普通的追问路径被判死**。

    测试重点是**边界**，不只测"放行"：
        该放行的放行（跨轮编号 + 跨轮数字）
        该拦的照旧拦（从没出现过的编号、编造的数字）
        回看窗口关掉（=0）就回到严格模式
    """
    print('\n[11] 跨轮引用：本轮有证据时，可沿用前几轮的依据')
    from config.config import settings

    history = turn_one_history()
    state = {'messages': history + [HumanMessage(content='那价格呢？', id='h2')]}

    # ---- 1.纯函数层：回看窗口到底取到了什么 ----
    lookback = _lookback_tool_messages(state, 3)
    check('回看窗口取到上一轮的工具消息', len(lookback) == 1, str(len(lookback)))
    check('回看窗口能抽出上一轮的编号',
          _evidence_ids(lookback) == {201}, str(_evidence_ids(lookback)))
    check('回看窗口设为 0 时不取任何历史',
          _lookback_tool_messages(state, 0) == [])
    check('只有一轮对话时没有可回看的历史',
          _lookback_tool_messages(
              {'messages': [HumanMessage(content='你好', id='h9')]}, 3) == [])

    # ---- 2.端到端：回答同时带"上一轮的 E201"和"本轮的 E301" ----
    answer = '万龙共 32 条雪道；雪票平日 560 元。[E201][E301]'
    result = await run_agent([
        tool_call('get_resort_price', {'resort': 'wanlong'}, 'c2'),
        AIMessage(content=answer),
    ], '那价格呢？', history=history)
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('沿用上一轮的编号没有被判成"不存在的编号"',
          not _blocked(contents),
          next((c[:70] for c in contents if RETRY_MARK in c), ''))
    check('回答原样返回给用户',
          '32 条雪道' in str(result['messages'][-1].content))

    # ---- 3.仍然拦得住：从没出现过的编号 ----
    result = await run_agent([
        tool_call('get_resort_price', {'resort': 'wanlong'}, 'c3'),
        AIMessage(content='雪票平日 560 元。[E999]'),
        AIMessage(content='雪票平日 560 元。[E301]'),
    ], '那价格呢？', history=history)
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('编造编号（E999）仍被拦下', _blocked(contents))

    # ---- 4.仍然拦得住：本轮与回看窗口里都没有的数字 ----
    result = await run_agent([
        tool_call('get_resort_price', {'resort': 'wanlong'}, 'c4'),
        AIMessage(content='雪票平日 777 元。[E301]'),
        AIMessage(content='雪票平日 560 元。[E301]'),
    ], '那价格呢？', history=history)
    contents = [str(getattr(m, 'content', '')) for m in result['messages']]
    check('编造数字（777 元）仍被拦下', _blocked(contents))

    # ---- 5.回看窗口关掉 = 回到严格模式（证明这个开关真的有用） ----
    settings.CITATION_LOOKBACK_TURNS = 0
    try:
        result = await run_agent([
            tool_call('get_resort_price', {'resort': 'wanlong'}, 'c5'),
            AIMessage(content=answer),
            AIMessage(content='雪票平日 560 元。[E301]'),
        ], '那价格呢？', history=history)
        contents = [str(getattr(m, 'content', '')) for m in result['messages']]
        check('回看关闭（=0）时，跨轮编号回到被拦下的行为',
              _blocked(contents))
    finally:
        settings.CITATION_LOOKBACK_TURNS = 3


def test_missing_only_retry():
    from config.config import settings
    from agent.middleware import _verdict
    original = settings.CITATION_RETRY_MISSING_ONLY, settings.CITATION_RETRY_ON_FAIL
    turn = [AIMessage(content='有19条高级道')]
    try:
        settings.CITATION_RETRY_ON_FAIL = False
        settings.CITATION_RETRY_MISSING_ONLY = False
        missing = ['回答没有标注任何来源编号']
        check('默认漏引用直接降级', 'jump_to' not in _verdict(missing, turn, {201}))
        settings.CITATION_RETRY_MISSING_ONLY = True
        retry = _verdict(missing, turn, {201})
        check('合法证据且仅漏引用允许重答', retry.get('jump_to') == 'model')
        check('已有重答标记不会再重试', 'jump_to' not in _verdict(missing, turn + retry['messages'], {201}))
        check('无证据不允许窄重答', 'jump_to' not in _verdict(missing, turn, set()))
        check('错误编号不允许窄重答', 'jump_to' not in _verdict(['引用了不存在的来源编号：E999'], turn, {201}))
        check('漏引用伴随无据数字直接降级', 'jump_to' not in _verdict(missing + ['这些数字在本轮证据里找不到依据：999元'], turn, {201}))
    finally:
        settings.CITATION_RETRY_MISSING_ONLY, settings.CITATION_RETRY_ON_FAIL = original


async def test_missing_only_graph():
    from config.config import settings
    original = settings.CITATION_RETRY_MISSING_ONLY, settings.CITATION_RETRY_ON_FAIL
    try:
        settings.CITATION_RETRY_MISSING_ONLY = True
        settings.CITATION_RETRY_ON_FAIL = False
        result = await run_agent([
            tool_call('get_resort_runs', {'resort': 'wanlong'}),
            AIMessage(content='万龙有19条高级道。'),
            AIMessage(content='万龙有19条高级道。[E201]'),
        ], '万龙有几条高级道')
        check('窄重答经完整图恢复合法引用', '[E201]' in str(result['messages'][-1].content))
        result = await run_agent([
            tool_call('get_resort_runs', {'resort': 'wanlong'}),
            AIMessage(content='万龙有19条高级道。'),
            AIMessage(content='万龙雪票999999元。[E201]'),
        ], '万龙有几条高级道')
        check('重答新增无据数字仍被降级', DEGRADE_REPLY in str(result['messages'][-1].content))
    finally:
        settings.CITATION_RETRY_MISSING_ONLY, settings.CITATION_RETRY_ON_FAIL = original


def test_quantity_objects():
    from agent.middleware import _check_facts, _check_quantity_objects
    for obj in ('缆车', '索道', '魔毯'):
        check(f'雪道同数字不能支持{obj}', bool(_check_facts(f'共有22条{obj}', '雪道共22条', [])))
        check(f'{obj}同数字不能支持雪道', bool(_check_facts('共有22条雪道', f'{obj}共22条', [])))
        check(f'两对象都有证据允许{obj}', not _check_facts(f'共有22条{obj}', f'22条雪道，22条{obj}', []))
    check('两对象都有证据允许雪道', not _check_facts('22条雪道', '22条索道，22条雪道', []))
    check('无数量否定缺失说明不拦', not _check_quantity_objects('未收录缆车表，不代表现实没有缆车', '22条雪道'))
    check('不按未知对象猜语义', not _check_quantity_objects('22条线路', '22条雪道'))
    check('明确纠正数量对象不拦否定部分', not _check_facts('不是22条缆车，而是22条雪道', '22条雪道', []))
    check('反向语序明确否定部分不拦', not _check_facts('缆车不是22条，雪道共22条', '22条雪道', []))
    check('分级雪道归入雪道对象', not _check_facts('22条初级雪道', '雪道共22条', []))
    check('不把目标明确条数误配给前文对象', bool(_check_quantity_objects('22条雪道', '雪道资料：22条缆车')))


async def test_quantity_object_graph():
    from config.config import settings
    lookback_original = settings.CITATION_LOOKBACK_TURNS
    original = settings.CITATION_RETRY_ON_FAIL, settings.CITATION_RETRY_MISSING_ONLY
    try:
        settings.CITATION_RETRY_ON_FAIL = False
        settings.CITATION_RETRY_MISSING_ONLY = True
        result = await run_agent([
            tool_call('get_resort_runs', {'resort': 'taiwu'}),
            AIMessage(content='太舞共有22条缆车。[E201]'),
        ], '太舞有哪些缆车？')
        check('真图同数字对象错配直接降级不走漏引用重答',
              result['messages'][-1].content == DEGRADE_REPLY)
        history = [HumanMessage(content='先查缆车'),
                   ToolMessage(content='[E601] 万龙有22条缆车。', tool_call_id='old', name='get_resort_lifts'),
                   AIMessage(content='22条缆车。[E601]')]
        settings.CITATION_LOOKBACK_TURNS = 1
        result = await run_agent([
            tool_call('get_resort_runs', {'resort': 'taiwu'}),
            AIMessage(content='历史资料有22条缆车。[E601]'),
        ], '再核对资料', history=history)
        check('允许窗口同时有两对象不误伤缆车', result['messages'][-1].content != DEGRADE_REPLY)
        settings.CITATION_LOOKBACK_TURNS = 0
        result = await run_agent([
            tool_call('get_resort_runs', {'resort': 'taiwu'}),
            AIMessage(content='太舞有22条缆车。[E201]'),
        ], '再核对资料', history=history)
        check('窗口外缆车证据不能挽救对象错配', result['messages'][-1].content == DEGRADE_REPLY)
        result = await run_agent([
            AIMessage(content='历史资料有22条缆车。[E601]'),
        ], '再核对资料', history=history)
        check('本轮无证据仍不借历史放行', result['messages'][-1].content == DEGRADE_REPLY)
    finally:
        settings.CITATION_RETRY_ON_FAIL, settings.CITATION_RETRY_MISSING_ONLY = original
        settings.CITATION_LOOKBACK_TURNS = lookback_original


def test_resort_arg_guard():
    from langchain_core.messages import HumanMessage
    from agent.middleware import _query_resort_guard
    def state(text): return {'messages': [HumanMessage(content=text)]}
    call = {'name': 'get_resort_price', 'args': {'resort': 'dahulushui', 'item': '全天'}, 'id': 'guard'}
    fixed, rejected = _query_resort_guard(call, state('北大湖平日票价'))
    check('唯一用户雪场纠正臆造ID', fixed['args']['resort'] == 'beidahu' and not rejected)
    check('明确禁止借万龙不算查询万龙', _query_resort_guard(call, state('北大湖价格，只用本库；不要拿万龙的价格补。'))[0]['args']['resort'] == 'beidahu')
    check('保留其他参数且不改原请求', fixed['args']['item'] == '全天' and call['args']['resort'] == 'dahulushui')
    for text in ('万龙和北大湖比价', '北大湖和二世谷比价', '多少钱', 'dahulushui多少钱'):
        check('不按模糊/多场/空对象猜名：' + text, _query_resort_guard(call, state(text))[0] is call)
    wrong = {**call, 'args': {'resort': 'wanlong'}}
    check('用户北大湖不能查询已知万龙', bool(_query_resort_guard(wrong, state('北大湖票价'))[1]))
    check('明确未收录雪场参数不纠正', bool(_query_resort_guard({**call, 'args': {'resort': '二世谷'}}, state('北大湖票价'))[1]))
    correct = {**call, 'args': {'resort': '北大湖'}}
    check('合法同场参数不改', _query_resort_guard(correct, state('北大湖票价'))[0] is correct)
    check('用户精确英文ID可纠正', _query_resort_guard(call, state('beidahu票价'))[0]['args']['resort'] == 'beidahu')
    check('非查询写状态工具不改', _query_resort_guard({**call, 'name': 'set_trip_info'}, state('北大湖'))[0]['args']['resort'] == 'dahulushui')
    followup = {'messages': [HumanMessage(content='北大湖'), HumanMessage(content='票价呢')]}
    check('显式省略承接紧邻单场', _query_resort_guard(call, followup)[0]['args']['resort'] == 'beidahu')
    for previous in ('万龙和北大湖', '北大湖和二世谷', '你好'):
        check('歧义/闲聊不承接：' + previous, bool(_query_resort_guard(call, {'messages': [HumanMessage(content=previous), HumanMessage(content='它的票价呢')]})[1]))
    check('没有省略意图不借历史', _query_resort_guard(call, {'messages': [HumanMessage(content='北大湖'), HumanMessage(content='你好')]})[0] is call)
    check('多场合法逐场参数放行', not _query_resort_guard(correct, state('万龙和北大湖分别票价'))[1])
    check('多场未知参数拒绝不猜', bool(_query_resort_guard(call, state('万龙和北大湖分别票价'))[1]))
    check('多场范围外已知参数拒绝', bool(_query_resort_guard({**call, 'args': {'resort': 'taiwu'}}, state('万龙和北大湖分别票价'))[1]))
    check('本轮切换覆盖旧场', _query_resort_guard(call, {'messages': [HumanMessage(content='北大湖'), HumanMessage(content='太舞缆车')]})[0]['args']['resort'] == 'taiwu')


def test_board_arg_guard():
    from langchain_core.messages import HumanMessage
    from agent.middleware import _query_board_guard
    def state(text): return {'messages': [HumanMessage(content=text)]}
    price = {'name': 'get_resort_price', 'args': {'resort': 'rusutsu', 'item': '单板租赁'}, 'id': 'board'}
    fixed = _query_board_guard(price, state('留寿都双板租赁一天多少钱'))
    check('用户问双板，模型传单板要改回双板', fixed['args']['item'] == '双板租赁')
    check('不改原请求对象', price['args']['item'] == '单板租赁')
    bare = {**price, 'args': {'resort': 'rusutsu', 'item': '租赁'}}
    check('只写租赁时补上用户板型', _query_board_guard(bare, state('留寿都双板租赁一天多少钱'))['args']['item'] == '双板租赁')
    ticket = {**price, 'args': {'resort': 'wanlong', 'item': '全天雪票'}}
    check('非租赁项不加板型', _query_board_guard(ticket, state('万龙双板全天雪票多少钱')) is ticket)
    check('用户两种都提不改', _query_board_guard(price, state('单板和双板租赁分别多少钱')) is price)
    check('用户没提板型不改', _query_board_guard(price, state('留寿都租赁多少钱')) is price)
    search = {'name': 'search_resort_kb', 'args': {'query': '单板租赁', 'board': 'snowboard'}, 'id': 's'}
    fixed = _query_board_guard(search, state('留寿都双板租赁一天多少钱'))
    check('检索的板型代码和问句都跟用户走', fixed['args'] == {'query': '双板租赁', 'board': 'ski'})
    state_call = {**price, 'name': 'set_trip_info'}
    check('写状态工具不改', _query_board_guard(state_call, state('双板租赁')) is state_call)


async def test_board_arg_graph():
    result = await run_agent([
        tool_call('get_resort_price', {'resort': '留寿都', 'item': '单板租赁'}),
        AIMessage(content='留寿都没有双板租赁一日价。'),
    ], '留寿都双板租赁一天多少钱')
    tools = [m for m in result['messages'] if getattr(m, 'type', '') == 'tool']
    content = str(tools[0].content) if tools else ''
    check('真实查价不返回单板价格条目', '[E' not in content and '22300' not in content, content[:120])
    check('真实查价说明没有双板价格', '另一种板型' in content, content[:120])


def test_board_answer_check():
    from langchain_core.messages import HumanMessage
    from agent.middleware import _check_board_amounts
    ev = ('[E301] 留寿都单板租赁：22300 JPY/天（录入日期 2026-10-01）\n\n'
          '[E302] 留寿都机场接驳：5000 JPY/单程（录入日期 2026-10-01）')
    ask = lambda text: [HumanMessage(content=text)]
    q_ski = ask('留寿都双板租赁一天多少钱')
    check('问双板答单板金额被拦', bool(_check_board_amounts('双板租赁一天22300日元。[E301]', ev, q_ski)))
    check('没写板型的金额也被拦', bool(_check_board_amounts('租赁一天22300日元。[E301]', ev, q_ski)))
    check('明说是单板价放行', not _check_board_amounts('没有双板价。单板租赁22300日元，不能代替双板。[E301]', ev, q_ski))
    check('两板型同句也放行', not _check_board_amounts('双板暂无价格，单板为22300日元[E301]', ev, q_ski))
    check('其他板型行的金额不在回答里不拦', not _check_board_amounts('留寿都暂无双板租赁价格。', ev, q_ski))
    check('非板型证据行的金额不拦', not _check_board_amounts('机场接驳5000日元。[E302]', ev, q_ski))
    check('用户两种都问不校验', not _check_board_amounts('单板22300日元，双板22300日元', ev, ask('单板和双板租赁各多少钱')))
    check('用户没提板型不校验', not _check_board_amounts('租赁22300日元', ev, ask('留寿都租赁多少钱')))
    both = ev + '\n\n[E303] 留寿都双板租赁：22300 JPY/天'
    check('双板行也有同金额时不拦', not _check_board_amounts('双板租赁22300日元。[E303]', both, q_ski))
    check('用户自己报的数字不拦', not _check_board_amounts('22300日元。', ev, ask('双板租赁是不是22300日元')))
    check('反向：问单板答双板金额被拦', bool(_check_board_amounts(
        '单板租赁3000日元。[E5]', '[E5] 留寿都双板租赁：3000 JPY/天', ask('单板租赁多少钱'))))


async def test_board_answer_graph():
    from config.config import settings
    original = settings.CITATION_RETRY_ON_FAIL, settings.CITATION_LOOKBACK_TURNS
    history = [HumanMessage(content='先查单板'),
               ToolMessage(content='[E601] 留寿都单板租赁：22300 JPY/天（录入日期 2026-10-01）',
                           tool_call_id='old', name='get_resort_price'),
               AIMessage(content='单板租赁22300日元。[E601]')]
    try:
        settings.CITATION_RETRY_ON_FAIL = False
        settings.CITATION_LOOKBACK_TURNS = 1
        result = await run_agent([
            tool_call('get_resort_runs', {'resort': 'wanlong'}),
            AIMessage(content='双板租赁一天22300日元。[E601][E201]'),
        ], '双板租赁一天多少钱', history=history)
        check('真图：双板问题带出单板金额被降级', result['messages'][-1].content == DEGRADE_REPLY)
        result = await run_agent([
            tool_call('get_resort_runs', {'resort': 'wanlong'}),
            AIMessage(content='没有双板价格。之前查到的单板租赁是22300日元。[E601][E201]'),
        ], '双板租赁一天多少钱', history=history)
        check('真图：明说单板价的如实说明放行', result['messages'][-1].content != DEGRADE_REPLY)
    finally:
        settings.CITATION_RETRY_ON_FAIL, settings.CITATION_LOOKBACK_TURNS = original


def test_level_count_objects():
    from agent.middleware import _check_facts, _quantity_objects
    evidence = '分难度合计为 33 条（初级 7 条、中级 16 条、高级 10 条）。13 条缆车 + 7 条魔毯'
    check('证据「初级 7 条」认作雪道', (7.0, 'run') in _quantity_objects(evidence))
    check('「中高级道 5 条」不被「中级」截走', (5.0, 'run') in _quantity_objects('中高级道 5 条'))
    check('初级道 7 条有雪道证据不拦', not _check_facts('北大湖初级道有7条。', evidence, []))
    check('证据里有 7 条魔毯时不拦魔毯', not _check_facts('北大湖有7条魔毯。', evidence, []))
    check('雪道证据不能支持缆车同数', bool(_check_facts('北大湖有16条缆车。', evidence, [])))


async def test_no_tool_for_covered_resort():
    """用真实 Agent 图与离线证据复现漏调工具，不依赖 MySQL/Ollama。"""
    from config.config import settings
    from agent.middleware import NO_TOOL_FOR_RESORT, RETRY_MARK
    from langchain_core.tools import tool

    calls = []
    empty = False

    @tool
    def search_resort_kb(query: str, resort: str = '') -> str:
        """离线雪场查询，记录实际执行的参数。"""
        calls.append({'query': query, 'resort': resort})
        if empty:
            return '未检索到相关资料。暂无可靠来源。'
        name, city = {'beidahu': ('北大湖', '吉林市'), 'taiwu': ('太舞', '崇礼')}[resort]
        return f'[E1] (A 官方｜离线核验资料｜2026-09-13｜✅ 已核对（官方直采）)\n{name}位于{city}。'

    @tool
    def set_trip_info(resort: str) -> str:
        """离线记录行程，不提供雪场事实证据。"""
        return f'已记录雪场：{resort}'

    original = settings.CITATION_RETRY_ON_FAIL
    made_up = '北大湖滑雪场位于日本群马县，是知名滑雪胜地。看雾凇可以去山顶、树丛和湖边，清晨最容易看到，出行前请关注天气。'
    grounded = '北大湖位于吉林市。[E101]'
    try:
        settings.CITATION_RETRY_ON_FAIL = False
        for full_stack in (False, True):
            calls.clear()
            result = await run_agent([AIMessage(content=made_up), AIMessage(content=grounded)],
                                     '北大湖', tools=[search_resort_kb], full_stack=full_stack)
            tools = [m for m in result['messages'] if getattr(m, 'type', '') == 'tool']
            retried = any(RETRY_MARK in str(m.content) for m in result['messages'])
            check(f'漏调工具由图补查一次（全栈={full_stack}）', retried and len(calls) == 1)
            check('工具结果经过证据重编号', bool(tools) and '[E101]' in tools[0].content)
            check('补查后有据回答放行', grounded.replace('。[', '。 [') in result['messages'][-1].content)
            check('错误回答从后续上下文移除', all(m.content != made_up for m in result['messages']))
            check('裸雪场名优先查基本介绍', calls == [{'query': '北大湖 在哪里 基本介绍', 'resort': 'beidahu'}])

        for question, history, expected in (
            ('它的票价呢', [HumanMessage(content='北大湖')], 'beidahu'),
            ('太舞在哪', [HumanMessage(content='北大湖')], 'taiwu'),
            ('北大壶在哪', None, 'beidahu'),
        ):
            calls.clear()
            answer = grounded if expected == 'beidahu' else '太舞位于崇礼。[E101]'
            result = await run_agent([AIMessage(content=made_up), AIMessage(content=answer)],
                                     question, history, tools=[search_resort_kb])
            check('补查正确承接/换场/别名：' + question,
                  len(calls) == 1 and calls[0]['resort'] == expected)

        for question, history in (
            ('它的票价呢', [HumanMessage(content='北大湖和万龙')]),
            ('它的票价呢', [HumanMessage(content='北大湖'), HumanMessage(content='你好')]),
            ('北大湖和万龙分别在哪', None),
            ('二世谷票价，别拿北大湖补答', None),
        ):
            calls.clear()
            await run_agent([AIMessage(content=made_up), AIMessage(content=made_up)],
                            question, history, tools=[search_resort_kb])
            check('补查不猜测歧义或未收录对象：' + question, not calls)

        for answer in ('北大湖位于吉林市。[E999]', '北大湖有777条雪道。[E101]'):
            calls.clear()
            result = await run_agent([AIMessage(content=made_up), AIMessage(content=answer)],
                                     '北大湖在哪', tools=[search_resort_kb])
            check('补查后仍拦截假编号/编造数字：' + answer,
                  result['messages'][-1].content == DEGRADE_REPLY and len(calls) == 1)

        empty = True
        calls.clear()
        result = await run_agent([AIMessage(content=made_up), AIMessage(content='北大湖有777条雪道。')],
                                 '北大湖', tools=[search_resort_kb])
        check('补查无结果时不编造、不循环', result['messages'][-1].content == DEGRADE_REPLY and len(calls) == 1)
        empty = False

        calls.clear()
        result = await run_agent([tool_call('search_resort_kb', {'query': '北大湖在哪', 'resort': 'beidahu'}),
                                 AIMessage(content=grounded)], '北大湖在哪', tools=[search_resort_kb])
        check('正常工具查询不重复补查', len(calls) == 1 and not _blocked([m.content for m in result['messages']]))
        calls.clear()
        result = await run_agent([
            tool_call('set_trip_info', {'resort': '北大湖'}),
            AIMessage(content=made_up), AIMessage(content=grounded),
        ], '北大湖在哪', tools=[search_resort_kb, set_trip_info])
        check('仅记录行程不能替代取证', len(calls) == 1 and result['messages'][-1].content == grounded)

        # 真 Agent 图接真实流式服务：reset 后页面只能保留核验后的回答。
        from unittest.mock import patch
        from service.agent_service import AgentService
        model = ScriptedToolModel(messages=iter([AIMessage(content=made_up), AIMessage(content=grounded)]))
        graph = create_agent(model=model, tools=[search_resort_kb], state_schema=TripState,
                             context_schema=UserContext, middleware=MiddlewareFactory.build(model))

        async def get_graph(user_id=None):
            return graph

        calls.clear()
        with patch.object(AgentService, 'aget_agent', get_graph):
            events = [event async for event in AgentService.achat_stream(None, 'lookup-offline', '北大湖')]
        shown = ''
        for event in events:
            if event['type'] == 'reset':
                shown = ''
            elif event['type'] == 'replace':
                shown = event['text']
            elif event['type'] == 'text':
                shown += event['text']
        check('真实图流式补查只发一次 reset', sum(e['type'] == 'reset' for e in events) == 1)
        check('流式页面清除失败草稿', '北大湖位于吉林市。 [E101]' in shown and '日本群马县' not in shown, shown)
        check('流式返回补查证据', any(e['type'] == 'evidences' and e['evidences'] for e in events))

        calls.clear()
        result = await run_agent([
            tool_call('search_resort_kb', {'query': '北大湖在哪', 'resort': 'beidahu'}),
            AIMessage(content='北大湖位于吉林市，欢迎来玩。'),
        ], '北大湖', tools=[search_resort_kb])
        intro = result['messages'][-1]
        check('裸雪场名有证据时直接渲染介绍，不靠模型补编号',
              intro.additional_kwargs.get('grounded_intro') and '北大湖位于吉林市。 [E101]' in intro.content)
        check('介绍保留来源、日期和可信度', all(s in intro.content for s in ('离线核验资料', '2026-09-13', '官方直采')))
        from agent.middleware import _resort_intro
        for source in (
            '[E101] (A 官方｜测试来源｜2026-09-13｜🚫 示例数据)\n北大湖位于吉林市。',
            '[E101] (A 官方｜测试来源｜2026-09-13｜✅ 已核对（官方直采）)\n太舞位于崇礼。',
            '[E101] (A 官方｜测试来源｜2026-09-13)\n北大湖位于吉林市。',
        ):
            check('介绍不引用样例、异场或未核验位置',
                  not _resort_intro([HumanMessage(content='北大湖')], [source]))
        check('具体问题不被基本介绍覆盖',
              not _resort_intro([HumanMessage(content='北大湖票价呢')], [tools[0]]) if tools else False)

        calls.clear()
        result = await run_agent([AIMessage(content='你想了解北大湖的哪方面？')], '北大湖', tools=[search_resort_kb])
        check('短反问不打回', result['messages'][-1].content == '你想了解北大湖的哪方面？')
        result = await run_agent([AIMessage(content='你好！')], '你好', tools=[search_resort_kb])
        check('闲聊不触发补查', result['messages'][-1].content == '你好！' and not calls)
        check('原因文案稳定', '没有查任何资料' in NO_TOOL_FOR_RESORT)
    finally:
        settings.CITATION_RETRY_ON_FAIL = original


async def run_lookup_recovery_suite() -> tuple[int, int]:
    """供无数据库环境单独验证补查路径，复用同一组断言。"""
    before = PASSED, FAILED
    await test_no_tool_for_covered_resort()
    return PASSED - before[0], FAILED - before[1]


async def test_resort_arg_graph():
    result = await run_agent([
        tool_call('get_resort_price', {'resort': 'dahulushui'}),
        AIMessage(content='本库已收录北大湖，但暂无价格资料。'),
    ], '北大湖票价')
    tools = [m for m in result['messages'] if getattr(m, 'type', '') == 'tool']
    check('真图纠正参数后工具确认已收录', any('本库已收录' in str(m.content) and '暂无价格' in str(m.content) for m in tools))
    result = await run_agent([
        tool_call('get_resort_price', {'resort': 'wanlong'}),
        AIMessage(content='工具参数不一致，暂无可靠来源。'),
    ], '北大湖票价')
    tools = [m for m in result['messages'] if getattr(m, 'type', '') == 'tool']
    check('真图已知错场被拒且不返回价格', any('未查询' in str(m.content) for m in tools) and all('[E' not in str(m.content) for m in tools))


def test_multi_resort_evidence_blocks():
    from langchain_core.messages import HumanMessage
    from agent.middleware import _query_evidence_block
    from rag.parser import ResortRegistry
    def block(resort, question, name='get_resort_runs'):
        return _query_evidence_block({'name': name, 'args': {'resort': resort}},
                                     {'messages': [HumanMessage(content=question)]})
    ids = sorted(ResortRegistry.list_resort_ids())
    expected = lambda id_: 200 + 10000 + (ids.index(id_) + 1) * 1000
    check('多场万龙确定性子段', block('wanlong', '分别查万龙和太舞雪道') == expected('wanlong'))
    check('多场太舞确定性子段', block('太舞', '分别查万龙和太舞雪道') == expected('taiwu'))
    check('多场请求顺序不改子段', block('万龙', '太舞和万龙分别查询') == expected('wanlong'))
    check('单场保持旧段', block('wanlong', '万龙有几条雪道') == 200)
    check('同场别名重复保持旧段', block('wanlong', '万龙和万龙滑雪场有几条雪道') == 200)
    check('禁止借场不触发多场段', block('wanlong', '万龙雪道，不要拿太舞的资料补') == 200)
    check('known加unknown已知场分段', block('wanlong', '万龙与二世谷分别查雪道') == expected('wanlong'))
    check('未知场参数不编子段', block('二世谷', '万龙与二世谷分别查雪道') == 200)
    check('外部工具段不改', block('wanlong', '万龙太舞', 'get_weather') == 400)


async def test_multi_resort_evidence_graph():
    from agent.middleware import _query_evidence_block, _evidence_ids
    from langchain_core.messages import HumanMessage
    question = '分别查询万龙和太舞的雪道数，不要混在一起'
    state = {'messages': [HumanMessage(content=question)]}
    wb = _query_evidence_block({'name': 'get_resort_runs', 'args': {'resort': 'wanlong'}}, state)
    tb = _query_evidence_block({'name': 'get_resort_runs', 'args': {'resort': 'taiwu'}}, state)
    batch = AIMessage(content='', tool_calls=[
        {'name': 'get_resort_runs', 'args': {'resort': 'wanlong'}, 'id': 'runs-w', 'type': 'tool_call'},
        {'name': 'get_resort_runs', 'args': {'resort': 'taiwu'}, 'id': 'runs-t', 'type': 'tool_call'},
        {'name': 'get_resort_price', 'args': {'resort': 'taiwu'}, 'id': 'price-t', 'type': 'tool_call'},
    ])
    result = await run_agent([batch, AIMessage(content=f'两场资料分别见[E{wb+1}][E{tb+1}]。')], question)
    messages = [m for m in result['messages'] if getattr(m, 'type', '') == 'tool']
    by_call = {m.tool_call_id: str(m.content) for m in messages}
    check('同批两场runs头编号不同', f'[E{wb+1}]' in by_call['runs-w'] and f'[E{tb+1}]' in by_call['runs-t'] and wb != tb)
    wids = _evidence_ids([next(m for m in messages if m.tool_call_id == 'runs-w')])
    tids = _evidence_ids([next(m for m in messages if m.tool_call_id == 'runs-t')])
    check('同批两场全部编号不重叠', not wids.intersection(tids))
    check('价格空表不编来源编号', '[E' not in by_call['price-t'])
    check('多场合法引用被放行', DEGRADE_REPLY not in str(result['messages'][-1].content))
    single = await run_agent([
        tool_call('get_resort_runs', {'resort': 'wanlong'}, 'single-w'),
        AIMessage(content='万龙资料见[E201]。'),
    ], '万龙雪道数量')
    single_tool = next(m for m in single['messages'] if getattr(m, 'type', '') == 'tool')
    check('单场真图仍用旧E201段', str(single_tool.content).startswith('[E201]'))


async def run_suite() -> tuple[int, int]:
    """跑完全部断言，返回 (通过数, 失败数)。

    给 CLI 和 pytest 共用（tests/test_offline_suites.py）：这里**不 sys.exit**，
    否则 pytest 只会看到 SystemExit，而不是一条能定位的失败用例。
    """
    from utils.console import enable_utf8_console
    enable_utf8_console()      # pytest 直接调这里，不经过 main()——控制台编码要自己设
    test_shift()
    test_multi_resort_evidence_blocks()
    await test_multi_resort_evidence_graph()
    test_resort_arg_guard()
    await test_resort_arg_graph()
    test_board_arg_guard()
    await test_board_arg_graph()
    test_board_answer_check()
    await test_board_answer_graph()
    test_level_count_objects()
    await test_no_tool_for_covered_resort()
    test_quantity_objects()
    await test_quantity_object_graph()
    test_missing_only_retry()
    await test_missing_only_graph()
    await test_fabricated_number()
    await test_bad_citation()
    await test_degrade()
    await test_happy_path()
    await test_markdown_citations_keep_fact_and_id_guards()
    await test_no_evidence_guard()
    await test_no_tool_guard()
    test_derive_rules()
    test_stack()
    await test_trim()
    await test_cross_turn_citation()
    return PASSED, FAILED


def db_ready() -> tuple[bool, str]:
    """这一套需要 MySQL：假模型只负责"说错话"，工具是真的会查库。

    返回 (是否可用, 原因)。库不可用时 CLI 直接给出人话提示并退出 2——
    而不是让 OperationalError 的堆栈糊在屏幕上，看着像项目坏了。
    """
    try:
        from sqlalchemy import create_engine, text

        from config.config import settings
        if not settings.DATABASE_URL:
            return False, 'DATABASE_URL 未配置（检查 ski-backend/.env）'
        with create_engine(settings.DATABASE_URL).connect() as conn:
            conn.execute(text('SELECT 1'))
        return True, ''
    except Exception as exc:                                    # noqa: BLE001
        return False, f'{type(exc).__name__}: {str(exc)[:140]}'


async def main() -> None:
    print('=' * 64)
    print('引用校验中间件 · 离线验证（假模型驱动，不需要 API key；但需要 MySQL）')
    print('=' * 64)
    ok, why = db_ready()
    if not ok:
        print(f'❌ 无法连接数据库：{why}')
        print('   这一套会通过真工具查库（价格/雪道走 SQL），所以需要 MySQL。')
        print('   先跑 python -m scripts.check_env 确认数据库可用，再重试。')
        sys.exit(2)
    _, failed = await run_suite()
    print()
    print('=' * 64)
    print(f'通过 {PASSED} 项，失败 {FAILED} 项')
    print('=' * 64)
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    from utils.console import enable_utf8_console
    enable_utf8_console()      # 断言结果里有 ✅/❌
    asyncio.run(main())
