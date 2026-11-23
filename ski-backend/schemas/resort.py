"""雪场相关的 Pydantic 响应模型（Day3 接口层用）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResortResponse(BaseModel):
    resort_key: str = Field(description='雪场 id，如 wanlong')
    name: str
    region: str
    data_status: str = Field(description='sample=示例待核实 / verified=已核对')


class RunResponse(BaseModel):
    name: str
    difficulty: str
    level: str
    length_m: int | None = None
    vertical_m: int | None = None
    note: str | None = None


class PriceResponse(BaseModel):
    item: str
    price: int
    unit: str
    updated_at: str
    expire_at: str
    source: str
    source_type: str


class PhotoResponse(BaseModel):
    object_key: str
    shot_at: str
    location: str
    credit: str
    license: str
    source_type: str
