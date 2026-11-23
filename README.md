# PowderPilot · 滑雪情报官

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://www.python.org/)
[![CI](https://github.com/zzs-327/powderpilot/actions/workflows/ci.yml/badge.svg)](https://github.com/zzs-327/powderpilot/actions/workflows/ci.yml)
[![GitHub](https://img.shields.io/badge/GitHub-zzs--327%2Fpowderpilot-181717.svg)](https://github.com/zzs-327/powderpilot)

> 帮滑雪者**选对雪场、算清总账、少花冤枉钱**的可溯源垂直 AI Agent：雪场情报 + 分层推荐 + 行程辅助。
>
> 核心命题不是“能不能回答滑雪问题”，而是**“答案凭什么可信”**。
>
> **性价比不是低价优先**：结合出发地、日期、水平、预算和偏好，说明费用包含项、额外开销及体验取舍；不预设便宜雪场更合适，也不把名气或高价等同于值得。

> 本仓库是 2026-11-23（Asia/Shanghai）从私有开发树导出的**公开源码快照**。导出时做了脱敏，并保守地去掉了版权未澄清的雪场全文档案。本页说明以本快照为准。公开版维护测试使用固定测试数据；依赖本地真实档案的用例会显示所需文件和跳过原因。历史评测分数仍对应原记录。

## 近期产品重点（规划）

- 做扎实四件事：**行程卡、具体雪场资料、可溯源图片、推荐教学视频**；图片和视频的可用程度以实际素材核验为准。
- 目的地采用 **20 个规划池、首批 12 个**，覆盖日本、东北、新疆、崇礼、中部、欧洲等，名单与验收见 [ROADMAP.md](ROADMAP.md)。规划数量不等于已收录数量，也不等于本仓库已附带的档案数量。
- 既有软件主线聚焦雪场选择与行程辅助；不承诺最低价、实时雪况或自动动作诊断。独立硬件竞赛扩展是未实现提案，见 [docs/proposals/SPORTS_HARDWARE_MVP.md](docs/proposals/SPORTS_HARDWARE_MVP.md)，不列入本页近期产品重点。

---

## 公开快照边界（请先读）

| 项目 | 说明 |
|---|---|
| **雪场全文数据未附带** | `ski-backend/data/resorts/` 只保留字段模板和一份明确标注的虚构示例。作者本地策展的真实雪场 YAML 多为第三方公开页面转引，版权在原出处，本快照不把它们当作可再分发的开放数据。归因或链接不等于授权。请自行准备有权使用的档案，步骤见该目录 [README](ski-backend/data/resorts/README.md) 与 [DATA_LICENSE.md](DATA_LICENSE.md)。 |
| **评测不能开箱复现历史分数** | 仓库提供 62 条人工维护的检索/拒答用例（`ski-backend/data/eval/golden_set.yaml`）以及历史评测报告。这些用例外依赖上述未随仓库提供的完整档案，**克隆后直接跑 `python -m scripts.evaluate` 不会得到历史报告里的命中率**，也不应把历史数字写成当前线上成绩。 |
| **向量库只能整集合重建，不能记录级删** | 记录级 `delete(ids)` 在较大集合上可能触发 compaction，本机 chromadb 曾出现写不出 HNSW 段索引、集合永久不可读的情况。历史排障记录见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) #24，不代表你当前环境的集合状态。清空或换分块请使用 `python -m rag.ingest --rebuild`。 |
| **数据库迁移** | 已接入 Alembic 初始基线；已有数据库不会被自动接管。见 [迁移说明](docs/DATABASE_MIGRATIONS.md)。 |

## 亮点

| 能力 | 做法 |
|---|---|
| **溯源** | 每条结论带来源编号与置信级别：A 官方 / B 实拍 / C 经验；价格带录入日期与保鲜期 |
| **防幻觉三闸** | 向量距离阈值（历史标定 0.85）→ BM25 关键词覆盖闸门 → **收录范围守卫**（问未收录雪场直接拒答） |
| **混合检索** | 向量 + BM25 双路召回，经 RRF 融合和规则加权排序；`RERANK_TOP_N` 是融合后的截断条数，**不是神经 Cross-Encoder / 重排模型** |
| **分层推荐** | 用户画像（水平/单双板/区域/预算）；单双板硬过滤 + 水平软加权 |
| **结构化查询** | “有几条高级道”这类统计走 MySQL（RAG 做 count 会猜数字），并区分“官方总数”与“本库已收录明细” |
| **行程状态机** | 显式 TripState + Command 记录行程要素，信息不全自动追问（不是靠提示词祈愿） |
| **行程卡分模式** | 配置默认 `CARD_MODE=data`，从数据渲染；`model` 是保留的模型结构化出卡路径。不能把模型出卡重写流程当作默认数据渲染流程 |
| **证据信封** | 工具返回值统一带 `source_type / source / date / expire_at`，让引用成为数据结构而非提示词请求 |
| **流式输出** | SSE 逐字上屏 + 每步进度 + 可随时中断；引用校验打回时发 `reset` 让前端清空重写 |
| **评估工具与历史报告** | 见下一节。检索命中 ≠ 回答正确 |

## 评测口径与历史报告

分层评测已经实现，但要把「检索指标」和「回答质量」分开看：

- **检索级**：`scripts/evaluate.py` 按同一 chunk 内的预期子串组计算 hit@k、MRR；拒答用例看检索是否为空。当前实现是向量 + BM25，经 RRF 和规则加权，不是神经重排。
- **回答级**：端到端脚本会查关键词/引用编号是否合法、数字是否被证据支撑、价格是否仍在时效内。这些检查都不等于回答在语义上完全正确。
- **用例**：`golden_set.yaml` 现有 **62 条人工维护的检索及拒答用例**。另外还有派生集、端到端集和若干历史报告，口径不同，不能直接加减。

| 历史报告（均非本快照导出时重跑） | 原记录 | 使用时请注意 |
|---|---|---|
| `last_report.md`（标记 2026-11-10 01:12:34） | 52 条检索 hit@1/3/5=52/52、MRR=1.000；拒答 10/10；分层 3/3 | 针对当时的完整档案与配置；**不是问答准确率 100%**，也不能当作本公开快照的开箱结果 |
| `generated_report.md`（标记 2026-10-07） | 102 条检索命中；拒答 8/8 | 同源派生回归，不是独立回答准确率 |
| `last_e2e.md` | 125 题；引用存在 73/125，引用合法与数字 grounding 均 125/125；P50 7.2s / P95 14.1s | 引用合法和数字校验 ≠ 语义正确；正文无生成日期 |
| 更早基线（2026-10-07） | 42 题 hit@1 88%、MRR 0.933 | 题集/语料/配置不同，不能和 52 题直接算提升 |

更细的边界见 [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md)。

## 技术栈

**后端**：FastAPI · SQLAlchemy 2 · MySQL · JWT ｜ **Agent**：LangChain `create_agent` · LangGraph（状态机 / SqliteSaver）· GLM
**RAG**：Ollama 本地嵌入（dmeta-embedding-zh）· Chroma · 向量 + BM25 + RRF + 规则排序 ｜ **前端**：uni-app（H5）· SSE 流式输出

## 目录

```
ski-backend/
├── rag/          RAG 管线（parser / chunker / embedding / store / retriever / ingest）
├── agent/        Agent 核心（factory / tools / state / evidence / prompts / middleware）
├── api/ service/ dao/ models/ schemas/   FastAPI 分层
├── scripts/      同步 MySQL / 检索评估 / 环境自检 / 命令行对话 / 离线验证
├── tests/        pytest 用例（接口契约 / 收录范围守卫 / 离线套件）
├── data/
│   ├── resorts/  字段模板 + 虚构示例（完整真实档案请自行准备）
│   └── eval/     62 条人工用例 + 其他评测集与历史报告
├── init_db.py    建表；如需演示账号，请自行设置 DEMO_USERNAME / DEMO_PASSWORD
└── ski-front/    uni-app 前端（对话 / 行程 / 我的）
```

## 快速开始

```bash
cd ski-backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # 填入你自己的 API 密钥、数据库连接和 SECRET_KEY（不要使用仓库占位值）
python init_db.py             # 建表。演示账号不会使用仓库内置口令，需自设环境变量后才会创建
python -m scripts.sync_to_mysql   # 将 resorts/ 档案写入 MySQL（先放入你有权使用的档案或自带示例）
python -m scripts.export_graph    # 为交互入口生成当前档案的图谱
python -m rag.ingest --dry-run   # 只解析体检，不调用嵌入模型
python -m rag.ingest             # 写入向量库（需先启动 Ollama 并 pull 嵌入模型）

python -m scripts.check_env   # 环境自检
python -m pytest -rs          # 自动化测试，显示本地数据缺失的跳过原因
python -m scripts.chat_cli --debug
python main.py                # 文档 http://127.0.0.1:8000/docs ；健康检查 http://127.0.0.1:8000/
```

前端：用 HBuilderX 打开 `ski-backend/ski-front`，再运行到浏览器。

入库与对话需要你自己的模型密钥和本地嵌入服务；本仓库不提供密钥，也不分发模型权重。完整 62 条检索评测还需要你准备好对应的雪场档案。

## 文档地图

先看这四项。没有单独的架构文档，架构入口指向本页已有的「技术栈」和「目录」章节，不另造文件。数据许可与公开快照边界仍写在本页正文，不因索引折叠而取消。

| 文档 | 看它是为了 |
|---|---|
| [本 README](README.md) | 本页：产品说明、公开快照边界、评测口径与启动 |
| [docs/DEMO.md](docs/DEMO.md) | 演示入口与从历史 P0 计划摘出的验收条件 |
| [本页「技术栈」](#技术栈)与[「目录」](#目录) | 技术栈与仓库目录结构 |
| [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) | 核对日期、模型默认值、历史报告及未验收边界 |

<details>
<summary>完整文档索引</summary>

| 文档 | 看它是为了 |
|---|---|
| [docs/backend-workflow.md](docs/backend-workflow.md) | 后端数据流、dry-run 与检索调试 |
| [AGENTS.md](AGENTS.md) | 协作者必读：命令、目录职责、约定、已知坑 |
| [ROADMAP.md](ROADMAP.md) | 接下来做什么，每条都带可量化判据 |
| [docs/proposals/SPORTS_HARDWARE_MVP.md](docs/proposals/SPORTS_HARDWARE_MVP.md) | 未实现的可穿戴硬件提案，非当前软件主线 |
| [docs/DATABASE_MIGRATIONS.md](docs/DATABASE_MIGRATIONS.md) | Alembic 新库初始化、旧库结构核对与接管边界 |
| [docs/archive/SHORT_DELIVERY.md](docs/archive/SHORT_DELIVERY.md) | 已归档的短交付清单与历史本机测试记录 |
| [docs/MEDIA_COLLECTION.md](docs/MEDIA_COLLECTION.md) | 图片/教学视频来源授权与接入规则 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 数据贡献门槛、门禁、提交规范 |
| [GOVERNANCE.md](GOVERNANCE.md) | 决策流程、发布规则、数据治理 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 踩坑记录与设计决策 |
| [docs/demo-script.md](docs/demo-script.md) | 详细演示脚本 |
| [docs/GUIDANCE_AND_TERRAIN.md](docs/GUIDANCE_AND_TERRAIN.md) | 滑雪需求澄清、教学资源空态与三维展示边界（产品功能说明，不是面试追问） |
| [SECURITY.md](SECURITY.md) | 安全上报渠道 |
| [CHANGELOG.md](CHANGELOG.md) | 版本记录 |
| [DATA_LICENSE.md](DATA_LICENSE.md) | 数据授权（**代码开源 ≠ 数据开源**） |
| [DEPENDENCIES.md](DEPENDENCIES.md) | 依赖许可清单 |

</details>

## 许可

- **代码**：[Apache-2.0](LICENSE)。版权声明见 LICENSE 文首；这不构成对第三方数据或商标的权利主张。
- **依赖**：核心依赖为 MIT / Apache-2.0 / BSD；唯一的 AGPL 依赖 PyMuPDF 已移出核心依赖。详见 [DEPENDENCIES.md](DEPENDENCIES.md)。
- **数据**：本快照只附带模板和虚构示例。转引资料的版权归原出处，`sample` 数据不得当作事实引用，见 [DATA_LICENSE.md](DATA_LICENSE.md)。

## 说明

- 密钥只放本地 `.env`（已被 `.gitignore` 忽略）。示例环境文件只保留空占位，不提供可用的默认口令。
- 问题与安全报告请走 [GitHub Issues](https://github.com/zzs-327/powderpilot/issues)。
