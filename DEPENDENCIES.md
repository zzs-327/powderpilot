# 依赖与许可（DEPENDENCIES）

> 这份文件回答一个问题：**把这个项目拿去做商用，会不会被某个依赖的许可卡住？**
> 结论写在最前面，证据（包名 / 版本 / 许可 / 用途）写在后面。
> 数据许可是另一回事，见 [DATA_LICENSE.md](DATA_LICENSE.md)——**代码开源 ≠ 数据开源**。

## 结论

1. **核心依赖（`ski-backend/requirements.txt`）全部是宽松许可**：MIT / Apache-2.0 / BSD，
   没有 AGPL / GPL / LGPL / SSPL，没有 Commons Clause 或"非商用"限制。
2. **全项目唯一的 AGPL 依赖是 PyMuPDF**，已移出核心依赖，见下方"为什么 PyMuPDF 不在核心依赖里"。
3. 传递依赖（依赖的依赖）同样做过体检：146 个包里只有 5 个 MPL-2.0（**文件级**弱著佐权，
   我们不修改这些文件，因此不触发开源义务），其余仍是宽松许可。
4. **复核方式**：`python -m scripts.pre_open_source_check` 第 5 项会读已安装包的元数据，
   把 AGPL / GPL 标出来；CI 每次 push 都跑。新增依赖必须在下表登记。

## 核心依赖清单

版本为本文档写入时的实测环境（2026-10，Python 3.12）；可安装版本下界以 `requirements.txt` 为准。

| 包 | 实测版本 | 许可 | 用途 |
|---|---|---|---|
| fastapi | 0.141.1 | MIT | HTTP 接口层 |
| uvicorn[standard] | 0.52.3 | BSD-3-Clause | ASGI 服务器 |
| sqlalchemy | 2.0.52 | MIT | ORM |
| pymysql | 1.2.0 | MIT | MySQL 驱动 |
| cryptography | 50.0.0 | Apache-2.0 OR BSD-3-Clause | MySQL8 认证 / JWT 加密 |
| pydantic | 2.13.4 | MIT | schema 校验 |
| pydantic-settings | 2.15.0 | MIT | 读 `.env` |
| python-jose[cryptography] | 3.5.0 | MIT | JWT 签发与校验 |
| bcrypt | 4.0.1 | Apache-2.0 | 密码哈希 |
| PyYAML | 6.0.3 | MIT | 读 `data/resorts/*.yaml`（数据单一真相源） |
| langchain | 1.3.18 | MIT | `create_agent` / 中间件 |
| langchain-openai | 1.1.9 | MIT | 智谱 / DeepSeek 走 OpenAI 兼容接口 |
| langgraph | 1.2.11 | MIT | Agent 图 / 检查点 |
| langchain-text-splitters | 1.1.2 | MIT | 中文分块 |
| langchain-ollama | 1.1.0 | MIT | 本地嵌入 |
| langchain-chroma | 1.1.0 | MIT | 向量库接入 |
| langchain-community | 0.4.1 | MIT | 文档加载器 |
| chromadb | 1.5.9 | Apache-2.0 | 向量库本体 |
| rank-bm25 | 0.2.2 | Apache-2.0 | 混合检索的 BM25 一路 |
| pytest | >=8.0 | MIT | 测试 |
| httpx | 0.28.1 | BSD-3-Clause | TestClient |
| python-dotenv | 1.2.3 | BSD-3-Clause | 本地读 `.env` |

## 可选依赖（`ski-backend/requirements-optional.txt`）

| 包 | 许可 | 状态与说明 |
|---|---|---|
| pymupdf | **AGPL-3.0**（或 Artifex 商业许可，双许可） | **故意不放进核心依赖**，见下 |
| minio | Apache-2.0 | 远景：实拍原图改存对象存储（当前未启用） |
| langchain-mcp-adapters | MIT | 远景：接百度地图 MCP |
| fastmcp | Apache-2.0 | 远景：自研 MCP 服务 |

### 为什么 PyMuPDF 不在核心依赖里

这是一条**许可合规决定**，不是整理癖：

- PyMuPDF（import 名 `fitz`）是 **AGPL-3.0** 双许可。AGPL 的传染性对**网络服务**同样生效：
  一旦作为在线服务提供，就要向使用者开放源码，企业法务通常直接亮红灯。
- 而它在主流程里**根本用不到**：`rag/parser.py` 的 `parse_pdf()` 是唯一使用点，
  但仓库里**一个 `.pdf` 都没有**（`git ls-files` 实测），当前语料全是 YAML 档案。
- 所以把它连同另外三个远景依赖一起挪到 `requirements-optional.txt`：
  **核心依赖因此干净为 MIT / Apache-2.0 / BSD**，需要 PDF 解析的贡献者自行安装并自评许可。
- `scripts/check_env.py` 里对 `fitz` / `fastmcp` 的检查也标了"可选"，环境自检不会因为它缺失而报错。

## 传递依赖体检（实测）

按 `requirements.txt` + `requirements-optional.txt` 求依赖闭包，逐个读 `importlib.metadata`：
**共 146 个包**，许可标签分布如下（一个包带多个标签时会出现在多行，故合计大于 146）：

| 许可 | 包数 |
|---|---|
| MIT | 66 |
| Apache-2.0 | 41 |
| BSD（2/3-Clause） | 27 |
| PSF-2.0 | 5 |
| **MPL-2.0** | 5 |
| ISC | 2 |
| **AGPL-3.0** | 1（`pymupdf`，仅出现在**可选**清单） |

两点说明：

- **MPL-2.0 不构成风险**：它是**文件级**弱著佐权，只约束"被修改过的 MPL 文件本身"。
  本项目原样安装使用、不修改这些文件，因此不需要开放本项目源码。涉及 `certifi` / `orjson` / `tqdm` 等。
- 未发现 GPL / LGPL / SSPL / Elastic License / Commons Clause / 非商用条款。

## 运行期外部组件（不是 Python 依赖，但评审一定会问）

| 组件 | 许可 | 我们的用法与判断 |
|---|---|---|
| MySQL 8 | GPL-2.0（Community） | 作为**独立数据库服务**部署，我们通过 PyMySQL（MIT）走网络协议访问，属**进程外使用**，不传染本项目；只要不再分发 MySQL 二进制或把它嵌进我们的程序即可 |
| Ollama | MIT | 本地嵌入服务，HTTP 调用，随部署环境单独安装 |
| Chroma | Apache-2.0 | 作为库嵌入（`chromadb`），已在核心依赖表内 |
| Three.js / OrbitControls 0.128.0 | MIT | 三维雪场 H5 渲染；固定版本原样保存于 `ski-backend/ski-front/static/vendor/`，许可证同目录 `THREE-LICENSE.txt`；来源 `https://cdn.jsdelivr.net/npm/three@0.128.0/` |
| uni-app（前端） | DCloud 自有《uni-app 使用许可协议》 | **不是 OSI 标准开源协议**，故单独列出：本项目只通过官方工具链使用、未修改其源码；采用前请以 DCloud 官方协议原文为准。若需要完全规避，前端可迁移到 Vue 3 + Vite（MIT） |
| 嵌入模型 `shaw/dmeta-embedding-zh` | 以模型发布页标注为准 | **模型权重不随本仓库分发**，由使用者在自己的环境 `ollama pull`，因此不产生我们的再分发义务 |
| 智谱 GLM / DeepSeek API | 各家服务条款（非代码许可） | 只作为可配置的在线模型调用，不打包任何模型权重 |

## 新增依赖的规矩

1. 新依赖必须在本文件登记（包名 / 许可 / 用途），并在 PR 说明里写清为什么需要它。
2. **AGPL / GPL 一律不进 `requirements.txt`**，只能进 `requirements-optional.txt` 并注明许可与替代方案。
3. 提交前跑 `python -m scripts.pre_open_source_check`；CI 也会跑，AGPL / GPL 会被拦下。
4. 换掉某个依赖（例如换向量库）时，同步更新本表与 [ROADMAP.md](ROADMAP.md) 的对应阶段。
