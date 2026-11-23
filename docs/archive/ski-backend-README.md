> **归档的后端长篇 README。** 当前启动步骤以仓库根目录 [README](../../README.md) 为准；操作补充见 [../backend-workflow.md](../backend-workflow.md)；本目录的简短入口是 [../../ski-backend/README.md](../../ski-backend/README.md)。本文保留更细的目录说明、历史进度和评测叙述，其中的本机路径与历史命中率不是开箱结果。

# PowderPilot · 滑雪情报官（后端）

> 反照骗、可溯源的滑雪情报 Agent：选雪场 / 排行程 / 分层推荐 / 教学与避坑。
> 入口文档：根目录 [README](../README.md) 的「文档地图」；工程约定与已知坑见 [AGENTS.md](../AGENTS.md)；
> 路线图见 [ROADMAP.md](../ROADMAP.md)；踩坑记录见 [docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)。

---

## 快速开始

### 1. 准备环境

**推荐直接复用课程环境**（已实测，本机路径）：

```bash
# 注意：conda activate zzss 可能解析到 <本地路径>\.conda\envs\zzss（空的），
# 真正的课程环境在 <你的 Python 环境>，用绝对路径最稳。
<你的 Python 环境>/python.exe -V
# Python 3.14.2 ｜ 已装 langchain 1.3.18 / langgraph / langchain-ollama /
# langchain-chroma / chromadb / fastapi / sqlalchemy / pymupdf

# 只缺一个包：BM25 关键词召回
<你的 Python 环境>/python.exe -m pip install rank-bm25

# 其余依赖按需补齐
<你的 Python 环境>/python.exe -m pip install -r requirements.txt
```

**骨架自检**（应该全部输出 OK）：

```bash
cd ski-backend
for m in main agent.tools agent.middleware rag.retriever; do
  <你的 Python 环境>/python.exe -c "import $m; print('$m OK')"
done
```

### 2. 配置

```bash
cp .env.example .env     # 然后填 DATABASE_URL / SECRET_KEY / ZHIPU_API_KEY
```

### 3. 建库

```sql
CREATE DATABASE ski_db DEFAULT CHARACTER SET utf8mb4;
```

首次启动会自动建表（`models/` 下的表结构），也可以手动执行迁移。

### 4. 启动

```bash
cd ski-backend
<你的 Python 环境>/python.exe main.py
# 文档：http://127.0.0.1:8000/docs
# 健康检查：http://127.0.0.1:8000/
```

### 5. 准备嵌入模型（RAG 用）

```bash
ollama pull shaw/dmeta-embedding-zh
ollama list          # 确认模型在
```

### 6. 跑通 RAG（Day1 已实现）

```bash
cd ski-backend
# 只解析与体检，不调用嵌入模型（先跑这个最省事）
<你的 Python 环境>/python.exe -m rag.ingest --dry-run

# 真正入库（会调用 Ollama 嵌入）
<你的 Python 环境>/python.exe -m rag.ingest --rebuild

# 混合检索测试（--debug 可分别看到向量与 BM25 的召回）
<你的 Python 环境>/python.exe -m rag.retriever "万龙有几条高级道"
<你的 Python 环境>/python.exe -m rag.retriever "金龙道难不难" --debug
<你的 Python 环境>/python.exe -m rag.retriever "新手第一次去要注意什么" --level beginner --board snowboard
<你的 Python 环境>/python.exe -m rag.retriever "二世谷雪票多少钱"    # 库里没有 → 应输出"无结果"
```

> Windows 控制台中文：脚本入口已统一把 stdout 切成 UTF-8（`utils/console.py`），
> 一般不需要再手动设 `PYTHONIOENCODING`；若在第三方终端里仍有乱码，可临时加上它。

### 7. 同步结构化数据到 MySQL（Day1 晚上已实现）

```bash
cd ski-backend
# 建库 + 建表 + 把 data/resorts/*.yaml 同步进 MySQL（幂等，可反复跑）
<你的 Python 环境>/python.exe -m scripts.sync_to_mysql

# 改了模型字段（加列/删列）时先重建表，数据从 YAML 重新同步
<你的 Python 环境>/python.exe -m scripts.sync_to_mysql --recreate
```

**数据流向（单一真相源）**：

```
data/resorts/*.yaml  ──sync_to_mysql.py──▶  MySQL（resort/run/price/photo）
        │                                        │
        └──rag.ingest──▶ Chroma 向量库            └──▶ Agent 工具（SQL 查询）
```

改数据只改 YAML，然后跑这两条命令即可，不要在数据库里手改。

---

## 目录结构

```
ski-backend/
├── main.py               入口（异常处理 / CORS / 路由注册）
├── .env.example          配置模板（.env 已被 gitignore）
├── config/               配置 / 数据库 / 依赖注入 / 自定义异常
├── api/                  路由层（user / chat / profile / resort 均已实现）
├── service/              业务层（user / agent / profile / resort）
├── dao/                  数据访问层
├── models/               SQLAlchemy 表（user 已完成）
├── schemas/              Pydantic 模型（result / user 已完成）
├── utils/                JWT / 密码工具
├── rag/                  ★ RAG 管线（本项目重点一）
│   ├── parser.py         文档解析（YAML/PDF/MD → Document）
│   ├── chunker.py        中文分块
│   ├── embedding.py      Ollama 嵌入（已实现懒加载工厂）
│   ├── ingest.py         入库脚本
│   └── retriever.py      向量 + BM25 + RRF 混合检索
├── agent/                ★ Agent 核心（本项目重点二）
│   ├── agent.py          create_agent 装配
│   ├── tools.py          4 个工具
│   ├── state.py          行程状态机 TripState
│   ├── evidence.py       证据信封（已实现数据结构）
│   ├── prompts.py        系统提示词（已实现）
│   └── middleware.py     证据信封 / 引用校验 / 裁剪
├── data/
│   ├── resorts/          雪场档案 YAML（单一真相源，含 _template.yaml）
│   └── photos/           实拍图（原图不入 git）
└── ski-front/            uni-app 前端（对话 / 行程 / 我的，已接 SSE 流式）
└── tests/                pytest 用例（接口契约 / 收录范围守卫 / 离线套件）
```

---

## 当前进度

| 模块 | 状态 |
|---|---|
| FastAPI 分层骨架 / 统一响应 / 全局异常 / JWT | ✅ 已就绪 |
| 用户注册登录 | ✅ 已就绪 |
| 配置项（模型/嵌入/向量库/检索参数） | ✅ 已就绪 |
| rag/ 与 agent/ 目录与函数签名 | ✅ 骨架就绪（实现见 TODO） |
| **雪场档案 YAML（万龙 / 丝绸之路 / 北大湖，含数据可信度分级）** | ✅ **已换真实核对数据；北大湖为官网直采** |
| **RAG 管线（解析/分块/嵌入/入库/混合检索）** | ✅ **已实现并跑通** |
| 距离阈值拒答 + 关键词覆盖闸门 | ✅ 已实现（防幻觉） |
| 分层软加权 + 统计意图提权 | ✅ 已实现 |
| **结构化表（雪场/雪道/价格/实拍图）+ DAO + Service** | ✅ **已实现** |
| **YAML → MySQL 同步脚本（幂等）** | ✅ 已实现 |
| 工具：search_resort_kb / get_resort_runs / get_resort_price | ✅ 已实现 |
| 用户画像表 + 渐进式采集 + update_profile 工具 | ✅ 已实现 |
| **Agent 装配（create_agent + 6 工具 + 状态机 + 记忆）** | ✅ **已实测通过（glm-5.3-flash）** |
| 行程状态机 set_trip_info + 追问判定 | ✅ 已实现 |
| 结构化行程卡 TripCard（response_format） | ✅ 已实现（可开关） |
| **FastAPI 接口层（chat/profile/resort + SSE）** | ✅ **已实现并验证** |
| **流式对话（SSE + 打字机前端 + 进度事件 + 可中断）** | ✅ **已实现（离线 20+17 项 + 无头浏览器实测）** |
| 统一异常格式（401/403/404 也返回 Result） | ✅ 已实现 |
| **中间件栈（官方重试/摘要 + 自研编号唯一/引用校验/裁剪兜底）** | ✅ **已实现（离线 49/49 + 真模型验证）** |
| 工具：get_weather | ⬜ 可选（当前优雅降级，不报错） |
| **前端（ski-front：对话 / 行程 / 我的）** | ✅ **已实现（H5 运行，对话页流式输出）** |
| **评估集 + 指标脚本（35 条检索 + 6 条拒答，另 4 条 pending）** | ✅ **已实现并跑出数字** |
| **pytest 用例（接口契约 / 收录范围守卫 / 三个离线套件）** | ✅ **已实现（`python -m pytest`）** |
| 收录范围守卫（未收录雪场直接拒答） | ✅ 已实现（策展名单 + 后缀正则两道） |

### 11. 检索评估（Day3 已实现）

```bash
cd ski-backend
<你的 Python 环境>/python.exe -m scripts.evaluate      # 跑 golden set，报告写入 data/eval/last_report.md
```

评估集：`data/eval/golden_set.yaml`（39 条检索 + 6 条拒答，全部生效、无 pending）。
当前基线（2026-10-03 实测）：

| 指标 | 数值 |
|---|---|
| hit@1 | 82%（32/39） |
| hit@3 | 97%（38/39） |
| hit@5 | 100%（39/39） |
| MRR | 0.894 |
| 拒答正确率 | 100%（6/6） |
| 分层命中率 | 100%（3/3） |
| 语料规模 | 12 座雪场 / 538 chunk（`data/eval/last_report.md` 是唯一真相源） |

> 12 座雪场是「已收录」口径；**拒答用例问的是名单外的雪场**（二世谷 / 南山 / 亚布力 / 军都山），
> 所以新增雪场时务必重跑 `scripts.evaluate`——它带一项**拒答用例失效体检**：
> 一旦某条拒答题问的雪场被收录进来，会直接点名提醒（r05/r06 就是这么补上的）。

> 2026-10-03 把 4 条 `pending`（银河 / 拾雪川 / 雪如意 / 多乐美地）转成正式用例：
> 它们的"数据已备好但未进向量库"备注是坑 #24 时期的产物，早已过期（数据确实在库里）。
> **被 `pending` 跳过的用例等于没有覆盖**——4 条实测全部命中，用例数 35 → 39。
>
> hit@1 82% 看着不如早期（93% → 89% → 83% → 82%）不是检索退化：语料从"1 个雪场 28 条示例"
> 一路扩到"12 座雪场"，干扰项变多，新增的还都是**更难的题**——跨雪场隔离（问北大湖不能答成万龙）、
> 只公布总数没有逐条明细的雪场、复合分级归并后的分层题。
> **hit@5 始终 100%，说明正确答案一直在模型能拿到的证据里**，差距出在排序而不是召回。
> 根目录 README 只保留这一份数字，不再各自维护一套。
>
> 当前没能排到 @1 的 7 条（q07 / q14 / q19 / q23 / q24 / q32 / q39）是下一步的调优靶子，
> 其中 q14「万龙在哪个城市」排到 @5——top1 被"可信度补充"这类**元信息 chunk** 抢占，
> 属于"元信息该不该与事实 chunk 同权重"的老问题（见 `rag/parser.py` 的元信息块设计）。

改分块/检索权重/embedding 后重跑这个脚本，就能看出是变好还是变坏——**别凭感觉调参**。

### 11.5 中间件离线验证

```bash
cd ski-backend
<你的 Python 环境>/python.exe -m scripts.test_middleware   # 49 项断言，不需要 API key
```

引用校验是"机制"，机制就该能被确定性验证。这个脚本用假模型按剧本喂消息，
复现"编造数字 / 引用不存在的编号 / 屡教不改 / 正常回答 / 工具空手而归 / 裁剪越界 /
跨轮引用上一轮编号"等情况。

**中间件栈分两类，别混着看**（`agent/middleware.py` 的 `MiddlewareFactory`）：

| 类型 | 组件 | 职责 |
|---|---|---|
| 官方件 | `ToolRetryMiddleware` | 工具失败自动重试（指数退避） |
| 官方件 | `SummarizationMiddleware` | 上下文超长时摘要压缩（一级，保住信息） |
| 自研件 | `evidence_envelope` | 证据编号按工具分段，保证全轮唯一 |
| 自研件 | `citation_check` | 引用编号 + 带单位数字的后置校验，不过则重写/降级 |
| 自研件 | `trim_history` | 消息数硬上限的兜底裁剪（二级，保险丝） |

> 为什么引用校验必须自研：官方中间件库里没有"引用校验"这个东西，
> 没有任何官方件能判断"回答里的 35 条是不是编的"——这是本项目"反照骗"命题的技术落点。
> 而工具重试、上下文摘要属于通用工程问题，能直接用官方的就别自己写。

**校验的"回看窗口"**（`CITATION_LOOKBACK_TURNS`，默认 3）：
本轮**有**证据时，允许沿用最近 3 轮出现过的证据编号与数字。

- 为什么需要：白名单原来只认本轮，但模型的引用是跨轮连续的——追问「那价格呢？」时
  它会把上一轮的编号带进回答，于是被判"引用了不存在的编号"，重写还带着它，最后降级成拒答。
  **最普通的追问路径被判死**，而且打回一次就多等一轮模型调用。
- 为什么不是"整段会话"：证据基数越大，数字巧合匹配的机会越多，校验越松；
  数据更新后，几十轮前的旧证据也不该继续给新回答背书（记忆是有保质期的）。
- 设为 `0` = 关闭回看，退回严格模式（可做对照实验）。
- 边界：本轮**空手而归**（工具回了"未收录"）或**没调工具**时，回看窗口一律不生效——
  否则"未收录的雪场"会被答成上一个雪场的价格，那是最坏的一种错。

### 11.6 引用校验压测（把"误伤率"变成数字）

```bash
<你的 Python 环境>/python.exe -m scripts.experiments.stress_citation              # 6 场景 × 3 轮
<你的 Python 环境>/python.exe -m scripts.experiments.stress_citation --scenario price_followup --rounds 5
<你的 Python 环境>/python.exe -m scripts.experiments.stress_citation --list       # 看有哪些场景
```

跑的是 demo 里真正会走的那几条路（结构化统计 / 多轮追问价格 / 价格保鲜期 /
RAG 检索 / MCP 天气 / 未收录拒答），统计**降级率、打回次数、未答到要点、平均耗时**，
报告写入 `data/eval/last_stress.md`。

> 为什么要单独压这个：**误伤比漏报更伤演示**——漏掉一条幻觉，用户看不出来；
> 误伤一次，用户直接看到"暂无可靠来源"。而且这个脚本是回归工具：
> 以后动 `_check_facts` / `_derive` 的规则，跑一遍就知道有没有变差。

### 11.7 日志（诊断留痕）

诊断信息（尤其是引用校验的打回原因、被判不合格的回答原文）同时写控制台和
`logs/backend.log`（滚动切分，已在 `.gitignore` 里）。

> 为什么必须落盘：服务往往起在某个终端或 IDE 里，stdout 没人接。
> 实测踩过一次——用户看到回答变成拒答话术，我们**看不到原因**，只能靠猜。

### 11.8 数据可信度分级（含多源交叉印证）

**两个正交的维度**（`rag/parser.py` 的 `ResortProvenance`）：

| 维度 | 取值 | 回答的问题 |
|---|---|---|
| `source_type` | A 官方 / B 实拍 / C 经验 | 这条信息**本身**是什么性质 |
| `data_status` | verified / **corroborated** / transcribed / sample | 我们**怎么拿到**它的 |

`data_status` **按数据类型生效**（`source_defaults.<类型>.data_status` 可单独覆盖）：

```yaml
source_defaults:
  run:   { data_status: corroborated }   # 雪道有独立来源印证
  price: { }                             # 价格只有单一转引 -> 继承雪场默认档
```

> 为什么必须按类型分：真实情况就是"雪道有多源印证、价格只有单一转引"。
> 一个雪场级档位表达不了这种差别，而表达不了就会说谎——
> 要么把价格一起说成有印证，要么把雪道一起否掉。
> 雪场表里的 `data_status` 取**各类里最保守的那一档**（`weakest_status`），
> 行级真相在 `run.data_status` / `price.data_status`。

**逐字段的印证记录**（`provenance.corroboration`）：谁印证了谁、哪条一致、
**哪里分歧**、独立性如何、谁复核过。四类结论：
`consistent 一致 / partial 部分一致 / conflict 有分歧 / new_field 我方原档案没有`。

> 两条不能省的：
> 1. **分歧也是一等公民**。丝绸之路的雪道总数在四个来源之间是 12/8/28/6，
>    我们不下断言，但把"谁说了多少"原样留在档案里——这恰恰是用户最该知道的事。
> 2. **交叉印证 ≠ 独立**。两个页面都抄同一篇宣传稿时，它们的一致什么都证明不了。
>    所以每条记录都要记 `stated_source`（它对原始出处的自述）与 `independence`。
>
> 万龙就是"印证材料自己打架"的例子（2026-09-12 逐条核对过）：
> 百科与票务聚合页都是 **32 条 / 554 米 / 雪季超 180 天**，
> 而**官网是 22 条 / 560 米 / 约 150 天**——三项全不一致。
> 官网把"树林道、蘑菇道、公园"与"22 条国际标准雪道"分开列，推测 22 只计压雪雪道。
> **来源不一致就不提档**：雪道仍是 transcribed，工具输出改成同时给出两个口径，
> 并明确要求"不要只报一个数、不要断言哪个是总数"。
>
> 官网倒是让**概览类数据第一次够到 verified（官方直采）**：地址、电话、雪季、资质，
> 以及雪道总数与落差这两条"官方声明"。为名副其实，概览块只用官网字段渲染，
> 交通参考单独成块（出处不是官网，不能蹭 verified）。

离线验证：`python -m scripts.test_provenance`（122 项断言，不需要 key、不需要 MySQL），
盯三件事：档位表在 parser 与 evidence 两处**逐字一致**、印证记录（含分歧）解析正确、
话术按档位分档（二手转引不许说"官方公布"）。
> 现在每行带 pid，多个进程（服务 / 压测 / 离线测试）同时写也不会互相污染统计。

### 11.9 行程卡字段校验（结构化输出不是法外之地）

正文走引用校验，但行程卡是 `response_format` 那条路的产物，**不经过消息中间件**。
实测（2026-10-03，glm-4-flash）它就出过这样一张卡：行程里写「二世谷雪道」（未收录）、
周末出行按平日价算、「酒店住宿 200 / 餐饮 100 / 交通 100」查无依据却照样挂着
`sources=[E301]`、合计 3150 与明细之和 3360 对不上。**看着有来源，数字没依据**——
正是本项目最该避免的形状。所以 `agent/card_check.py` 做四项校验：

| 校验 | 规则 | 抓的是什么 |
|---|---|---|
| A 收录范围 | 卡片文本出现未收录雪场 → 违规 | 「二世谷雪道」这类串场/幻想地名 |
| B 来源白名单 | `sources[].index` 必须在证据白名单里（**含回看窗口** `CITATION_LOOKBACK_TURNS`） | 引用根本不存在或早已失效的编号 |
| C 预算有据 | 每个金额要么在证据里"**同一行 + 费用项关键词重合**"，要么在 `note` 里写明是估算 | 酒店/餐饮/交通这类凭空加的费用 |
| D 预算自洽 | `total` 必须等于明细之和（容差 1 元） | "合计 = 人均 = 用户报的数" |

不合格 → 把违规清单**原样喂回模型重写一次**（与引用校验同一套路）；仍不合格 → **不出卡**
（宁可没有卡，也不给错卡）。三个工程细节都是踩出来的：

- **不能把结构化输出的副作用消息当证据**：那条 `Returning structured response: …` 里就是卡的原文，
  留着等于自己给自己作证——"教练课 300 元"会因为出现在这条消息里被判成"有依据"（实测假阴性）。
- **证据窗口要回看 N 轮**：出卡那一轮往往只有 `set_trip_info`，价格是前几轮查的；
  只认本轮的话任何 `sources` 都会被判"编号不存在"，等于永远出不了卡。
- **卡被丢弃后文案不能漏内部消息**：`_clean_reply` 原来在 card 为空时把
  `Returning structured response…` 原样返回，而"打回后 card 恰好为 None"这条新路径把它暴露了。

离线验证：`python -m pytest tests/test_card_check.py tests/test_reply_cleanup.py`（14 项，
纯函数 + 接线层，不需要模型 / MySQL / Ollama）。

> **已知边界**（写进用例里，避免误以为已覆盖）：数字**有据但档位选错**（周末出行用平日价 560）
> 拦不住——560 确实在证据里；要判档位得把行程要素纳入校验，代价是可能误伤
> "两日行程含一个平日"这种合法情况。
>
> 另一个观察：弱模型（glm-4-flash）实测基本产不出合规卡（两次都被打回，最终不出卡）。
> 这既说明校验有效，也说明**演示行程卡要换强模型**（改 `.env` 的 `LLM_MODEL`）。

### 9. 接口清单（Day3 上午已实现）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/user/register` | 注册 |
| POST | `/api/v1/user/login` | 登录，返回 JWT |
| GET | `/api/v1/user/info` | 当前用户 |
| GET | `/api/v1/profile` | 读画像 |
| PUT | `/api/v1/profile` | 更新画像（level/board_type/home_region/budget_band） |
| POST | `/api/v1/chat` | 对话，一次性返回（非 H5 端 / 流式通道不通时的兜底） |
| POST | `/api/v1/chat/stream` | 对话，**SSE 流式**（H5 走这条，打字机效果） |
| GET | `/api/v1/resort` | 雪场列表 |
| GET | `/api/v1/resort/{key}/runs` | 雪道统计与清单（?difficulty=高级） |
| GET | `/api/v1/resort/{key}/prices` | 价格（?item=雪票） |
| GET | `/api/v1/resort/{key}/photos` | 实拍图集（含 url 字段） |

启动后打开 http://127.0.0.1:8000/docs 可直接试。

### 12. 自动化测试（pytest）

```bash
cd ski-backend
<你的 Python 环境>/python.exe -m pytest                      # 全部用例
<你的 Python 环境>/python.exe -m pytest tests/test_api_contract.py -q
```

| 文件 | 覆盖什么 | 依赖 |
|---|---|---|
| `tests/test_api_contract.py` | 统一响应结构（`code/msg/data`，**异常路径也得是它**）/ 没带 token 被拒 / 雪场列表带可信度档位 / 未收录雪场是业务错误而非 404 | MySQL（连不上自动 skip） |
| `tests/test_scope_guard.py` | 收录范围守卫：未收录雪场直接不返回证据、已收录雪场和通用问题不被误伤 | 无（连 Ollama 都不用） |
| `tests/test_offline_suites.py` | 复用三个既有离线套件：可信度分级 / 中间件引用校验 / 流式事件协议 | 前两者纯离线；**中间件套件需要 MySQL**（假模型驱动真 Agent 图，工具会真查库；没库时 pytest 跳过、CLI 明确报错） |

> 为什么把离线脚本也接进 pytest：它们本来就是确定性的"机制验证"（假模型 / 假 agent / 纯解析），
> 但手工跑容易忘。接进来之后，"改完跑一遍"是**一条命令**的事；
> 三个脚本本身仍是可独立运行的 CLI（`python -m scripts.test_middleware` 等）。

### 10. 前端运行（uni-app / HBuilderX，Day3 已实现）

前端目录：`ski-front/`，三个 tab 页 + 登录页。

| 页面 | 作用 |
|---|---|
| `pages/index/index.vue` | 对话页：**流式打字机** + 进度提示 + 来源标签 + 行程要素栏 + 停止生成 |
| `pages/trip/trip.vue` | 行程页：行程卡（日程/预算/装备/避坑/教学/来源/时效）+ 实拍图集 |
| `pages/profile/profile.vue` | 我的画像：水平/单双板/区域/预算四个 picker，直接写后端 |
| `pages/login/login.vue` | 登录 + 注册 |

运行步骤：

1. 先起后端：`<你的 Python 环境>/python.exe main.py`（默认 http://localhost:8000）
2. 打开 HBuilderX（本机路径 `D:\HBuilder\HBuilderX.3.6.13.20221209`）
3. 菜单 文件 → 打开目录 → 选 `ski-backend/ski-front`
4. 菜单 运行 → 运行到浏览器 → Chrome
5. 注册一个账号 → 登录 → 在对话页问问题

> **流式怎么走的**：H5 端用 `fetch` + `ReadableStream` 读 `POST /chat/stream`（`api/chat.js`），
> 文字一到就上屏，不再等整段答案（原来最长干等 30~90 秒）。
> ⚠️ `uni.request` 收不到分块——它会把整个响应读完才回调，所以**不能**用它做流式；
> 小程序端改用 `enableChunked`，都不支持时自动退回 `POST /chat`（协议不变，页面无感）。
> 实拍图若未配 MinIO，图片位置会显示占位块 + 拍摄日期/拍摄者（元数据仍可溯源）。

**流式事件协议**（`service/agent_service.py` 的 `achat_stream` → `api/chat.py` 逐条编码成 SSE）：

| 事件 | 含义 | 前端怎么用 |
|---|---|---|
| `meta` | 会话 id | 存下来，下一轮续聊（**第一条就发**，否则前端永远拿不到后端生成的 id） |
| `status` | 进度（正在查价格…） | 显示在气泡上方，替代原来那句笼统的"正在查资料" |
| `text` | 正文增量 | 追加渲染（带节流）+ 光标闪烁 |
| `reset` | 引用校验打回，清空重来 | **必须清空气泡**，否则两版答案叠在一起 |
| `trip` | 行程要素 | 顶部状态栏（还差什么一眼看见） |
| `card` | 结构化行程卡 | 存本地 + 会话里留一个可点卡片 |
| `evidences` | 本轮来源 | 回答下面的可点标签（点开看出处/日期/几手数据） |
| `error` | 流内异常 | 追加一行提示，不打断已完成的内容 |

离线断言（不联网、不调模型）：

```bash
cd ski-backend
<你的 Python 环境>/python.exe -m scripts.test_chat_stream   # 20 项：流式事件协议
cd ski-front && node tests/test_stream.mjs                    # 17 项：SSE 解析 + markdown 清洗
```


### 8. 命令行对话（Day2 已实现）

```bash
cd ski-backend
# 先在 .env 填 ZHIPU_API_KEY
<你的 Python 环境>/python.exe -m scripts.chat_cli --debug
```

`--debug` 会打印工具调用与返回，用来确认 Agent 是否选对了工具。
| 价格/雪道结构化表 | ⬜ Day1 晚上 |
| create_agent 装配 + 画像 + 状态机 | ⬜ Day2 |
| 证据信封 + 引用校验 + 行程卡 | ⬜ Day2 晚上 |
| chat/profile/resort 路由 + SSE | ⬜ Day3 上午 |
| 前端对话页 | ⬜ Day3 下午 |
| 评估集（10-20 条） | ⬜ Day3 晚上 |

---

## 代码风格约定（对齐课程工程 `day32/his-backend`）

| 约定 | 本项目做法 |
|---|---|
| 工具类 | `class` + `@staticmethod`：RAG 侧 `ResortParser` / `ResortRegistry` / `ChunkUtil` / `OllamaEmbeddingFactory` / `ResortVectorStore` / `RagIngestService` / `ResortRetriever`；Agent 侧 `AgentFactory` / `PromptBuilder` / `EvidenceFormatter` / `TripHelper` / `AgentService` |
| 服务类 | `__init__(self, session)` 组合 DAO，如 `ResortService` / `ProfileService` / `UserService` |
| 文档 | 中文类/方法 docstring；关键步骤用 `# 1. # 2.` 编号 |
| 重依赖 | 在方法内懒加载，并注释「懒加载 xxx，避免影响无关模块导入」 |
| 单例 | 模块级 `_xxx = None` + `global` 缓存（嵌入模型 / 向量库 / 雪场名册） |
| 异常 | 统一 `ServiceException`；`except Exception as e: raise ServiceException(f'xxx失败：{e}')` |
| 配置 | 大写 + 域前缀：`RAG_CHUNK_SIZE` / `OLLAMA_EMBED_MODEL` / `MAX_DISTANCE` |
| 工具函数 | 保持模块级 `@tool`（Agent 侧不封装成类） |
| 类型注解 | 全部方法补 `list[str]` / `dict | None` 等 |
| 兼容 | 类方法之外保留模块级别名（`retrieve = ResortRetriever.retrieve`），旧 import 不用改 |
| 数据库初始化 | `python init_db.py`（建表 + 补列 + 可选演示账号（需自设 DEMO_PASSWORD）），对齐课程 `init_db.py` 写法 |

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `ModuleNotFoundError: schemas.reuslt` | 已把拼写错误的 `reuslt.py` 修正为 `result.py`，确认 import 已更新 |
| 连不上 MySQL | 检查 `.env` 的 DATABASE_URL 与数据库是否已建 |
| 嵌入报连接错误 | Ollama 没启动，或没 pull 嵌入模型 |
| 入库后查不到 | 入库与检索的嵌入模型不一致，需重建集合 |
| 前端 401 | 请求头缺 `Authorization: Bearer <token>` |
| **429「余额不足或无可用资源包」** | 该模型欠费。改成 `.env` 里的 `LLM_MODEL=glm-4-flash`（免费）即可，代码不用动；换模型后**必须重启服务**（`.env` 不在热重载范围内） |
| 回答变成"暂无可靠来源" | 引用校验没过。先看 `logs/backend.log` 里的"被判不合格的回答"与原因——**多半是模型没标来源编号**（小模型常见），不是数据问题 |

### 换模型要付的代价（2026-09-13 实测，免费 glm-4-flash vs 收费 glm-5.3-flash）

同一套 Agent、同一批数据，只换 `LLM_MODEL`：

| 问法 | glm-5.3-flash | glm-4-flash（+ 已做的提示词加固） |
|---|---|---|
| 万龙有几条高级道 | 十几秒，会展开"官网 22 / 第三方 32 两个口径" | **8 秒**，直接答"19 条 [E201]"——对，但短 |
| 万龙雪票多少钱 | 完整表格 + 时效提醒 | 17 秒，9 个编号，一次通过 |
| 这数据是几手的 | 按数据类型分档说清 | ⚠️ **不稳定**：两次实测一次通过、一次连打回 2 次后降级成拒答 |
| 二世谷雪票多少钱（本库未收录） | 正确拒答 | 8 秒正确拒答（调了工具、如实说没收录） |
| glm-4.5-flash（也免费） | — | ❌ 实测 6 分钟仍在反复调同一个工具（多半是思考模式没关），不建议用 |
| glm-4.5-air（赠送包常见 1200 万 token） | — | ⚠️ **开结构化输出时用不了**，原因见下 |

### ⚠️ `tool_choice="required"` 是个隐形雷（2026-09-13 定位）

`glm-4.5-air` 有 1200 万赠送 token，看着很香，但实测**工具一次都不会真的执行**：
模型把工具调用当正文吐出来（`<tool_call>get_resort_price <arg_key>resort</arg_key>...`），
`tool_calls` 字段是空的，而这段文本里又没有"带单位的事实数字"，**连引用校验都拦不住它**。

定位过程（记下来省得再查一遍）：
1. 裸 HTTP 直连 → `tool_calls` 结构完全正常（说明不是 API 的问题）
2. 逐步加变量：真系统提示词 ✅ / 9 个工具 ✅ / temperature ✅ / TripCard 嵌套 schema ✅
3. 拦截 langchain 组装的请求体 → 发现多了一个参数 **`tool_choice: "required"`**
4. 单独验证：`required` ❌ 文本化、`auto` ✅ 正常

**`tool_choice: "required"` 是 langchain 开结构化输出（`ToolStrategy`）时自己加的**
（它需要模型每轮都调用那个隐藏的出卡工具）。`glm-4-flash` 认这个值，`glm-4.5-air` 不认，
不认还不报错，只是静默退化。

要用 glm-4.5-air 就得同时 `USE_STRUCTURED_OUTPUT=False`（代价：没有行程卡）。
另外 GLM-4.5/4.6 系列默认"先想再答"，必须 `LLM_THINKING=disabled`（见 config.py 注释）。

结论：**免费模型能跑通整条链路（工具调用 / 引用校验 / 流式 / 结构化输出都在），但回答更短、
且关键题不稳定**——而"可信度分档 / 来源分歧"恰好是 demo 最值钱的部分。

> 想让别的模型也过引用校验，提示词里做了两处加固（`agent/prompts.py`）：
> ① 把"必须标来源编号"压成一小段放到系统提示词**最末尾**（近因效应 + 逐字示例）；
> ② 工具路由表补上"这数据是几手的 -> search_resort_kb"（原来没这条，小模型会直接答"我不知道"）。
> 这两条对任何模型都只有好处，机制没动——编号照样要过中间件校验。

### 换模型 / 横向比模型：`scripts/experiments/bench_models.py`

通用榜单测的是"会不会答知识题"，跟本项目要的东西不是一回事。本项目的及格线是三条工程约束：
**会不会调工具**（9 个工具 + 结构化输出全靠 function calling）、**会不会标来源编号**
（不标就被打回、两次不过直接降级成拒答）、**快不快 / 收不收得住**。
这三条只有真跑一遍才知道，所以这个脚本直接跑真 Agent、量真指标（耗时 / 工具 / 编号数 /
打回次数 / 是否降级），把 key 加进 `.env` 就能横向比：

```bash
python -m scripts.experiments.bench_models --list          # 看候选清单与 key 状态（不花额度）
python -m scripts.experiments.bench_models                 # 跑全部"配了 key"的候选 × 4 道题
python -m scripts.experiments.bench_models --models 硅基 qwen3.8-flash --rounds 2
```

候选（都走 OpenAI 兼容端点，换模型只改 `.env` 三行，代码一行不用动）：

| 平台 | base_url | 模型 | 门槛 / 备注 |
|---|---|---|---|
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | 免费，已实测可用 |
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.8-flash` | 新用户 100 万 token（华北2 / 90 天），原生支持 Function Calling + 结构化输出 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `nex-agi/Nex-N2-Pro` | 标价 $0；397B MoE（Qwen3.5 底座）、262K 上下文；注册即送额度、无需绑卡 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `Qwen/Qwen3-8B` | 免费小模型，够不够用要实测 |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 豆包 | 每模型 100 万 token（90 天）；⚠️ model 要填控制台的接入点 id（ep-xxx） |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | **不免费**但便宜；本机那把 key 已失效（401） |

报告写到 `data/eval/last_model_bench.md`。

---

## 安全提醒

- `.env` 已加入 `.gitignore`，**不要**把密钥提交到仓库。
- 笔记里出现过的百度地图 AK 与智谱 Key 是明文，请到控制台轮换后再用。
