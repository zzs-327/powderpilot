# 踩坑记录与设计决策（TROUBLESHOOTING）

> 这份文档是**公开版**：只保留现象、根因与处置，去掉了本机路径、账号与内部排障过程。
> 代码注释里凡写"见踩坑记录 #N"的，都指向这里。
>
> 为什么要公开这些：本项目要回答的是"**答案凭什么可信**"，而可信是靠一次次
> 把假象拆穿换来的。踩坑记录就是这套机制的来路。

## 一、环境与依赖

### Ollama 未启动时，聊天先出字后被引用校验拒答
- **现象**：位置、攻略问题先显示回答，随后出现「引用校验未通过」；相关链接卡仍可能展示。
- **根因**：相关链接来自策展档案，正文检索还依赖 Ollama。嵌入服务不可用时，卡片能读取不代表检索正常；模型凭记忆生成的位置也可能错误。
- **处置**：先运行 `python -m scripts.check_env`，确认 MySQL、Ollama、模型和 Chroma 均可用。使用自定义模型目录时，在启动 `ollama serve` 的同一终端设置 `OLLAMA_MODELS` 为已有模型目录；再跑 `python -m scripts.evaluate` 和真实聊天。
- **流式收尾**：最终引用校验拒答通过 `replace` 事件覆盖已流出的草稿，避免错误回答、重写残片和拒答拼在一起。不要关闭引用校验。
- **链接回答的格式**：模型可能把来源编号写成 `[来源](E101)`。先归一为 `[E101]` 再进行编号白名单和事实校验，流式与非流式出口使用相同格式；不存在的编号和编造数字仍会被拒绝。

### #9 Ollama 的 `llama-server` 会偶发卡死
- **现象**：`rag.ingest` 挂起、**无任何输出**；`ollama ps` 里没有已加载模型。
- **根因**：Ollama 的 runner 子进程卡死，父进程的 API 请求因此永久等待。
- **处置**：重启 Ollama 即恢复（别急着改代码）。判断依据看 Ollama 自己的日志目录。
- **顺带设计**：嵌入层自己实现了**分批 + 指数退避重试 + 维度自检**（见 `rag/embedding.py`），
  因为"一次 500 条全发给 embedding 服务"时 runner 中途猝死会让整批失败，
  还可能在向量库里留下残缺数据。

### #10 `import langchain_text_splitters` 在本机会挂死
- **现象**：单独 import 也 60 秒无输出，看起来像"入库无输出地卡住"。
- **处置**：分块逻辑不依赖它（`rag/chunker.py` 自己实现中文分块），
  代码里保留注释说明，避免后人为了"更标准"把它引回来。

### #22 YAML 生成的文档必须短于分块阈值
- **现象**：某些档案入库时卡住、无输出。
- **根因**：单条文档超过分块阈值时的处理路径会走到上面 #10 那条死路上。
- **处置**：控制单块文本长度；`rag.ingest --dry-run` 先体检 chunk 数再真入库。

### #12 测试 / 长任务要后台跑
- **现象**：首次嵌入要几十秒，前台跑会被超时掐断，**且看不到任何输出**。
- **处置**：长任务（ingest / evaluate / 压测）后台执行并落日志。

### 结果打完了、进程却不退出（CLI 被 checkpointer 的 aiosqlite 线程卡住，2026-10-05）
- **现象**：`python -m scripts.experiments.bench_models`、`python -m scripts.experiments.measure_cost` 把结果全打印完了，
  进程却一直不退出；在 `docker compose exec -T` 下表现为 `docker compose exec` **永不返回**
  （排查时白等了十几分钟，还以为是模型卡住）。
- **根因**：`service/agent_service.py` 的 `get_checkpointer()` 用 `aiosqlite.connect()` 建了长连接，
  它留着的线程让解释器无法收尾（`threading._shutdown` 会等非 daemon 线程）。
  零成本诊断（两级都不调模型）：只建 checkpointer → 打印成功后挂住（`timeout` 退 **124**）；
  只 `import aiosqlite` → 正常退 **0**。
- **处置**：CLI 外面套 `timeout 300 python -m scripts.x`；根治方向是 CLI 收尾时关掉那条连接。
  **服务端不受影响**（uvicorn 常驻，本来就不该退出），所以别照这个去动 Agent 主链路。

### 别把新版单文件 `docker cp` 进旧容器（2026-10-05）
- **现象**：`docker cp agent.py powderpilot-backend-1:/app/agent/agent.py` 之后，bench 4/4 全军覆没：
  `❌ AttributeError: 'Settings' object has no attribute 'LLM_TIMEOUT_SECONDS'`——
  拷进去的 `agent.py` 是新的（用了刚加的配置项），而容器里还是几天前构建的 `config.py`。
- **根因**：`docker cp` **只换了那一个文件**，容器整体代码与仓库并不一致；跨文件的新依赖
  （新的 Settings 字段、新的 import）会立刻炸，而且报错位置离真凶很远。
- **处置**：更新运行中的服务要用 `docker compose --env-file .env.docker up -d --build backend`，
  让容器代码 = 仓库代码（`up -d --build` 完顺手跑 `python -m pytest -q` 回归）。
  `docker cp` 只适合"临时验证某个自包含脚本"，别用来改应用代码。

### 系统代理会劫持"本机调用"（2026-10-03）
- **现象**：`Ollama 连不上`、`嵌入模型调用失败`，但 Ollama 其实活着。
- **根因**：Windows 上 `requests` 与 `urllib` 不止读环境变量，还会读注册表里的
  WinINET 代理设置，把 `localhost:11434` 也送去代理；代理不转发 localhost → 连接被重置。
- **处置**：本机服务统一走 `utils/net.py`（`http_session` / `url_opener`），
  只对 localhost 关闭代理继承，远程调用仍走系统代理。

### Windows 控制台默认 GBK，打印 emoji 会直接崩（2026-10-03）
- **现象**：脚本跑到一半 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'`，
  看着像"环境缺依赖"，其实是打印挂了。
- **处置**：脚本入口调 `utils/console.py` 的 `enable_utf8_console()`
  （同时切控制台代码页与 stdout，`errors='replace'` 兜底）。

### 依赖漏了 `langgraph-checkpoint-sqlite` / `aiosqlite`：本机全绿，容器里第一次对话就 500（2026-10-04）
- **现象**：`docker compose` 起好后 `/docs`、`/api/v1/user/login`、`/api/v1/resort/*` 全部正常，
  `check_env` 五段全绿，但 `POST /api/v1/chat` **立刻**返回 `{"code":500,"msg":"服务器内部错误"}`；
  容器日志末行是 `ModuleNotFoundError: No module named 'aiosqlite'`
  （`service/agent_service.py` 构造异步 checkpointer 处）。
- **根因**：`langgraph` 本体**不带** `langgraph.checkpoint.sqlite`（会话记忆的异步 SQLite
  checkpointer），它和 `aiosqlite` 是两个独立的包。本机的 conda 环境里早已装过
  （传递安装或手工装过），所以**在本机永远复现不了**，只有干净镜像才会暴露。
- **处置**：`requirements.txt` 显式补 `langgraph-checkpoint-sqlite>=2.0` + `aiosqlite>=0.20`；
  同时把 `langgraph.checkpoint.sqlite`、`aiosqlite` 加进 `scripts/check_env.py` 的 `REQUIRED`，
  让自检能在**部署阶段**拦住它，而不是等用户发第一条消息才 500。
- **教训**：`check_env` 只能证明"当前解释器能 import 什么"，**证明不了 Dockerfile 里装了什么**；
  凡"本机有、镜像可能没有"的隐式同族包，都要在 requirements 里写死。

## 二、向量库

### #24 "写进去读不出来"——根因不是数量，是 delete（2026-09-13 结案）
- **现象**：`rag.ingest` 报"入库完成 538 条"，但**任何新进程**查询都失败：
  `Error sending backfill request to compactor: ... Error loading hnsw index`。
- **根因**：触发需要两个条件同时成立——① 记录级 `delete(ids)`（它触发 compaction）；
  ② 记录数 ≥ 约 500（compactor 这时才决定建 HNSW 段）。而该版本的 chromadb
  在本机**写不出 HNSW 段的索引文件**（段目录里只有 `index_metadata.pickle`）。
  之前"449 条好、538 条坏"看起来像数量阈值，其实是巧合。
- **处置**：入库改成原生 `upsert`（同 id 覆盖，天然幂等，**完全不走 delete**），
  实测 700 条跨进程读正常。可复现：`scripts/probe_chroma.py --langchain ingest 600`（FAIL）
  / `--langchain upsert 700`（PASS）；`--langchain rebuild 600/900` 也已验证安全。
- **配套**：`scripts/check_env.py` 现在会做**向量库自检**（打开集合 → count → 真查一次），
  因为"库里有数据 ≠ 检索能用"这种状态原来的自检完全看不出来。
- **2026-10-07 用真实数据复验**：在 `chroma/` 的**副本**上（与生产 sqlite 逐字节一致）走一遍
  `ski_kb_quality_v2`（538 条 / 768 维）：改前 count + `get`（BM25 路径）+ 向量召回三路正常 →
  `delete_collection` + 重新 upsert 538 条 → **新进程**重新打开，三路依旧正常。
  同规模对照组（记录级 `delete(ids)` @538）精确复现 `Error loading hnsw index`。
  结论：**要清空集合就用 `--rebuild`，不要用记录级 delete**。
- **操作规则**：从 YAML 删掉一座雪场后，普通 `rag.ingest` 只做 upsert，
  **不会清理该雪场遗留的旧 chunk**，必须 `--rebuild` 才真的删干净。
- **遗留物**：本机库里还留着一个**打不开的老集合 `ski_kb`**，是结案前那次事故的残骸。
  当前运行不受影响（`.env` 把 `CHROMA_COLLECTION` 覆盖成 `ski_kb_quality_v2`），
  但 `config.py` 的默认值仍是 `ski_kb`——**任何没有这条 .env 覆盖的环境都会去开那个坏集合**。
  要清理就 `delete_collection('ski_kb')`（与 rebuild 同路径，安全），或在 v0.3 换库时一并丢弃。

## 三、Agent 与引用校验

### 点名已收录雪场却拒答：模型没有执行查询（2026-11-10）
- **现象**：只输入「北大湖」，模型凭记忆把位置说错；被提醒先查资料后，仍未调用工具，最终显示「来源校验未通过」。日志中的「未调用任何工具」能与真正的工具报错区分。
- **处置**：对于唯一明确的已收录雪场，引用校验发现漏查时直接让图的工具节点执行 `search_resort_kb`，移除失败草稿，再根据本轮证据重答；不再只依赖模型接受查询提醒。仅记录画像/行程不算取证。
- **边界**：补查沿用范围守卫识别别名、紧邻单场追问和显式换场；多场、未收录对象或歧义不猜测。每轮最多补查一次，查不到资料和编造数字/编号仍按原规则拒答。流式沿用 `reset` 清除失败草稿。
- **简短输入**：精确雪场名已有本轮位置证据时，逐字引用位置首句并保留来源、日期、可信度与过期提醒，随后澄清用户关心的方面；仍经编号/事实校验。样例、异场、未标可信度的证据不用于渲染介绍，流式通过 `replace` 覆盖模型草稿。
- **验证**：确定性断言维护在 `scripts/test_middleware.py`，无数据库时可经 `tests/test_offline_suites.py::test_lookup_recovery_suite` 单独运行；真实模型小题集为 `data/eval/e2e_lookup_recovery.yaml`，报告不替代完整回答评估基线。

### #8 白名单只认本轮 = 会误杀跨轮引用
- **现象**：追问「那价格呢？」时，模型把上一轮的证据编号一起带进回答，
  被判"引用了不存在的编号"，重写仍带着它 → 降级成拒答。**最普通的追问路径被判死。**
- **处置**：加"回看窗口"（`CITATION_LOOKBACK_TURNS`，默认 3 轮）。
  为什么不做成整段会话：证据基数越大，数字"巧合匹配"的机会越多，事实校验越松；
  而且数据更新后，几十轮前的旧证据不该给新回答背书——**记忆是有保质期的**。

### 事实校验的粒度：数值 + 单位配对，且允许有限推算
- 比对粒度是 **(数值, 单位)**，不是光比数值（否则"32 条雪道"会被"32 元"背书）。
- 同单位内允许加减乘（用户能自己算出来的），跨单位绝不合并。
- "条"不参与推算：雪道条数在证据里本来就写全了，允许推算只会放大巧合。
- 四舍五入要放行、单位同义写法要归一（`22℃` 与 `22 度`）。
  —— 这些规则每一条都是被真实误报逼出来的。

### GLM 的结构化输出必须指定 `method='function_calling'`
- **现象**：默认策略下模型返回 markdown 文本 → `Invalid JSON: expected value at line 1 column 1`。
- **处置**：主 Agent 用 `ToolStrategy(TripCard)`；兜底出卡用 `with_structured_output(..., method='function_calling')`。

### 结构化输出的副作用消息**不能当证据**（2026-10-03）
- **现象**：行程卡的字段校验漏掉了"教练课 300 元"这种无据费用。
- **根因**：卡片本身是通过工具调用产出的，那条 `Returning structured response: …`
  工具消息里就是卡的原文——把它算进证据，等于**自己给自己作证**。
- **处置**：校验时剔掉内部消息；证据窗口与引用校验一致（回看 N 轮），
  否则"出卡那一轮只有 `set_trip_info`"，任何来源编号都会被判不存在，等于永远出不了卡。

### #26 流式 + 后置校验的固有代价：被打回的那一版收不回来
- **现象**：校验在流末尾才发现引用不实，但那一版文字**已经流到前端了**。
- **处置**：发 `reset` 事件让前端清空气泡重写。
  这是"边流边校验"的必然冲突，不是 bug——写清楚，避免后人以为多发了个事件是多余的。

### stub 工具不能抛异常
- `get_weather` 曾直接抛 `NotImplementedError`，整轮被打断。
  正确做法：返回"该能力未接入"的提示，让模型自己决定怎么告诉用户。

## 四、前端

### #14 `uni.request` 默认超时只有 60 秒
- 对话要调工具、校验不过还要重写再调一遍模型 → 超 60 秒很常见。
- 处置：放宽超时；长链路走流式（SSE）。

### #25 `uni.request` 不是"不支持 SSE"，而是收不到分块
- 处置：H5 端改用 `fetch` 读流，自己解析 SSE 帧。

### #27 `uni-app` 的 `<text>` 会把 `\n` 渲染成 `<br>`
- 排查时被这条误导过：以为是后端多吐了换行。写文档留个记号。

### #18 / #20 前端白屏与"输入框不见了"
- 白屏根因：进页面时对画像对象取值，但该对象在部分路径下还没就绪。
- CSS 两个坑：`scroll-view` 只认**固定高度**（给 `max-height` 不滚动）；
  底部内容被 tabBar 压住要用 uni-app 注入的 CSS 变量让位：
  `height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px))`。

### #19 没有可视环境时怎么查前端
- 用无头浏览器把页面 DOM dump 出来，比"猜 CSS"有效得多。这招救过一次场。

## 五、工程习惯（这几条最值钱）

### #21 诊断信息只 `print` 到控制台 = 没留证据
- 服务往往起在某个终端或 IDE 里，stdout 没人接，出了问题只能靠猜。
- 处置：诊断信息（尤其是**引用校验的打回理由与被判不合格的回答原文**）同时落盘。

### #17 带 checkpointer 时 `result['messages']` 是整条会话历史
- 不是"本轮"。不过滤会把前几轮的工具调用一起打出来，看着像"这轮调了十几次工具"；
  证据提取也必须按"最后一条 human 之后"切分，否则历史证据会被当成本轮依据。

### #28 改完后端要确认"端口上到底是哪个进程在应答"
- 吃过半小时的亏：旧进程还占着端口，改的代码根本没生效。**先确认进程，再谈现象。**

### #15 改异步时连带着三处都得改
- 不是把 `def` 改成 `async def` 就完了：外部工具（MCP 只有协程）、
  会话记忆（同步 checkpointer 不支持 async 方法）、交互式 CLI
  （异步资源绑定事件循环，每轮 `asyncio.run` 会拿到上一轮的连接并报错）。
