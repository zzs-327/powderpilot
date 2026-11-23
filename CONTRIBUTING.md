# 贡献指南（CONTRIBUTING）

感谢你愿意为 PowderPilot（滑雪情报官）做点事。这是一份**可溯源**的滑雪情报 Agent，
所以本项目的贡献门槛和普通项目不太一样：**代码可以改，数据不能凑**。

## 一、我们欢迎什么

| 类型 | 具体形式 | 特别要求 |
|---|---|---|
| 🐛 Bug 修复 | 检索漏召、引用校验误伤、接口异常 | PR 里写清复现步骤；涉及检索/引用要附评测结果 |
| ✨ 功能 | 检索质量、行程规划、前端体验 | 先开 Issue 讨论方向，避免做完了方向不对 |
| 📊 数据 | 新增雪场档案、修正错误、补充来源 | **必须带可复核的来源**（见第二节，这是硬门槛） |
| 📚 文档 | 纠错、补充踩坑记录、翻译 | 欢迎；本项目很多结论都来自踩坑记录 |
| 🧪 测试 | 补 golden set 用例、离线断言 | 见"必须跑的门禁" |

## 二、数据贡献的硬门槛（本项目最看重的部分）

数据是全项目的地基，规则只有一条：**宁可不写，也不要写没有来源的东西。**

1. **每条数据都要能被人复核**：填 `primary_source` + `primary_url`；
   转引的要写 `stated_source`（该页面自己标注的原始出处）。
2. **不许提档**：转引自票务聚合页的数据，`data_status` 只能是 `transcribed`，
   哪怕它自称"来自官方价目表"。**只有官网/官方手册直采才是 `verified`。**
3. **源之间冲突时不猜**：两个来源给出不同的雪道总数，就把两个口径都留在档案里
   （并在 `caveat` 写清分歧），**不要选一个"看起来更权威"的当成总数**。
4. **源没给的字段就留空**：不要用"看起来差不多"的值填（比如拿落差估算坡度）。
5. **`sample` 只能用于演示**：标注 `sample` 的数字不得对外引用。
6. **不接受**：来源不明、去掉了来源标注、无法被人复核的数据。

> 参考 `ski-backend/data/resorts/_template.yaml`：每个字段的填写口径都写在注释里。
> 新增雪场后跑 `python -m scripts.sync_to_mysql && python -m rag.ingest`，
> 并**同时补 golden set 用例**（覆盖题 + 跨雪场隔离题 + 拒答题）。

### 本轮资料采集约定

目的地名单与批次以 [ROADMAP.md](ROADMAP.md) 为准：20 个规划池、首批 12 个。规划项不等于已收录；先提交一处完整样板，再按相同口径补充。不因雪场名气或票价高低预设性价比。

- **费用资料**：保留适用日期/雪季、币种、按人/按单口径、时长、包含项与额外费用；区分雪票、租具、教学、交通、住宿。查不到就标待确认，历史价格不能冒充当前报价。
- **目的地归属**：区域、成员雪场与联票范围分开记录，不把不同成员的数据拼接成同一个雪场。
- **图片候选**：每个目的地先收集 4–6 张，附原始链接、所属雪场、作者/机构、日期（未知注明）及授权情况。仅能公开访问不代表允许下载分发；不把宣传图冒充实拍，不从旧图推断实时雪况。
- **视频候选**：首批 8–12 条按单双板和学习阶段共用，附原平台链接、标题、作者、主题与适用水平；实际核验后再写内容要点或时间戳，未观看不能声称已核验。视频不替代现场教学，不搬运视频本体。
- 用户可提供链接、截图、PDF、自摄或获授权素材，由维护者核实并整理；截图和候选素材不自动成为 verified 数据，公开分发仍需遵守授权要求。

## 三、必须跑的门禁（PR 会被要求出示结果）

```bash
cd ski-backend

# 1. 通用门禁：pytest（接口契约 / 收录范围守卫 / 行程卡校验）
python -m pytest
# ⚠️ 部分用例需要 MySQL：中间件套件用假模型驱动真 Agent 图，工具会真查库。
#    没库时 pytest 会跳过这些用例（不报错），但 CI 里起了 MySQL 所以会真跑。

# 2. 改了检索逻辑（分块/权重/embedding/重排）→ 必须跑评测并贴数字
python -m scripts.evaluate      # 关注 hit@1/3/5、MRR、拒答正确率、分层命中率

# 3. 改了 Agent 引用校验 / 中间件 → 三个离线套件
python -m scripts.test_middleware   # ⚠️ 需要 MySQL（假模型驱动真 Agent，工具真查库）
python -m scripts.test_provenance
python -m scripts.test_chat_stream

# 4. 改了雪场档案（YAML）
python -m rag.ingest --dry-run  # 先体检解析与 chunk 数
python -m scripts.sync_to_mysql && python -m rag.ingest

# 5. 准备发布 / 开公开 PR
python -m scripts.pre_open_source_check
```

> ⚠️ 不要凭感觉调参：任何"我觉得这样更好"的检索改动，都要用 `scripts.evaluate` 的数字说话。

## 四、提交规范

- **提交信息**：延续仓库风格（Conventional Commits + 中文说明），例如
  `fix(rag): BM25 关键词闸门漏掉两字查询`、`feat(agent): 行程卡字段级校验`
- **一次提交只做一件事**；重构与行为变更分开提交
- **不要提交**：`.env`、`chroma/`、`logs/`、`*.db`、实拍图原图（都已在 `.gitignore`）
- **绝不**在代码、日志、截图、提交信息里出现 API key / 数据库密码 / JWT secret；
  配置一律走 `config/config.py` 的 Settings

## 五、Issue / PR 响应承诺

| 事项 | 承诺 |
|---|---|
| 新 Issue 首次响应 | **48 小时内**（哪怕只是"收到了，正在看"） |
| PR 首次 Review | **7 天内** |
| 安全类问题 | **72 小时内**（走 [SECURITY.md](SECURITY.md)，不要开公开 Issue） |
| 数据类 PR | 需要来源可复核；无法复核会说明原因后关闭 |

## 六、许可与合规

- 提交代码即表示你同意以本仓库的 [Apache-2.0](LICENSE) 授权
- **数据不随代码授权**：`ski-backend/data/` 的授权另见 [DATA_LICENSE.md](DATA_LICENSE.md)
- **新增依赖注意许可证**：`AGPL/GPL` 类传染性许可**不得**进入核心依赖
  （`pymupdf` 就因为 AGPL-3.0 被挪到 `requirements-optional.txt`）；
  `python -m scripts.pre_open_source_check` 会自动检查这一点

## 七、行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
