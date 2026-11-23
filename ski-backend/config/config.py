"""项目配置：统一从 .env 读取，禁止在代码里写死密钥。

对应计划书 v0.4 第 3.1 节。原 his-backend 只有 DATABASE_URL / SECRET_KEY，
这里补齐 Agent 与 RAG 所需配置。
"""
from pathlib import Path

from pydantic_settings import BaseSettings

# 项目根目录（ski-backend/），用于把相对路径固定住，避免受启动目录影响
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # ---------------- 基础服务 ----------------
    DATABASE_URL: str = ''
    SECRET_KEY: str = ''
    APP_HOST: str = '127.0.0.1'
    APP_PORT: int = 8000
    DEBUG: bool = True

    # CORS 白名单（逗号分隔）。'*' 只适合本地开发，部署时必须填前端域名。
    #
    # ⚠️ 为什么不能一直用 '*'：中间件是 allow_credentials=True，
    #    而浏览器在带 cookie 的请求里**不接受** Access-Control-Allow-Origin: *，
    #    结果是"看起来配了 CORS，但带凭证的请求就是失败"，排查方向很容易跑偏。
    #    所以部署时显式列出来，例如：ALLOW_ORIGINS=https://ski.example.com
    ALLOW_ORIGINS: str = '*'

    # ---------------- 大模型 ----------------
    # LLM_PROVIDER 决定用哪家：zhipu（默认）或 deepseek（备用）
    LLM_PROVIDER: str = 'zhipu'
    ZHIPU_API_KEY: str = ''
    DEEPSEEK_API_KEY: str = ''
    DEEPSEEK_MODEL: str = 'deepseek-chat'
    DEEPSEEK_BASE_URL: str = 'https://api.deepseek.com/v1'
    # ⚠️ 2026-10-03 实测：glm-5.3-flash **始终思考、不支持关闭**，与下面的
    # LLM_THINKING=disabled 组合会每次调用都 400（"请使用 low、high 或 max"）。
    # 所以默认值改成能直接跑通的 glm-4.5-air；要换 glm-5.3-flash 记得同时改 LLM_THINKING=low。
    LLM_MODEL: str = 'glm-4.5-air'
    LLM_BASE_URL: str = 'https://open.bigmodel.cn/api/paas/v4/'
    LLM_TEMPERATURE: float = 0.3

    # 思考模式开关（GLM-4.5 / 4.6 系列默认"先想再答"）
    #
    # ⚠️ 开着它，这个 Agent 会变得又慢又"笨"（2026-09-13 实测）：
    #   同一个问题，开思考 2.0 秒只吐思考过程（content 为空，120 个 token 全花在
    #   reasoning 上），关掉 0.6 秒直接给答案；而一轮对话要调好几次模型，
    #   glm-4.5-flash 开着思考时**6 分钟还在反复调同一个工具**，
    #   看着像"模型不会用工具"，其实是每步的思考把时间和 token 都吃光了。
    #   还有个坑：这些模型的 max_tokens 是"思考 + 回答"共用的，额度给小了会得到空回答。
    # '' = 不发这个参数（glm-4-flash 这类模型不认识它）；
    # 'auto' = 交回模型自己决定（想拿它做对照实验时用）。
    LLM_THINKING: str = 'disabled'

    # 通用 OpenAI 兼容 provider（LLM_PROVIDER=openai 时用这三个）
    # 为什么留这一手：换模型供应商（阿里云百炼 / 硅基流动 / 其他）不该改代码。
    # 例：LLM_PROVIDER=openai / LLM_BASE_URL=https://api.siliconflow.cn/v1
    #     LLM_MODEL=Qwen/Qwen3-8B / LLM_API_KEY=sk-xxx
    LLM_API_KEY: str = ''

    # ---------------- RAG（嵌入 + 分块，命名对齐课程工程） ----------------
    OLLAMA_BASE_URL: str = 'http://localhost:11434'
    OLLAMA_EMBED_MODEL: str = 'shaw/dmeta-embedding-zh:latest'
    # 嵌入的分批与重试（2026-09-13 加）：
    #   原来一次性把 500+ 条丢给 Ollama，runner 中途猝死就整批失败，
    #   还可能在向量库里留下残缺数据（表现为"库里有数据、检索永远报错"）。
    #   分批后单批失败只影响 32 条，重试用指数退避（runner 重启通常几秒）。
    EMBED_BATCH_SIZE: int = 32
    EMBED_MAX_RETRIES: int = 3

    # 嵌入服务可切换（2026-10-03 加，为部署到小内存服务器）：
    #   ollama  —— 本地 Ollama（默认，零成本，开发期用它）
    #   openai  —— 任何 OpenAI 兼容的 /embeddings（托管服务）
    # 为什么需要：Ollama 在服务器上要占 1~2GB 内存 + 全部 CPU，而查询时每条问题都要
    # 嵌入一次；换成托管服务后只需改环境变量，不再依赖 Ollama。
    #
    # ⚠️ 换嵌入模型 = 维度与语义空间都变：**必须重建向量库并重跑评估**（AGENTS.md 约定 5），
    #    维度自检（rag/embedding.py 的 _check）会把旧库当场拦下来。
    EMBED_PROVIDER: str = 'ollama'
    EMBED_API_KEY: str = ''
    EMBED_BASE_URL: str = ''    # 例：https://dashscope.aliyuncs.com/compatible-mode/v1
    EMBED_MODEL: str = ''       # 例：text-embedding-v4 / BAAI/bge-m3

    RAG_CHUNK_SIZE: int = 400     # 中文分块大小（300~500 之间）
    RAG_CHUNK_OVERLAP: int = 80   # 重叠约 1/5，避免断句丢语义

    # ---------------- 向量库（Chroma 本地持久化） ----------------
    CHROMA_DIR: str = str(BASE_DIR / 'chroma')
    CHROMA_COLLECTION: str = 'ski_kb'
    RETRIEVE_TOP_K: int = 10   # 单路召回条数（向量 / BM25 各取 top10）
    RRF_K: int = 60            # RRF 融合参数
    RERANK_TOP_N: int = 5      # 融合后进入 prompt 的证据条数
    # 拒答阈值：向量 L2 距离超过它就丢弃该条证据。
    # 实测标定（万龙 28 条语料）：库内问题 top1 距离 0.37~0.76，
    # 库外问题（如"二世谷雪票多少钱"）≥ 0.90，完全无关问题 > 1.2。
    # 取 0.85 作为分界——宁可说"暂无可靠来源"，也不要把万龙的价格答成二世谷的。
    MAX_DISTANCE: float = 0.85

    # ---------------- 日志 ----------------
    # 诊断信息（尤其是引用校验的打回原因）必须落盘：
    # 服务往往起在某个终端或 IDE 里，stdout 没人接，出了问题只能靠猜。
    LOG_DIR: str = str(BASE_DIR / 'logs')

    # ---------------- 数据目录（单一真相源） ----------------
    DATA_DIR: str = str(BASE_DIR / 'data')
    RESORT_DIR: str = str(BASE_DIR / 'data' / 'resorts')
    PHOTO_DIR: str = str(BASE_DIR / 'data' / 'photos')

    # ---------------- 输出形态 ----------------
    # 打开后 Agent 会额外产出结构化行程卡（schemas/trip.py）。
    # 若模型对复杂结构支持不好，把它设为 False 可退回纯文本，不影响主链路。
    USE_STRUCTURED_OUTPUT: bool = True

    # 行程卡从哪来（2026-10-04 加）：
    #   data （默认）——**从数据渲染**：预算取 MySQL 价格行、来源取证据信封、
    #                   行程是模板骨架、装备查规则表；模型只写正文回答。
    #                   收益：出卡 0 次额外模型调用，也没有"生成→校验→重写"的循环
    #                   （实测弱模型上这个循环能烧完 40 步 recursion_limit）。
    #   model        —— 老路径：模型用 ToolStrategy 生成卡片，再过字段校验。
    #                   保留它是为了对照实验（"数据渲染 vs 模型生成"差在哪）。
    CARD_MODE: str = 'data'

    # ---------------- 中间件（Agent 护栏） ----------------
    # 打开后启用整条中间件栈：
    #   官方件：ToolRetryMiddleware（工具失败重试）+ SummarizationMiddleware（超长摘要）
    #   自研件：证据编号全轮唯一 + 引用校验 + 历史裁剪兜底
    # 关掉它便于做对照实验（同一条问题，有护栏 vs 没护栏，答案差在哪）。
    USE_MIDDLEWARE: bool = True

    # ---------------- Agent 单轮上限（防"不收敛"） ----------------
    # 实测（2026-10-04）：成本测算脚本跑"排行程"那题时**反复调用模型不收敛**，
    # 一轮能烧好几分钟——根因就是缺这几个上限。run_limit 是"单轮"，不是整个会话：
    # 会话级的 thread_limit 会把长对话里的正常追问也掐掉，这里刻意不用。
    # 一轮最多几次模型调用。**0 = 不挂 ModelCallLimitMiddleware**（默认）。
    # ⚠️ 2026-10-04 实测：它与自研中间件（引用校验的打回重写路径）组合时会让模型节点
    #    空转到 recursion_limit（可复现的三组对照见 agent/middleware.py 注释）。
    #    所以默认不挂；模型次数的兜底交给 AGENT_RECURSION_LIMIT + 超限降级。
    AGENT_MAX_MODEL_CALLS: int = 0
    AGENT_MAX_TOOL_CALLS: int = 12     # 一轮最多几次工具调用（超了提示模型用已有资料作答）
    # LangGraph 的 super-step 上限（默认 25，对本项目偏紧：工具 + 打回重写 + 合成）
    AGENT_RECURSION_LIMIT: int = 40

    # 引用校验的"回看窗口"：本轮有证据时，允许引用最近 N 轮出现过的证据编号。
    #
    # 为什么需要这个窗口（实测误伤）：
    #   白名单原来只认本轮证据，但模型的引用是跨轮连续的——追问「那价格呢？」时
    #   它会把上一轮的编号一起带进回答，于是被判"引用了不存在的编号"，
    #   重写仍带着它 → 降级成拒答。**最普通的追问路径被判死**。
    # 为什么不能设成"整段会话"：
    #   证据基数越大，数字巧合匹配的机会越多，事实校验越松；而且数据更新后，
    #   几十轮前的旧证据不该继续给新回答背书（记忆是有保质期的）。
    # 设为 0 = 关闭回看，退回"只认本轮"的严格模式（可用来做对照实验）。
    CITATION_LOOKBACK_TURNS: int = 3

    # ---------------- 会话记忆与上下文管理 ----------------
    CHECKPOINT_DB: str = str(BASE_DIR / 'checkpoints.db')

    # 上下文管理是**两级**的，别把这两个参数搞混：
    #   一级 摘要（官方 SummarizationMiddleware）：正常路径。
    #        对话变长时把旧消息摘要后保留，信息不丢。
    #   二级 裁剪（自研 trim_history）：兜底路径。
    #        只在一级失效（或摘要本身也撑不住）时才动手，代价是直接丢历史，
    #        所以阈值要设得远高于摘要阈值——它是保险丝，不是常规手段。
    #
    # SUMMARY_TRIGGER_TOKENS 是**实测标定**出来的，不是拍的：
    #   本项目系统提示词很长（含 7 条铁律 + 分层规则 + 工具路由 + 画像），
    #   实测光是一句"你好"就要 3669 total_tokens，第二轮带证据就 6762。
    #   最初按官方示例填 4000，结果是**第二句就开始摘要**，每轮白烧一次模型调用。
    #   取 12000：约等于 5-6 轮带完整证据的对话，既压得住成本，又不至于过早丢历史。
    #   注意这个值随系统提示词长度变化，改了 prompts.py 就该重新测一次。
    # 单次模型调用的超时（秒）。**必须有**：不设时用的是 openai SDK 的默认读超时
    # **600 秒（10 分钟）**，叠加 ModelRetryMiddleware 的重试，一次供应商卡顿就能把
    # 一轮对话拖到十几分钟——实测出现过一次 **17 分钟**的"排行程"（3 次里 1 次）。
    # 实测正常模型调用耗时 1.8~17.9 秒，所以 60 秒 = 观测上限的 3 倍，够宽松又兜得住。
    LLM_TIMEOUT_SECONDS: float = 60.0

    # 单个外部（MCP）工具的调用超时（秒）。实测必须有：外部工具慢/挂住时，
    # ToolRetryMiddleware 会按指数退避重试，时间和图步数一起被吃掉，
    # 最终烧穿 recursion_limit（用户侧="回答卡住"）。15 秒 ≈ 一个地图接口的合理上限。
    MCP_TIMEOUT_SECONDS: float = 15.0

    # 引用校验不过时：默认**直接降级**（说"暂无可靠来源"），不做"喂回模型重写"。
    # 依据（实测）：模型对比 20 轮里只有最弱的 glm-4-flash 触发过打回（2/4），
    # glm-4.5-air 以上一次都没触发 → 重写收益小、成本明确（多 1-2 次模型调用，
    # 而且"打回→回模型节点"正是与 ModelCallLimit 组合把图转死的那条路径）。
    # 设 True 可恢复旧行为，用于对照实验（量化"重写能救回多少回答"）。
    CITATION_RETRY_ON_FAIL: bool = False
    # 仅漏引用且数字检查通过时重答一次；其他不合格仍直接降级。
    # 2026-10-12 改为默认开启：便宜模型（glm-4-flash）北大湖实测 7 问里 3 问只是漏标编号，
    # 内容有据却被整条降级；重答只多一次调用，且只在"唯一问题是漏标"时触发。
    CITATION_RETRY_MISSING_ONLY: bool = True

    # ---------------- 上下文管理（2026-10-04 改为"官方优先"） ----------------
    # 官方 ContextEditingMiddleware + ClearToolUsesEdit：把**旧工具输出清空**
    # （保留消息结构，只把内容换成占位符）——比自研的"删消息"更安全：
    # 不会产生"孤儿工具消息"，也不需要那些边界处理。
    # 自研 trim_history 退居保险丝（默认不挂）：它删消息，风险更大，
    # 但消息条数无上限时仍是最后一道闸。
    USE_CONTEXT_EDITING: bool = True
    # 触发阈值（token）：设得远高于"一轮对话的真实规模"（系统提示词约 3.7k），
    # 否则会在正常多轮对话里就清空证据——而引用校验正需要那些证据，会互相打架。
    CONTEXT_EDIT_TRIGGER_TOKENS: int = 24000
    CONTEXT_EDIT_KEEP_TOOL_RESULTS: int = 3
    USE_TRIM_HISTORY: bool = False
    HISTORY_HARD_LIMIT: int = 40

    # 上下文摘要中间件开关（2026-10-04 加，默认关闭）
    #
    # 关它的动因是排查"多轮工具对话空转到 recursion_limit"（debug 统计：
    #     chain:model 进入 40 次，真正发往模型的调用只有 5 次）。
    # ⚠️ 但**关掉它之后仍然超限**——所以它不是根因，别再把它当罪魁。
    #     真正的相互作用还在定位中（已知：三组中间件各自单独跑都正常，全栈一起才炸）。
    # 保险丝现在是：HISTORY_HARD_LIMIT（硬裁剪）+ AGENT_RECURSION_LIMIT（步数上限）。
    # 要启用摘要：设 True，并把 SUMMARY_TRIGGER_TOKENS 提到明显高于真实上下文规模
    #     （本项目系统提示词约 3.7k token，摘要阈值 12000 在多轮工具对话里很容易被跨过）。
    USE_SUMMARY_MIDDLEWARE: bool = False
    SUMMARY_TRIGGER_TOKENS: int = 12000
    SUMMARY_KEEP_MESSAGES: int = 10      # 摘要后保留最近这么多条原文
    HISTORY_HARD_LIMIT: int = 40         # 消息数硬上限，超过才启用裁剪兜底

    # ---------------- 外部工具（可选加分项） ----------------
    BAIDU_MAP_AK: str = ''      # 百度地图 MCP / 路线 / POI
    QWEATHER_KEY: str = ''      # 和风天气（与地图 MCP 互为备份）

    # ---------------- MinIO（可选加分项） ----------------
    MINIO_ENDPOINT: str = '127.0.0.1:9000'
    MINIO_ACCESS_KEY: str = ''
    MINIO_SECRET_KEY: str = ''
    MINIO_BUCKET_PHOTOS: str = 'ski-photos'
    MINIO_PUBLIC_BASE_URL: str = 'http://127.0.0.1:9000'

    model_config = {
        # 用绝对路径，避免"在别的目录启动就读不到 .env"
        "env_file": str(BASE_DIR / '.env'),
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        # .env 里多写的键不影响启动
        "extra": "ignore",
    }


settings = Settings()
