## 这个 PR 做了什么

<!-- 一句话说清：解决什么问题，而不是"改了哪些文件" -->

Closes #<!-- Issue 号；没有 Issue 就写清动机 -->

## 改动类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 数据（新增/修正雪场档案）
- [ ] 文档 / 测试
- [ ] 重构（无行为变化）
- [ ] 架构级改动（需先在 Issue 里讨论过）

## 我跑了哪些门禁（勾选并贴结果）

- [ ] `python -m pytest`（离线用例）
- [ ] `python -m scripts.evaluate` — 改了检索/引用相关逻辑时**必填**

```
改前：hit@1 __% / hit@3 __% / hit@5 __% / MRR ___ / 拒答 __/6
改后：hit@1 __% / hit@3 __% / hit@5 __% / MRR ___ / 拒答 __/6
```

- [ ] 三个离线套件（`test_middleware` / `test_provenance` / `test_chat_stream`）
- [ ] `python -m scripts.pre_open_source_check`（准备发布时）

## 数据类改动（改动 `data/` 时必填）

- 来源链接：
- 该来源自述的原始出处：
- `data_status` 判定：`verified` / `corroborated` / `transcribed` / `sample`
- 是否与其他来源冲突（冲突时请保留双方口径）：
- [ ] 已同步更新 golden set 用例（新增雪场时必须补：覆盖题 + 跨雪场隔离题 + 拒答题）

## 自查

- [ ] 没有提交 `.env` / `chroma/` / `logs/` / `*.db` / 实拍图原图
- [ ] 没有在代码、日志、截图、提交信息里出现真实密钥
- [ ] 新增依赖的许可证不是 AGPL/GPL（见 `DEPENDENCIES.md`）
- [ ] 注释与文档保持中文风格（与现有代码一致）
- [ ] 改动是"小而聚焦"的；无关重构另开 PR
