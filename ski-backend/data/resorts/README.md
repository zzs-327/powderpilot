# 雪场档案（公开快照）

本目录在公开仓库中只包含：

- `_template.yaml`：字段模板
- `example_snowvale.yaml`：作者自写的虚构示例（`data_status: sample`）

**完整的真实雪场 YAML 档案未包含在本快照中。** 那些档案多为从公开页面转引的二手资料，版权属于原出处，作者无权将其作为本仓库的开放数据再分发。来源标注不等于已获得再分发授权。

## 如何自行准备数据

1. 复制 `_template.yaml`，按字段填写你有权使用的资料（官方授权、自行核实或明确的公开数据）。
2. 为每条记录保留 `source_type` / `stated_source` / `primary_url` / `data_status`。
3. 标注为 `sample` 的数字只能用于走通流程，不能当事实引用。
4. 放入本目录后，在 `ski-backend` 下执行 `python -m scripts.sync_to_mysql` 与 `python -m rag.ingest`。

评测用例见 `ski-backend/data/eval/`。其中 62 条人工检索/拒答集依赖作者本地的完整档案，**克隆本仓库后不能开箱跑出历史报告中的命中率**。可先用示例档案验证安装与入库流程。
