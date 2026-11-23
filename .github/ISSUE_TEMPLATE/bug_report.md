---
name: Bug 报告
about: 报告一个可复现的问题（接口异常 / 检索漏召 / 引用误伤 / 前端异常）
title: '[Bug] '
labels: ['bug']
---

## 现象

<!-- 你看到了什么？把原始输出贴上来，不要只写"不工作" -->

## 复现步骤

1.
2.
3.

## 期望行为

## 环境

- 运行方式：本地 Python / Docker Compose
- Python 版本：
- `python -m scripts.check_env` 的输出（**请删掉 key 相关行**）：

```
（粘贴 here）
```

## 如果是检索 / 引用相关问题

<!-- 这两类问题必须带证据，否则没法定位 -->

- 问题原文（用户提问）：
- 实际召回/回答：
- `python -m scripts.evaluate` 的指标有没有变化：

```
（粘贴 here）
```

## 补充信息

<!-- 日志片段（logs/backend.log）、截图、相关 Issue 链接 -->
