"""Agent 核心（本项目重点二）。

对应计划书 v0.4 第五节：
    state.py       行程状态机（TripState + 追问判定）
    evidence.py    证据信封（所有工具返回值的统一结构）
    prompts.py     系统提示词（画像注入 + 溯源约束）
    tools.py       4 个工具：search_resort_kb / get_resort_price / get_weather / update_profile
    middleware.py  证据信封包装 + 引用校验 + 消息裁剪
    agent.py       用 create_agent 装配主智能体

实现顺序（Day2）：装配 → 画像 → 状态机 → 证据信封与引用校验 → 结构化行程卡
"""
