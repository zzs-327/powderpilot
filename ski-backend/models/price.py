"""价格表：带录入日期，用于保鲜期检查（超过 90 天要提示"可能已变动"）。

数据来源：data/resorts/<id>.yaml → scripts/sync_to_mysql.py 同步。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base

# 价格保鲜期（天）：超过就提示用户可能过期
PRICE_TTL_DAYS = 90


class Price(Base):
    __tablename__ = 'price'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resort_id: Mapped[int] = mapped_column(ForeignKey('resort.id', ondelete='CASCADE'), index=True)
    item: Mapped[str] = mapped_column(String(64), comment='价格项，如"平日全天雪票"')
    price: Mapped[int] = mapped_column(Integer, comment='原币金额整数；币种见 currency')
    unit: Mapped[str] = mapped_column(String(64), default='次',
                                      comment='计价单位；可分时段优惠价写清时段口径')
    updated_at: Mapped[dt.date | None] = mapped_column(Date, default=None, comment='录入日期')
    expire_at: Mapped[dt.date | None] = mapped_column(Date, default=None, comment='保鲜期截止')
    source: Mapped[str] = mapped_column(String(128), default='')
    source_type: Mapped[str] = mapped_column(String(4), default='A')
    # 这条价格自己"几手"（说明见 models/resort.py 里 Run.data_status 的注释）
    data_status: Mapped[str] = mapped_column(String(16), default='')

    # ---- 计价口径（0002 迁移新增；全部可空、无服务器默认，空值含义由 rag/pricing.py 解释）----
    currency: Mapped[str | None] = mapped_column(String(3), default=None,
                                                 comment='ISO 4217 币种；空按 CNY；不做换算')
    category: Mapped[str | None] = mapped_column(String(16), default=None,
                                                 comment='lift_ticket/rental/lesson/transfer/lodging/other')
    unit_basis: Mapped[str | None] = mapped_column(String(24), default=None,
                                                   comment='per_person/per_instructor/per_room/per_seat_one_way/per_set/per_item/unknown')
    valid_from: Mapped[dt.date | None] = mapped_column(Date, default=None, comment='报价适用期起（不是保鲜期）')
    valid_to: Mapped[dt.date | None] = mapped_column(Date, default=None, comment='报价适用期止')
    season: Mapped[str | None] = mapped_column(String(32), default=None, comment='适用雪季展示文本')
    tax_status: Mapped[str | None] = mapped_column(String(8), default=None, comment='included/excluded/unknown')
    includes: Mapped[list | None] = mapped_column(JSON, default=None, comment='包含项短语列表')
    excludes: Mapped[list | None] = mapped_column(JSON, default=None, comment='不包含项短语列表')
    conditions: Mapped[list | None] = mapped_column(JSON, default=None, comment='预约、退改等短条款')
    budget_usable: Mapped[bool | None] = mapped_column(Boolean, default=None,
                                                       comment='能否进入已知小计；空按可用')
    source_url: Mapped[str | None] = mapped_column(String(512), default=None, comment='单条原页链接')

    resort: Mapped['Resort'] = relationship(back_populates='prices')
