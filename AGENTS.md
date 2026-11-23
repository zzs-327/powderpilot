# AGENTS.md

Guidance for AI coding tools and contributors working in this repository.

## Project Overview

PowderPilot（滑雪情报官）是面向滑雪者的可溯源垂直 AI Agent。它将人工策展的雪场 YAML 档案同步到 MySQL 与 Chroma，使用混合检索、证据校验和 LangChain/LangGraph Agent 提供雪场问答、分层推荐与行程辅助。当前以课程/演示型项目为主，部分雪场数据明确标注为 sample，不能直接当作实时商业数据。

## Tech Stack

- 后端：Python、FastAPI、SQLAlchemy 2、MySQL、JWT
- Agent：LangChain create_agent、LangGraph、OpenAI 兼容模型（智谱/DeepSeek 可配置）
- RAG：YAML/文本解析、中文分块、Ollama embedding、Chroma、BM25 + RRF
- 前端：uni-app / Vue，H5，SSE 流式聊天
- 数据源：ski-backend/data/resorts/*.yaml 是策展数据单一真相源

## Commands

在仓库根目录执行；Windows 推荐使用已验证的课程环境解释器：

```powershell
cd ski-backend
<你的 Python 环境>/python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 填 DATABASE_URL / SECRET_KEY / 对应模型 API key
<你的 Python 环境>/python.exe init_db.py
<你的 Python 环境>/python.exe -m scripts.check_env
<你的 Python 环境>/python.exe -m scripts.sync_to_mysql
<你的 Python 环境>/python.exe -m rag.ingest --dry-run
<你的 Python 环境>/python.exe -m rag.ingest --rebuild
<你的 Python 环境>/python.exe -m scripts.evaluate
<你的 Python 环境>/python.exe -m pytest
<你的 Python 环境>/python.exe main.py
# API docs: http://127.0.0.1:8000/docs
# health:    http://127.0.0.1:8000/
```

不使用该环境时可将 `<你的 Python 环境>/python.exe` 替换为当前虚拟环境的 `python`。RAG 真正入库前需要启动 Ollama，并准备 `shaw/dmeta-embedding-zh:latest`。前端目录为 `ski-backend/ski-front`，使用 HBuilderX/uni-app 运行。

## Directory Layout

- main.py：FastAPI 入口、生命周期、异常处理、CORS、路由注册
- api/：HTTP 路由层，应保持薄，只做参数/鉴权/调用 service
- service/：业务编排与领域规则
- dao/：数据库访问，不放业务规则
- models/：SQLAlchemy 表模型
- schemas/：Pydantic 请求/响应模型与统一 Result
- config/：Settings、数据库、依赖和异常
- utils/：JWT、密码、日志、控制台编码、本机服务 HTTP（无状态工具）
- rag/：解析、分块、embedding、入库、检索
- agent/：Agent 装配、工具、状态、证据与中间件
- data/resorts/：雪场 YAML 档案
- data/eval/：golden set 与评估报告
- scripts/：环境检查、同步、评估、压测、CLI、离线验证
- tests/：pytest 用例（接口契约 / 收录范围守卫 / 三个离线套件）
- ski-front/：uni-app 前端页面、API 与流式聊天

## Conventions

1. 新功能按 models → schemas → dao → service → api → main 的分层顺序实现；路由不要直接写 SQL 或复杂业务。
2. 接口遵循现有统一响应结构 code/msg/data（字段名就是 `msg`，不是 `message`）；新增接口同时补充鉴权、错误码和 Swagger 描述。
3. 注释与文档以中文为主，保持现有风格；优先小步修改，不做无关的大重构。
4. 改动雪场资料只改 YAML，然后分别运行 sync_to_mysql 与 rag.ingest；不要手改运行期数据库或 Chroma。
5. embedding 模型、分块参数和 Chroma collection 变更后，必须重建向量库并重新跑评估。
6. 检索回答必须保留来源、日期、来源级别和过期信息；不确定或未收录内容应拒答，不要用相邻雪场数据补答。
7. 运行期产物（.env、chroma/、*.db、logs/、前端构建目录）不得提交。
8. 不在代码、日志、提交信息或文档中输出 API key、数据库密码、JWT secret；读取配置统一走 Settings。

## Testing and Evaluation

- 先运行 python -m rag.ingest --dry-run 检查解析和 chunk 数，再做真实入库。
- 检索改动至少运行 python -m scripts.evaluate；关注 hit@1、hit@3、hit@5、MRR、拒答正确率和分层命中率。基线数字只认 data/eval/last_report.md，README 不再各写一套。
- 新增雪场时同步增加 golden set 的覆盖题、跨雪场隔离题和拒答题，并检查拒答题是否因收录范围变化而失效（evaluate 自带这项体检）。
- 改完代码先跑 python -m pytest：它包含接口契约、收录范围守卫、行程卡字段校验，以及可信度分级 / 引用校验 / 流式协议三个离线套件。其中**中间件套件需要 MySQL**（假模型驱动真 Agent 图，工具真查库）：没库时 pytest 跳过该用例，CLI（python -m scripts.test_middleware）明确报错并退出 2。CI 里因此起了 MySQL service。
- 三个离线套件的断言逻辑只维护在 scripts/test_*.py；写新用例请调用它们的 run_suite()（返回 (通过, 失败)，不 sys.exit），不要另抄一份。
- 结构化行程卡是**独立出口**，不经过消息中间件：改 TripCard 字段时同步更新 agent/card_check.py 与 tests/test_card_check.py，别让卡成为"没校验的旁路"。
- API 改动应补充 FastAPI/TestClient 测试；流式接口要覆盖正常结束、断开/取消和引用校验打回。

## Known Pitfalls

- 系统代理会劫持本地调用：Windows 上 requests 与 urllib 都会读注册表里的 WinINET 代理，把 localhost:11434 也送去代理，症状是"Ollama 连不上 / 嵌入模型调用失败"。本机服务一律走 utils/net.py 的 http_session / url_opener。
- Windows 控制台默认 GBK：脚本打印 ✅/⚠️/❌ 会 UnicodeEncodeError 崩在半路。脚本入口调用 utils/console.py 的 enable_utf8_console()（同时切代码页与 stdout）。
- Ollama 的 llama-server 会偶发卡死，表现为 rag.ingest 挂起无输出；重启 Ollama 即恢复，别急着改代码。
- 会话记忆库 checkpoints.db 是运行期产物（已 gitignore）；对话串味时先确认 thread_id 是否每轮唯一。

## Security and Operations

- allow_origins=[*] 与 DEBUG=True 只适合本地开发；部署前必须改成白名单、关闭调试，并用真实 HTTP 状态码/统一错误体。
- 当前启动生命周期会尝试建表，数据库不可用时仍允许服务启动；生产环境应改为显式健康检查和迁移流程。
- 当前项目依赖本机 Ollama、MySQL 和本地 Chroma；如要部署或多人协作，优先补 Docker Compose、迁移工具和备份策略。

## AI Collaboration Notes

- **入口文档只有这几份**：根 `README.md`（含「文档地图」）、`AGENTS.md`、`ROADMAP.md`、
  `CONTRIBUTING.md`、`GOVERNANCE.md`、`SECURITY.md`、`CHANGELOG.md`、`ski-backend/README.md`、
  `docs/TROUBLESHOOTING.md`。不要去找"项目计划书 / 评审建议 / 复盘 / 差异对照"——
  它们已归档到 `private/archive/`，**是历史材料，不是当前依据**。
- `private/` 目录（交接说明、归档文档、排障产物、AI 记忆）**公开侧永不导出、默认不读**；
  只有用户明确要求查历史时才进去。
- 代码注释里"见踩坑记录 #N"指的是 `docs/TROUBLESHOOTING.md` 的对应条目。
- 不要猜测未读文件的实现细节；先定位并读取相关模块。
- 任何涉及 Agent、RAG、引用或拒答逻辑的修改，都要说明评估结果变化。
- 修改前端时同时核对后端路由前缀 /api/v1、token 字段和 SSE 事件协议。
- 若需求与“可溯源”（每条数据带来源与可信度标注）原则冲突，先向用户确认。
- 发布前跑 `python -m scripts.pre_open_source_check`；导出公开快照用
  `python -m scripts.export_public`（**不要把这个私有仓库整体转公开**）。
