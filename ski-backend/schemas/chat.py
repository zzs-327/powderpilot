"""对话接口的请求/响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field
from schemas.guidance import Guidance


class ChatRequest(BaseModel):
    message: str = Field(description='用户这一轮说的话')
    thread_id: str | None = Field(
        default=None, description='会话 id；不传则用 chat-{user_id}，同 id 可续聊')


class TripStateOut(BaseModel):
    """当前行程状态（前端可用来显示"还缺什么"）。"""

    resort: str | None = None
    date: str | None = None
    days: int | None = None
    people: int | None = None
    budget: int | None = None
    board: str | None = None
    ready: bool | None = None


class EvidenceOut(BaseModel):
    """一条证据摘要（前端"来源溯源"用）：编号 / 出处 / 日期 / 可信度档位"""

    id: int
    label: str = ''          # A 官方 / B 实拍 / C 经验
    source: str = ''         # 出处
    date: str = ''           # 信息日期
    status: str = ''         # 可信度中文标签
    status_key: str = ''     # verified / corroborated / transcribed / sample / ''
    warn: str = ''           # "可能已过期"等提醒
    snippet: str = ''        # 证据正文摘要


class ChatResponse(BaseModel):
    guidance: Guidance | None = Field(default=None, description='成功完成后的情境交互')
    reply: str = Field(description='文字回复')
    trip: TripStateOut = Field(default_factory=TripStateOut, description='当前行程状态')
    structured: dict | None = Field(default=None, description='结构化行程卡（未触发时为 null）')
    thread_id: str = Field(description='本次会话 id，下次带上它可续聊')
    evidences: list[EvidenceOut] = Field(default_factory=list,
                                         description='本轮引用的证据（前端来源溯源用）')
