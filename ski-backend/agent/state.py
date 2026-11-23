"""行程状态机与请求上下文。

为什么需要显式状态：
    在提示词里让模型"记得问"不可靠；显式 state 让"信息不全就追问"变成代码逻辑。
    工具通过 ToolRuntime 读 state、返回 Command 写 state（见 agent/tools.py）。
"""
from typing import Any

from langchain.agents import AgentState
from pydantic import BaseModel


class UserContext(BaseModel):
    """每次请求的运行时上下文（放 context 而不是 state）

    state 会进 checkpoint、会被工具改写；用户身份是"不可变的请求参数"，
    所以走 context_schema，工具里用 runtime.context.uid 取。
    """

    uid: int | None = None


class TripState(AgentState):
    """行程规划状态。字段为 None 表示"还没问到"

    注意：不要在这里放 ready 之类的"派生字段"。
    模型会在一轮里并行调用多次 set_trip_info（用户一次说完所有要素），
    每个工具都写 ready 会触发 LangGraph 的
    InvalidUpdateError: Can receive only one value per step。
    需要时用 TripHelper.is_ready(state) 现算。
    """

    resort: str | None      # 目标雪场（可留空，由 Agent 推荐）
    date: str | None        # 出行日期
    days: int | None        # 天数
    people: int | None      # 人数
    budget: int | None      # 预算（元）
    board: str | None       # 单双板


# 行程要素：字段名 -> (中文标签, 类型)。set_trip_info 工具按这张表解析与校验
TRIP_FIELDS: dict[str, tuple[str, type]] = {
    'resort': ('目标雪场', str),
    'date': ('出行日期', str),
    'days': ('滑几天', int),
    'people': ('几个人', int),
    'budget': ('人均预算（元）', int),
    'board': ('单双板', str),
}

FIELD_LABELS: dict[str, str] = {
    'resort': '目标雪场',
    'date': '出行日期',
    'days': '滑几天',
    'people': '几个人',
    'budget': '人均预算',
    'board': '单板还是双板',
}

# 必填信息：缺一个就追问，凑齐才出方案
REQUIRED_FIELDS: tuple[str, ...] = ('date', 'days', 'people', 'budget', 'board')


class TripHelper:
    """行程状态工具：字段解析、缺失判定"""

    @staticmethod
    def parse_value(field: str, value: str) -> Any:
        """把用户口语化的值转成 state 里的类型

        例："2 人" -> 2；"三千" 这种中文数字不处理（让模型先换算成阿拉伯数字）。
        转换失败抛 ValueError，工具会把它变成给模型的提示。
        """
        import re

        _, kind = TRIP_FIELDS[field]
        if kind is str:
            return str(value).strip()
        digits = re.findall(r'\d+', str(value))
        if not digits:
            raise ValueError(f'无法从"{value}"解析出数字')
        return int(digits[0])

    @staticmethod
    def missing_fields(state: dict[str, Any]) -> list[str]:
        """返回还缺哪些必填信息（用于生成追问话术）"""
        return [field for field in REQUIRED_FIELDS if not state.get(field)]

    @staticmethod
    def is_ready(state: dict[str, Any]) -> bool:
        """信息是否齐全"""
        return not TripHelper.missing_fields(state)


# ---------------- 兼容旧调用 ----------------
parse_trip_value = TripHelper.parse_value
missing_fields = TripHelper.missing_fields
is_ready = TripHelper.is_ready
