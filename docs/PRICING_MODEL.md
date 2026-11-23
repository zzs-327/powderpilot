# 价格模型设计：多币种、计价口径与适用期（草案）

> 状态：批次 A–E 已完成。代码在分支 `feat/pricing-model`（未合并、未推送）。2026-10-11 现有 MySQL 已升到 `0002`，并同步了 `rusutsu`：10 条日元价格，`budget_usable` 全部为 false。Chroma 增量写入 20 条，集合共 561 条。`check_schema` 仍因原先 4 组注释/可空性差异退出 1。前端未在 HBuilderX 里运行。
> 目的：让留寿都（JPY）这类目的地的费用能进入正式数据，同时不破坏现有人民币雪场。
> 章程红线：未知费用 ≠ 0；已知小计 ≠ 全行程预算；不同币种不相加、不擅自换算；候选 ≠ 正式数据。

## 一、为什么要先改模型

留寿都已核到的费用（见 [RUSUTSU_COST_REVIEW.md](collection/RUSUTSU_COST_REVIEW.md)）每一条都带着现有模型装不下的信息：

| 候选 | 金额 | 现有模型装不下的部分 |
|---|---|---|
| 新千岁机场→留寿都接驳 | 5000 JPY | 币种；**按座单程**；适用 2026-11-28～2027-03-30；含税；退改梯度 |
| 英语私教 2 小时（常规季） | 51000 JPY | **按教练**（1–5 人共用），不是每人；不含器材与缆车票 |
| 成人网上 5 小时票 | 11600 JPY | 适用季节；税费 unknown；首次过闸起连续计时 |
| 单板全套租赁 1 日 | 22300 JPY | 独立价目行（含板/鞋/雪服/头盔/手套/护目镜），不能由组件相加；雪季与税费 unknown |

硬塞进现有字段，结果只会是"5000 元"、"每人 51000"或"已知小计 = 预算"，正好踩中红线。

## 二、现状问题（逐处定位）

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `models/price.py` | 只有 `price:int` + 自由文本 `unit`，无币种、计价对象、适用期、含税、包含/不包含 | 外币与口径无处落地 |
| 2 | `scripts/sync_to_mysql.py` `int(price.get('price') or 0)` | YAML 漏写金额时**写成 0** | 违反"未知 ≠ 0"；卡片侥幸靠 truthy 过滤挡住，但 RAG 与接口会看到 0 |
| 3 | `rag/parser.py` 价格分块 `… 元/…` | 文本**硬编码"元"** | JPY 入库后被检索、引用成"5000 元" |
| 4 | `agent/card_builder.py` `pick_budget` | 靠品名关键词"雪票/租赁"和"平日/周末/节假日"挑价 | 表达不了日期区间；交通、教学、住宿永远进不了卡 |
| 5 | 同上，"同卡只保留首条币种" | 其他币种条目被**静默丢弃** | 用户看不到被丢掉的费用，也不知道丢了 |
| 6 | `ResortProvenance.status_of` | `data_status` 只到"价格"类型级 | 同一雪场里已核验与待核验价格无法分档 |
| 7 | `ski-front/utils/media.js` `budgetSummary` | 不分币种直接相加 | 前端会把 JPY 与 CNY 加在一起 |
| 8 | `updated_at` / `expire_at` | 只是**录入日期与保鲜期**，不是报价适用期 | 把"录入 90 天内"误当"报价有效" |

## 三、字段设计

原则：**只做加法**。新列在库里**全部可空、无服务器默认值**（给有数据的表加带默认值的非空列有风险，迁移测试也要求无服务器默认），空值的含义统一由 `rag/pricing.py` 解释。老雪场 YAML 不改也能照常同步，行为不变。

| YAML 字段 | MySQL 列 | 类型（均可空） | 空值含义 / 说明 |
|---|---|---|---|
| `currency` | `currency` | String(3) | 空按 `CNY`；ISO 4217 大写；不做汇率换算 |
| `category` | `category` | String(16) | `lift_ticket` / `rental` / `lesson` / `transfer` / `lodging` / `other`；空时回退老的品名关键词推断（仅兼容老数据）。餐饮、储物柜、停车暂归 `other` |
| `unit_basis` | `unit_basis` | String(24) | `per_person` / `per_instructor` / `per_room` / `per_seat_one_way` / `per_set` / `per_item` / `unknown`；`unit` 仍保留为展示文本 |
| `valid_from` / `valid_to` | 同名 | Date | **报价适用期**，与保鲜期 `expire_at` 分开 |
| `season` | `season` | String(32) | 展示用，如 `2026-27 常规季` |
| `tax_status` | `tax_status` | String(8) | `included` / `excluded` / `unknown`；空表示老数据未记录 |
| `includes` / `excludes` | 同名 | JSON | 短语列表；未逐项说明写 `unknown`，不要留空冒充"无" |
| `conditions` | `conditions` | JSON | 预约、退改等短条款；不保存长正文 |
| `budget_usable` | `budget_usable` | Boolean | 空按"可计入"，保持老数据行为；留寿都条目经人工复核后逐条显式填写 |
| `source_url` | `source_url` | String(512) | 单条原页链接；现有 `source`（128）保留为出处名称 |

`0002` 同时把 `price.price` 的列注释从"单位：元"改为"原币金额整数；币种见 currency"（仅 MySQL）。模型注释已改，若不随迁移更新，MySQL 上会多出一处注释漂移。
| `data_status` | `data_status`（已有） | — | **允许单条覆盖**类型级默认值（只改同步逻辑，不改表） |

**金额未知怎么办**：`price` 保持 NOT NULL，不放宽。YAML 里金额未知的项**不写成价格行**，同步时缺金额直接报错退出，不再兜底成 0。"缺了哪些费用"由行程卡按 `category` 覆盖情况推出（见下文第四节），不需要用空金额占位。

## 四、行为规则

### 同步（`scripts/sync_to_mysql.py`）

- 校验：`price` 为正整数；`currency` 为 3 位大写字母；`valid_from ≤ valid_to`；`category`、`unit_basis`、`tax_status` 必须在枚举内。不合规**整场失败并报出行号**，不跳过、不兜底。
- 单条 `data_status`、`source`、`source_url` 优先于类型级默认值。
- 不做任何币种换算。

### RAG 分块（`rag/parser.py`）

- 文本改为 `{金额} {币种}/{unit}`，CNY 仍显示"元"保持老文本不变；外币显示 ISO 码，如 `5000 JPY/单程每座`。
- 追加适用期、含税、按教练/按房等口径短语，供检索与引用使用。
- ⚠️ **老雪场若只是 CNY 且无新字段，分块文本应逐字不变**，这样不会让现有 541 个 chunk 全部变化。有一处文本变化，就按 AGENTS 规则重建向量库并跑 `scripts.evaluate`。

### 行程卡（`agent/card_builder.py`、`schemas/trip.py`、`agent/card_check.py`）

- **选价**：有 `category` 时按类别挑；出行日期已知且行有 `valid_from/valid_to` 时，只取覆盖该日期的行；日期不在任何区间内则**不取**（不回退到其他季）。无 `category` 的老数据继续走现有关键词与日期档逻辑。
- **只把** `budget_usable = true` 且非 sample 的行计入小计。`budget_usable = false` 的已知单价仍列在明细里，`counted` 为 false，不进入任何币种小计；sample 仍不列示。
- **分币种小计**：`BudgetBreakdown` 新增 `subtotals: [{currency, total}]`；**不再丢弃其他币种条目**。`total` / `currency` 保留为"首个币种"的小计以兼容老前端，单币种卡片行为不变。
- **缺失清单**：新增 `missing: [str]`，按本次行程需要的类别（雪票必需；未自带装备需租赁；多日需住宿；有机场/城市出发需交通；新手建议教学）减去已覆盖的类别，生成"住宿费用待确认"等条目。
- **完整性**：新增 `complete: bool`，当前一律为 `false`，在交通和住宿都能结构化计算之前不放开。
- **口径提示**：`per_instructor` 条目的 note 写"按教练计，1–5 人共用，非每人价"；`per_seat_one_way` 写"单程，往返需另计"。**不按人数、天数自动乘算**（保持现有约定）。
- `card_check.py` 校验改为"每个币种的 subtotal 等于该币种明细之和"，并同步更新 `tests/test_card_check.py`（AGENTS 要求行程卡不能成为没校验的旁路）。

### 接口与前端

- `GET /api/v1/resorts/{resort}/prices` 返回新增字段；`ResortService.get_prices` 去掉当前硬编码的 `'currency': 'CNY'`，改为读真实列。
- `budgetSummary` 按币种分组，返回 `subtotals`；`trip.vue` 每个币种各列一行小计，下面单独展示"缺失费用"清单，标题统一为"已知费用小计"。
- 雪场详情页价格区展示：金额 + 币种、计价对象、适用期、含税、包含/不包含。整表里只要有 `budget_usable=false` 的行，列表上方提示这些单价不能加总成完整旅行总价。有安全的 `https` 来源地址时显示「官网原文」，只打开原页，不下载图片或视频。
- 查价工具在有不能计入预算的行时，于证据前加一句总提醒。使用条件附在工具证据末尾，不写入 RAG 分块。条目名对不上时，先按「接驳 / 雪票 / 租赁 / 教练 / 住宿」缩小到同类价目，不再整表摊开。问双板租赁而库里只有单板价时，直接说明没有该板型价格，不用单板代替。
- 板型有两道闸：查价参数按用户原话的板型纠正（`_query_board_guard`）；回答侧由引用校验再查一次——用户只点名一种板型时，回答里出现「只见于另一种板型价格行」的金额，且所在分句没有明说那是另一种板型的价格，整条降级为「暂无可靠来源」。明说「单板 22300 日元，不能代替双板」属于如实说明，放行。
- 行程卡和雪场详情带上该雪场 YAML 里已经写明的避坑（双板无一日价、两家酒店不能混用权益等），并保留来源。问住宿或双板租赁而没有对应价格行时，查价工具附上这些说明，不用其他类别或其他板型的价格补答。
- 引用校验把证据里的 `JPY` 与回答里的「日元」当成同一单位；「5000日元」不会被当成「5000元」。日元金额只核对原文，不参与加减乘，因此不能把两笔 5000 自动说成 10000 日元。

## 五、数据库迁移与上线顺序

新增 revision `0002_price_pricing_fields`，**只加列，不改已有列**。

前置条件：现有库的 4 组 / 7 项漂移未处理，目前**禁止 stamp**（见 [DATABASE_MIGRATIONS.md](DATABASE_MIGRATIONS.md)）。因此顺序必须是：

1. 完整备份现有 MySQL，并在隔离库验证恢复。
2. 在恢复副本上处理 4 组漂移，`check_schema` 退出 0 后，由负责人 stamp `0001_initial`。
3. 在恢复副本上 `upgrade 0002`，跑 `sync_to_mysql` 与 pytest，确认老雪场价格逐行一致。
4. 同样步骤作用于主库。
5. **之后**再合并依赖新列的代码。

⚠️ 关键风险：`models/price.py` 一旦加了新列，ORM 查询就会 SELECT 这些列，而启动时的 `create_all` **不会给已有表补列**。代码先于迁移上线会导致价格查询直接报错。所以模型改动与迁移必须同批上线，且迁移先跑。

已实测（2026-10-11）：迁移前，分支代码连现有库报 `Unknown column 'price.currency'`。迁移后完整 pytest 为 290 passed。`sync_to_mysql` 在缺列时会直接退出，不会进入同步。

在主库接管完成前，开发与测试用临时 SQLite / 新空 MySQL 验证，**不往现有库写任何 JPY 数据**。

## 六、留寿都 YAML 写法示例（仅示意，不是待入库数据）

```yaml
prices:
  - item: 新千岁机场→留寿都 冬季接驳·网上预约
    price: 5000
    currency: JPY
    category: transfer
    unit: 单程每座
    unit_basis: per_seat_one_way
    valid_from: 2026-11-28
    valid_to: 2027-03-30
    season: 2026-27 冬季
    tax_status: included
    includes: [机场至留寿都单程一个座位, 税]
    excludes: [unknown]
    conditions: [出行前 7 日 24:00 前网上预约并信用卡预付, 前一天取消收 40%]
    budget_usable: false   # 人工复核前保持 false
    source: 留寿都官网·新千岁机场接驳页
    source_url: https://rusutsu.com/en/access-shuttle-bus-new-chitose-airport/
    data_status: verified  # 仅在人工复核后填写
```

入 YAML 的门槛不变：只有 `body_checked` 且有人工复核笔记的候选才能写入；金额、日期、单位任何一项不明确就**不写成价格行**，在资料正文里记为"待确认"。

## 七、测试与评估清单

- `tests/test_card_builder.py`：JPY 单币种出卡；CNY+JPY 混合出两条小计且不丢条目；日期在适用期外不取；`per_instructor` 不乘人数；`budget_usable=false` 不入小计；老万龙用例结果不变。
- `tests/test_card_check.py`：分币种小计校验；篡改任一币种小计被拦截。
- 同步：缺金额、非法币种、日期倒置均整场失败；老 YAML 同步结果逐行不变。
- 迁移：`0001 → 0002` 升级、降级、模型零差异（沿用 `tests/test_migrations.py` 套路）。
- 解析：CNY 老数据分块文本逐字不变（防止全量 chunk 漂移）；JPY 文本不含"元"。
- 前端：`budgetSummary` 分币种；`test_media.mjs` 增加混合币种用例。
- 评估：若分块文本有变化，重建后跑 `scripts.evaluate`，并对比 hit@1/3/5、MRR、拒答与分层；留寿都入库时另补覆盖题、跨场隔离题（防止串二世古）和拒答题（如"留寿都双板租赁价"）。

## 八、建议的实施切分

| 批次 | 内容 | 是否碰现有库 |
|---|---|---|
| A | 模型 + 迁移 0002 + 同步校验 + 解析文本（含测试） | 否，仅临时库 |
| B | 行程卡分币种小计、缺失清单、card_check（含测试） | 否 |
| C | 接口字段 + 前端分币种展示与缺失清单 | 否 |
| D | 现有库备份 → 漂移处理 → stamp → upgrade | **是，需负责人批准** |
| E | 留寿都候选人工复核 → `rusutsu.yaml` → 移出未收录名单 → 同步 → 增量入库 → 评估 | 是（经 D 之后） |

A–C 可以先在分支上完成并通过测试，D 完成前不合并 A 中的模型改动（见第五节风险）。

## 九、待决问题

1. `category` 枚举是否够用？餐饮、储物柜、停车要不要单列，还是先归 `other`？
2. `budget_usable` 默认值：老数据默认 `true` 以保持现状，还是默认 `false`、逐场显式放开？（草案取 `true`，避免万龙演示回退。）
3. 是否需要"汇率换算后参考价"？草案**不做**；如果要做，必须记录汇率来源与日期，并与原币金额分开展示。
4. 区域型目的地（二世古联合、白马谷）的成员雪场层级不在本次范围，留待留寿都样板通过后单独设计。
