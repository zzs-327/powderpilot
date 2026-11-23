"""实拍图表（B 级证据）：图片本体在 MinIO/本地，这里只存元数据。

前端图集、MCP 返回的图片 URL、回答里的"实拍证据"都从这里取。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


class Photo(Base):
    __tablename__ = 'photo'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resort_id: Mapped[int] = mapped_column(ForeignKey('resort.id', ondelete='CASCADE'), index=True)
    object_key: Mapped[str] = mapped_column(String(255), comment='MinIO object_key 或本地相对路径')
    shot_at: Mapped[dt.date | None] = mapped_column(Date, default=None, comment='拍摄日期')
    location: Mapped[str] = mapped_column(String(64), default='')
    credit: Mapped[str] = mapped_column(String(64), default='', comment='拍摄者')
    license: Mapped[str] = mapped_column(String(64), default='', comment='授权状态')
    source_type: Mapped[str] = mapped_column(String(4), default='B')

    resort: Mapped['Resort'] = relationship(back_populates='photos')
