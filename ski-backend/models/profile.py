"""用户画像表（长期记忆，跨会话生效）。

为什么单独建表而不是塞进 prompt：
    多用户项目里，画像写死在提示词会串号；放数据库按 user_id 取才安全，
    也才能实现"我晋级了"这类更新与（未来）用户可查看/可删除。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base


class UserProfile(Base):
    __tablename__ = 'user_profile'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, comment='对应 user.id')
    level: Mapped[str | None] = mapped_column(String(16), default=None, comment='新手/进阶/老手')
    board_type: Mapped[str | None] = mapped_column(String(16), default=None, comment='单板/双板/都玩')
    home_region: Mapped[str | None] = mapped_column(String(32), default=None, comment='常活动区域')
    budget_band: Mapped[str | None] = mapped_column(String(32), default=None, comment='预算带')
    create_time: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now)
    update_time: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now, onupdate=dt.datetime.now)
