# Python 脚本入口说明

业务请求走 `api/ → service/ → dao/`；这里的脚本是命令行工具，不是线上请求入口。均从 `ski-backend` 执行 `python -m scripts.<名称>`，不要直接移动文件或删除被测试引用的套件。

| 用途 | 脚本 |
|---|---|
| 日常自检与对话 | `check_env`、`chat_cli` |
| 数据同步与图谱导出 | `sync_to_mysql`、`export_graph`（向量入库另用 `python -m rag.ingest`） |
| 检索与回答评估 | `evaluate`、`eval_e2e`、`eval_policy` |
| 题集生成 | `gen_eval_set`、`gen_golden_set` |
| 离线套件 | `test_provenance`、`test_middleware`、`test_chat_stream`；pytest 会复用部分套件，不是无用文件 |
| 性能、费用与可靠性实验 | `experiments/` 下的 `bench_models`、`measure_cost`、`report_metrics`、`eval_retry_repeated`、`stress_citation` |
| 存储排障 | `probe_chroma`；不要把探针当作常规入库流程 |
| 发布检查与导出 | `pre_open_source_check`、`export_public` |

后端目录：`agent/` 管模型、工具、证据和状态；`rag/` 管资料解析与检索；`schemas/` 管接口数据结构；`tests/` 管自动化回归。`main.py` 为服务入口，`init_db.py` 为初始化入口。

`.test-deps/`、`.uv-cache/`、`__pycache__/`、`.pytest_cache/`、`_ptmp_pytest/` 是依赖或缓存；`chroma/`、`logs/`、`*.db` 是运行资料，不是业务 Python 模块。不要为整理目录而删除会话库、向量库或依赖环境。
