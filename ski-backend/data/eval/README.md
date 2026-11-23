# 检索评测集说明

## 两套题集

- `golden_set.yaml`：原人工维护基线，39 道检索题、6 道拒答题，含分层题。报告只认 `last_report.md`。
- `generated_golden_set.yaml`：由雪场 YAML 确定性生成的扩展覆盖集，当前 102 道检索题、4 道拒答题。覆盖地区、来源口径总数、分级统计、部分命名雪道与索道。生成题仍需人工复核，不是端到端回答忠实度评测，也不替代人工题。
- `generated_baseline.md` / `generated_report.md`：同一扩展题集改动前后的检索结果。两套题集的百分比不可直接互比；生成题满分不代表任意自然语言问题都能正确回答。

## 复现

在 `ski-backend` 下用已配置的 Python 环境运行：

```powershell
python -m scripts.gen_golden_set
python -m scripts.evaluate
python -m scripts.evaluate --cases data/eval/generated_golden_set.yaml --output data/eval/generated_report.md
```

生成器不覆盖人工题集。来源总数与分级合计有差异时，总览必须并列说明，不可将分级合计直接称为总数。跨雪场补召回与未收录守卫另由 `tests/test_typed_recall.py` 验证。

## 人工挑战题

`challenge_set.yaml` 包含 6 道检索题、2 道拒答题，覆盖来源总数分歧、混合数量/价格、押金退还、命名雪道，以及未收录/已收录比较。`challenge_baseline.md` 和 `challenge_report.md` 保存修复前后结果。

```powershell
python -m scripts.evaluate --cases data/eval/challenge_set.yaml --output data/eval/challenge_report.md
```

`require_groups` 要求各组证据分别在 top-k 中出现，`expected_resort` 要求全部返回文档属于指定雪场；任一失败，该题不计命中。挑战题的 hit@1 是“首条满足 expect_any，且 top-k 通过全部覆盖/隔离约束”，不是单条文档同时回答所有子问题。此题集规模仍很小，不代表端到端问答准确率。未覆盖真实错别字、多轮记忆或模型最终答案引用验证。

数量＋价格的明确分句问题会分别检索、轮流取证据后按 chunk_id 去重；比单问题多一次检索，保留元数据和全局未收录守卫，未修改 embedding 或 YAML。

## 回答级人工挑战

```powershell
python -m scripts.eval_e2e --cases data/eval/e2e_challenge_set.yaml --output data/eval/e2e_challenge_report.md
```

4题真跑 Agent：混合数量/票价、将军山来源分歧、未收录拒答和同线程多轮追问。`e2e_challenge_baseline.md` 保留首次运行，`e2e_challenge_report.md` 为最近运行，不覆盖原始 `last_e2e.md`。引用白名单与 middleware 同口径：本轮取得证据才可使用有限回看窗口。期望组是词面完整性，grounding 是现有数字检查，不是语义忠实度评分。

逐题回复摘要有界且对常见凭据、邮箱、手机、thread做匿名化；异常只保存类型。CLI结束关闭专属SQLite线程，通用run_all不关闭Web连接。多轮 token 只统计各轮新消息并累计，耗时累计首问和末问。首次基线生成于计量修复前，故不能直接比较两份报告的多轮耗时/token总数。

价格工具精确项名无结果时补查同一雪场候选价目，明确标注“请求项未命中”，不得直接用其他价格替代。真实小样本表现有波动，来源校验仍可降级；不以一次冒烟宣称普遍准确率。

## 仅漏引用重答实验

`CITATION_RETRY_MISSING_ONLY` 默认 False；只有已有合法编号且唯一校验原因是“回答没有标注任何来源编号”才重答一次。无证据、错误编号、无据数字仍直接降级；重答结果重新走原校验，有重试标记时不会再次重试。不自动拼引用，不修改默认 `.env`。旧 `CITATION_RETRY_ON_FAIL` 全错误实验开关保持原语义，实验时必须设 False。

`e2e_missing_retry_off.md` / `e2e_missing_retry_on.md` 是同一4题分别关闭/开启的真实运行。开关通过单进程环境变量指定。关闭3/4、开启4/4只是小样本观察，模型有波动，不是统计显著结论；开启组日志显示将军山漏引用实际触发一次重答后通过。总输入 token 88646→94901，P50 11.9s→16.4s，不能忽略额外成本。

```powershell
$env:CITATION_RETRY_ON_FAIL="false"
$env:CITATION_RETRY_MISSING_ONLY="true"
python -m scripts.eval_e2e --cases data/eval/e2e_challenge_set.yaml --output data/eval/e2e_missing_retry_on.md
Remove-Item Env:CITATION_RETRY_MISSING_ONLY
Remove-Item Env:CITATION_RETRY_ON_FAIL
```

中间件安全断言统一维护于 `scripts/test_middleware.py`：默认关闭、空证据、错误编号、无据数字、不二次重试，以及假模型驱动真Agent图的恢复引用/新增事实拦截。

## 重复稳定性实验

```powershell
python -m scripts.eval_retry_repeated --rounds 2
```

每轮独立子进程，第一轮 off→on、第二轮 on→off；继承同一模型/档案配置且显式关闭全错误重答，唯一会话由原评测生成。默认2轮=16道题次（多轮题另有首问），最多5轮，真实API会消费token。单子进程600秒超时。默认开关和 `.env` 均不改变。

`e2e_retry_repeated.md` / `.json` 保留每轮全部评分，而不是挑最好的一次。通过同时要求回答非空、期望完整、引用合法、数字检查通过、未降级；事实题还要求有引用、价格题有时效提示，拒答题要求明确拒答。原始日志与单次报告仅存本机忽略目录 `.test-deps/`；汇总JSON仅限定评分/耗时/token字段，无回复、工具或异常原文。不覆盖正式基线。

样本仍只有四题，重复运行衡量的是这些题的波动，不能代替更广泛人工题或语义忠实度评测。

本次2轮关闭4/8、开启7/8；平均题耗时12.9s→14.8s，输入token177292→188661。开启组剩余未通过题已有引用与61/48两数字，但用“两个不同的数据”说明差异，没有命中分歧组词表。保留原词面分数，不事后改词表追求满分；下一轮需独立复核释义覆盖。零降级不等于全部回答正确。

## 缆车专用档案工具

`get_resort_lifts` 只接受准确的单座雪场id/名称/别名，从策展YAML读取；不查询运行期数据库、不写Chroma。非空表复用 `ResortParser` 的 lift 文档及来源/日期/可信度，不保证全量、实时开放或缺失字段。空表返回 C 级“本库档案字段检查”证据，日期是档案复核日期而非运营日期；只证明本库缺表，不证明现实没有缆车。未识别/多雪场/路径输入不借其他场资料。工具独占E601起编号段，模型选路不使用雪道统计。

`e2e_lift_tool_report.md` 保存首次4题复测；太舞工具调用已正确且草稿说明缺档，但漏引用仍按原校验降级。单题提示复测独立保存在 `e2e_lift_missing_followup.md`，不覆盖首次失败。新工具解决证据选路，不代表模型引用稳定。单题复测回答已有E601且语义可接受，但v2反例正则误伤“不代表现实中没有缆车”。原报告hit=False保留不回填；v3收窄为明确“太舞没有缆车”的断言，并增加否定句保护测试，未再消费API重跑v3。

## 自动门禁与有限对象校验

单次与重复评测共用 `scripts.eval_policy.passed`：空回答/异常/降级/无据事实/错误引用/期望失败均不通过。普通事实题必须有引用，价格题必须提醒时效；`refusal` 和 `*_missing` 可无引用但必须明确拒答。`hit=None` 保留无期望旧题兼容，不代表人工语义验收。

两种CLI支持 `--fail-on-error`，默认关闭，打开后先写报告（及请求的metrics）再以1退出。默认关闭仅表示脚本执行成功，不能当所有回答通过。发布检查不同：`scripts.pre_open_source_check` 总是有阻断退出1、通过退出0。已确认CI占位值精确放行，不放行所有ci前缀秘密。

```powershell
python -m scripts.eval_e2e --cases data/eval/e2e_independent_set.yaml --output data/eval/e2e_gate_v3_report.md --fail-on-error
python -m scripts.eval_retry_repeated --rounds 2 --fail-on-error
python -m scripts.pre_open_source_check --quiet
```

`_check_facts` 新增有限条数对象检查：明确阿拉伯数字“22条缆车”不能仅由“22条雪道”支持，反向亦然；两对象都有同数字证据则允许。与原current gate/历史窗口相同，明确否定纠正句不误拦。只处理雪道与缆车/索道/魔毯数量，不检查跨雪场归属、价格项目或长度归属，也不是全面语义忠实度校验；未知对象和中文数字仍需人工复核。对象错配不会启用仅漏引用重答。

## 用户雪场约束的查询参数守卫

查询工具在证据信封执行前检查本轮最后用户原话。只在明确唯一已收录雪场时，把未知工具参数规范成该雪场ID；不依靠拼音/相似度猜名。精确合法同场参数保持不变，明确其他已收录场或未收录场参数拒绝并不执行工具。明确多场时，仅放行请求集合中合法单场参数；未知参数不猜、范围外已知参数拒绝。本轮未提场但显式“它/该雪场/票价呢”等省略时，只查看紧邻上一用户轮，唯一明确已收录场才承接；多场、未知、闲聊打断均要求澄清，不越过多轮找旧场。当前明确换场优先，承接后仍需本轮工具取证，不放宽空证据门。仅排除明确“不要拿/用…替代/补”的整段从句，普通比较句不排除。写行程/画像及外部工具不改。

首次 `e2e_resort_guard_report.md` 自动验收4/4但人工仍发现北大湖范围歧义；用户同时说北大湖和禁止借万龙时被判多场，守卫未执行，日志仍查dahulushui。随后补禁止替代从句边界，单题复测独立写入 `e2e_resort_guard_followup.md`，不覆盖首次报告。自动通过不等于语义修复，不事后改分。规则只在已加载中间件的Agent查询路径生效，直接调用工具仍须准确参数。

## 多场证据及对话挑战

同一工具查询多雪场时，依据策展sorted ids为每场分配子段 `base+10000+(index+1)*1000`，防万龙和太舞都返回E201。单场保持原段，known+unknown请求中已知场亦分段，未知场不制造证据；同工具同场反复不同筛选仍可能复用段。策展名册变化会改变多场编号，编号是会话证据定位而非永久档案ID。

`e2e_dialogue_set.yaml` 覆盖北大湖省略价格、万龙/太舞逐场高级道、万龙切换太舞缺缆车。v1首次2/3，换场缺表答复语义可接受但反例正则误伤“并不意味着太舞没有缆车”；原报告 `e2e_dialogue_report.md` 保留。v2收窄句首明确无缆车断言并增加否定保护，新报告独立 `e2e_dialogue_v2_report.md`，不直接改旧分。词面判定仍非全面语义评分。

## 本轮运行边界

全量 upsert 后，旧 Chroma 集合 `ski_kb` 出现 HNSW 索引加载错误。保留旧集合，以同一嵌入模型和分块参数新建 `ski_kb_quality_v2`，写入 538 条文档后完成两套评测。本机 `.env` 已切换到新集合（不提交），运行中的后端需重启。此次未调整嵌入模型或分块参数。

当前模板题还未覆盖复杂混合意图、多轮省略、真实用户错别字、以及完整答案的引用忠实度；下一轮应优先补人工挑战题，而不是为了扩大数字重复生成简单题。

## 公开快照说明（2026-11-23）

本目录包含 62 条人工维护的检索/拒答用例（`golden_set.yaml`）以及其他评测集与历史报告。
这些用例中的预期子串是针对作者本地完整雪场档案编写的。**完整档案未包含在本公开仓库中**，因此直接运行 `python -m scripts.evaluate` 不能复现历史 hit@k / MRR，也不要把历史报告里的满分检索结果写成问答准确率 100%。
