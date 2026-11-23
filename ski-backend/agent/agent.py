"""主智能体装配工厂。

装配清单：
    model           智谱 OpenAI 兼容接口（.env 里 LLM_PROVIDER 可切 DeepSeek）
    tools           6 个工具（见 agent/tools.py）
    state_schema    TripState —— 行程状态机
    context_schema  UserContext —— 请求级用户身份（工具里读 uid）
    checkpointer    SqliteSaver —— 短期记忆，同 thread_id 可续聊
    system_prompt   PromptBuilder.build(profile) —— 画像注入 + 溯源约束
    response_format TripCard —— 结构化行程卡（可用 .env 的 USE_STRUCTURED_OUTPUT 关闭）

TODO(可选)：middleware=[证据信封 / 引用校验 / 裁剪]
"""
import re
from typing import Any

from config.config import settings


class AgentFactory:
    """主 Agent 装配工厂"""

    @staticmethod
    def resolve_model() -> tuple[str, str, str]:
        """按 .env 的 LLM_PROVIDER 返回 (api_key, base_url, model_name)

        三个 provider（2026-10-04 加第三个）：
            zhipu（默认）  智谱，glm-4-flash 免费额度够开发用
            deepseek       备用；注意 key 失效时会 401，换之前先实测一次
            openai         任何 OpenAI 兼容服务（阿里云百炼 / 硅基流动 …），
                           用 LLM_API_KEY + LLM_BASE_URL + LLM_MODEL
            为什么要 generic 的那个：换供应商不该改代码——实测踩过
            "有 key 但没额度/已失效"，能一个环境变量换掉最省事。
        """
        provider = (settings.LLM_PROVIDER or 'zhipu').lower()
        if provider == 'deepseek':
            return (settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_BASE_URL,
                    settings.DEEPSEEK_MODEL)
        if provider in ('openai', 'openai_compatible', 'compatible'):
            return (settings.LLM_API_KEY, settings.LLM_BASE_URL, settings.LLM_MODEL)
        return settings.ZHIPU_API_KEY, settings.LLM_BASE_URL, settings.LLM_MODEL

    @staticmethod
    def build_model(temperature: float | None = None):
        """按 .env 装配对话模型（Agent 与兜底出卡共用，别各建一份）

        为什么单独抽出来：兜底出卡原来自己又 new 了一个 ChatOpenAI，
        于是"换模型要改的地方"变成了两处——加一个参数就漏一个。
        """
        # 懒加载 langchain 依赖，避免影响无关模块导入
        from langchain_openai import ChatOpenAI

        api_key, base_url, model_name = AgentFactory.resolve_model()
        if not api_key:
            raise RuntimeError(
                f'缺少 API key：当前 provider={settings.LLM_PROVIDER}，'
                '请在 ski-backend/.env 里填写对应 key（参考 .env.example）。')

        extra: dict[str, Any] = {}
        # 思考模式按**模型家族**分别判断（2026-10-05 修）：
        #   GLM-4.5 / 4.6：接受 disabled / low / high / max → 填 disabled 关掉
        #   GLM-5.x（如 5.3-flash）：**不接受 disabled**，发了就 400（要求 low/high/max），
        #       所以只在值是 low/high/max 时才发；填 disabled 视为"不指定"，
        #       让它走默认思考（能用，但慢——实测均值 28s vs 4.5-air 的 6s）
        # 原来的判断只认 glm-4\.[56]，于是 config.py 里那句"换 glm-5.3-flash 记得改
        # LLM_THINKING=low"是**空话**——那个值根本传不到模型。见 docs/TROUBLESHOOTING.md。
        thinking = settings.LLM_THINKING
        if thinking and re.match(r'glm-4\.[56]', model_name):
            # 关掉"思考模式"（GLM-4.5 / 4.6 系列默认先想再答）
            #
            # 为什么必须显式关（2026-09-13 实测）：
            #   同一个问题，开思考 2.0 秒只吐思考过程（content 为空、120 个 token
            #   全花在 reasoning 上），关掉 0.6 秒直接给答案。
            #   而 Agent 一轮要调好几次模型——实测 glm-4.5-flash 开着思考时
            #   **6 分钟还在反复调同一个工具**，看着像模型不会用工具，
            #   其实是每步的思考把时间和 token 都吃光了。
            #   注意：这些模型的 max_tokens 是"思考 + 回答"共用的，
            #   小额度下开思考会直接得到空回答。
            extra['extra_body'] = {'thinking': {'type': thinking}}
        elif thinking in ('low', 'high', 'max') and re.match(r'glm-5', model_name):
            extra['extra_body'] = {'thinking': {'type': thinking}}

        return ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=(settings.LLM_TEMPERATURE if temperature is None
                         else temperature),
            # 显式超时（2026-10-05 加）：不设时 openai SDK 的默认**读超时是 600 秒**，
            # 供应商一旦卡住，单次调用最长能挂 10 分钟；叠加 ModelRetryMiddleware 的重试，
            # 一轮对话就变成十几分钟（实测出现过一次 17 分钟的"排行程"）。
            timeout=settings.LLM_TIMEOUT_SECONDS,
            # max_retries=0：把重试**只留在一处**（ModelRetryMiddleware，带 jitter 与退避）。
            # 否则 SDK 自带 2 次 + 中间件 2 次 = 最多 9 次尝试，超时场景下就是 9×60 秒。
            max_retries=0,
            **extra,
        )

    @staticmethod
    def create(profile: dict[str, Any] | None = None, checkpointer=None):
        """装配主 Agent。每次请求按当前用户画像构建（画像不同，提示词不同）

        :param profile: 用户画像，如 {"level": "新手", "board_type": "单板"}
        :param checkpointer: 会话记忆（service/agent_service.py 里统一管理生命周期）
        """
        # 懒加载 langchain 依赖，避免影响无关模块导入
        from langchain.agents import create_agent

        from agent.prompts import PromptBuilder
        from agent.state import TripState, UserContext
        from agent.tools import TOOLS
        from schemas.trip import TripCard

        # 模型装配统一走 build_model（含"关掉思考模式"等 provider 相关处理）
        model = AgentFactory.build_model()

        extra = {}
        # 卡片来源决定要不要给模型"结构化输出"能力：
        #   CARD_MODE=data（默认）：卡片由数据渲染（见 agent/card_builder.py），
        #       **不给 ToolStrategy**——模型不再尝试自己写卡，也就没有了
        #       "生成卡 → 字段校验 → 喂回重写"这个循环（实测它能在弱模型上烧完 40 步）。
        #   CARD_MODE=model：保留老路径，用于对照实验。
        if settings.USE_STRUCTURED_OUTPUT and settings.CARD_MODE != 'data':
            # 显式用工具策略（课程 day30 写法）：GLM 对供应商策略的 JSON Schema 支持不稳定，
            # 而工具调用是它可靠的能力
            from langchain.agents.structured_output import ToolStrategy

            extra['response_format'] = ToolStrategy(TripCard)

        # 中间件栈（官方件 + 自研件，见 agent/middleware.py 的 MiddlewareFactory）
        # 可用 .env 的 USE_MIDDLEWARE 关掉，便于对比"有护栏 vs 没护栏"的差异
        if settings.USE_MIDDLEWARE:
            from agent.middleware import MiddlewareFactory

            extra['middleware'] = MiddlewareFactory.build(model)

        return create_agent(
            model=model,
            tools=TOOLS,
            state_schema=TripState,
            context_schema=UserContext,
            checkpointer=checkpointer,
            system_prompt=PromptBuilder.build(profile),
            **extra,
        )


# ---------------- 兼容旧调用 ----------------
build_agent = AgentFactory.create