# P0 过夜执行计划（万龙样板闭环）

> 窗口：今晚 → 明早演示。远程仍用 Gitee（`https://gitee.com/zzs_s/ski-agent.git`），本地改、自行推送。  
> 章程红线：YAML 为真相源；候选 ≠ 生产；未知费用 ≠ 0；已知小计 ≠ 全预算；不串场；不手改 MySQL/Chroma；外币不进人民币字段。

## 角色（单人多帽）

| 帽子 | 职责 | 今晚范围 |
|------|------|----------|
| 产品/演示 | 定单路径、验收话术 | **万龙**出卡 + 拒答未收录 |
| 数据策展 | 审候选、写 YAML | 不动 Rusutsu 入库；候选只记笔记 |
| 后端 | 行程卡 / 币种兼容 | schema 可选 `currency`（默认 CNY） |
| 前端 | 空态 / 金额展示 | 跟币种字段，不硬编码外币当元 |
| 质检 | 定向 pytest + 手测 | `test_card_builder` 等 |

## P0 任务（批次 1）

### 串行（先做完再并行）

1. **冻结演示路径**：万龙 · 下周末 · 2 人 · 人均 2000 · 2 天 · 单板 → 出行程卡。  
2. **币种兼容（最小）**：行程卡 `BudgetItem` / `BudgetBreakdown` 增加可选 `currency`，默认 `CNY`；小计只加同币种；**不**改 MySQL `price` 表（需 Alembic，放批次 2）。  
3. **演示文案**：对话 starter「排万龙行程」、行程页空态说明小计口径（已做可复核）。

### 可并行（有余量）

- 手测：出卡 → 雪场库详情 → 问「二世谷/留寿都」拒答。  
- 更新本文件「完成勾选」；Gitee 推送由负责人执行。

### 明确不做（批次 2+）

- Rusutsu 候选 → YAML / sync / ingest（需人工 review 笔记）。  
- `price.currency` 列迁移、汇率换算、跨币种合计。  
- 全量重建 Chroma、stamp 现有库、扩 12 场。

## 验收标准（明早可演示即过）

- [ ] 一句话要素齐全后出现行程卡；预算为**已知单价小计**（周末约 650+200），文案含「非全行程预算 / 不能视作 0」。  
- [ ] 来源编号可点；caveats 含可信度/时效/模板行程说明。  
- [ ] 未收录雪场拒答，不串万龙价格。  
- [ ] `currency` 缺省为 CNY，万龙路径展示仍为人民币/¥；定向 pytest 通过。  
- [ ] 无自动把 `data/collection/*candidates*` 写入 `data/resorts/*.yaml`。

## 批次 2 笔记（Rusutsu / 外币）

1. 审 `rusutsu_*_candidates.json`：仅 `content_checked` + `usable_for_budget` 且有人工 review 笔记的条目可进 YAML。  
2. 新建 `rusutsu.yaml` 时价格行带 `currency: JPY`；先合并 Alembic 增加 `price.currency`（默认 CNY）再 sync。  
3. 从 `uncovered_resorts.yaml` 移除留寿都别名；ingest dry-run → 增量灌入。  
4. 前端按 `currency` 显示，禁止把 JPY 数字写入「元」文案。

## 完成记录

- 2026-10-11：落地行程卡层 `currency`（默认 CNY）；新增本执行计划；万龙演示文案已抛光。
