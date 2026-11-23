"""用户画像接口的请求/响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileUpdateRequest(BaseModel):
    level: str | None = Field(default=None, description='新手/进阶/老手')
    board_type: str | None = Field(default=None, description='单板/双板/都玩')
    home_region: str | None = Field(default=None, description='常活动区域')
    budget_band: str | None = Field(default=None, description='预算带')


class ProfileResponse(BaseModel):
    level: str | None = None
    board_type: str | None = None
    home_region: str | None = None
    budget_band: str | None = None
