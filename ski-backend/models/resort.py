"""雪场与雪道表（结构化数据，回答"有几条高级道"这类统计问题）。

数据来源：data/resorts/<id>.yaml（单一真相源）→ scripts/sync_to_mysql.py 同步。
不要在数据库里手改——下次同步会被覆盖。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


class Resort(Base):
    __tablename__ = 'resort'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resort_key: Mapped[str] = mapped_column(String(32), unique=True, index=True, comment='对应 YAML 的 id')
    name: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64), default='')
    transport_hint: Mapped[str | None] = mapped_column(String(255), default=None)
    handbook_url: Mapped[str | None] = mapped_column(String(255), default=None)
    price_updated_at: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # 官方公布的雪道总数（按难度）——与"本库已收录明细"是两回事，不能混着说
    runs_beginner: Mapped[int | None] = mapped_column(Integer, default=None)
    runs_intermediate: Mapped[int | None] = mapped_column(Integer, default=None)
    runs_advanced: Mapped[int | None] = mapped_column(Integer, default=None)
    # 官网口径的雪道总数（各难度未公布时用这一列）。
    # 为什么要和 runs_* 分开存：两者**可能不一致**——万龙官网说 22 条，
    # 而第三方参数表给的是 32 条明细。合成一个字段就必须做取舍，而
    # "替数据做取舍"正是本项目最不该做的事：两个口径都要留着，回答时两个都要说。
    official_runs_total: Mapped[int | None] = mapped_column(Integer, default=None)
    data_status: Mapped[str] = mapped_column(
        String(16), default='sample',
        comment='数据可信度：verified=官方直采 / transcribed=二手转引 / sample=示例待核实')
    # 采集方式的可读说明（引用来源、检索日期、为什么够不到 verified）
    data_status_note: Mapped[str | None] = mapped_column(Text, default=None)
    create_time: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now)
    update_time: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now, onupdate=dt.datetime.now)

    runs: Mapped[list['Run']] = relationship(back_populates='resort', cascade='all, delete-orphan')
    prices: Mapped[list['Price']] = relationship(back_populates='resort', cascade='all, delete-orphan')
    photos: Mapped[list['Photo']] = relationship(back_populates='resort', cascade='all, delete-orphan')


class Run(Base):
    __tablename__ = 'run'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resort_id: Mapped[int] = mapped_column(ForeignKey('resort.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(64))
    difficulty: Mapped[str] = mapped_column(String(16), comment='初级/中级/高级')
    level: Mapped[str] = mapped_column(String(16), comment='beginner/intermediate/advanced')
    length_m: Mapped[int | None] = mapped_column(Integer, default=None)
    vertical_m: Mapped[int | None] = mapped_column(Integer, default=None, comment='落差（米），不是所有官方口径都给')
    # 坡度与海拔：官方雪道参数表的常见口径，比落差更能回答"陡不陡、我下不下得来"
    top_altitude_m: Mapped[int | None] = mapped_column(Integer, default=None, comment='最高海拔（米）')
    avg_slope_deg: Mapped[float | None] = mapped_column(Float, default=None, comment='平均坡度（度）')
    max_slope_deg: Mapped[float | None] = mapped_column(Float, default=None, comment='最大坡度（度）')
    note: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(String(255), default='')
    source_type: Mapped[str] = mapped_column(String(4), default='A')
    # 这条数据自己"几手"——**不要**看 resort.data_status（那是整座雪场的最保守口径）。
    # 为什么行级也要存：真实情况常常是"雪道有多源印证、价格只有单一转引"，
    # 拿雪场级的档位去渲染每一条，必然有一类会说谎。
    data_status: Mapped[str] = mapped_column(String(16), default='')
    date: Mapped[dt.date | None] = mapped_column(Date, default=None)

    resort: Mapped['Resort'] = relationship(back_populates='runs')
