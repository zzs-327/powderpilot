"""结构化行程卡：Agent 最终产出（对应计划书 v0.4 第 5.6 节）。

为什么用结构化输出而不是一段散文：
    前端要渲染成卡片（日程/预算/装备/来源），字段必须稳定；
    "给用户看的方案"和"给程序用的数据"统一，前端零解析成本。

注意：字段名用英文、description 写清楚——模型靠 description 填字段。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    """一条引用的来源（对应证据信封里的 [E1][E2]）。"""

    index: str = Field(description='引用编号，如 E1')
    source_type: str = Field(description='A 官方 / B 实拍 / C 经验')
    source: str = Field(description='出处：手册名 / 链接 / 拍摄者')
    date: str = Field(default='', description='信息日期，如 2026-09-01')


class DayPlan(BaseModel):
    day: int = Field(description='第几天，从 1 开始')
    plan: str = Field(description='当天安排：雪道、交通、注意事项，一到两句话')


class BudgetItem(BaseModel):
    item: str = Field(description='费用项，如"周末全天雪票"')
    amount: int = Field(description='该项金额（整数）；币种见 currency，勿把外币数字填进人民币语义')
    currency: str = Field(default='CNY', description='ISO 币种，如 CNY / JPY；缺省 CNY')
    note: str = Field(default='', description='补充说明，如"价格录入于 2026-09-01"')
    category: str = Field(default='', description='费用类别：lift_ticket/rental/lesson/transfer/lodging/other')
    counted: bool = Field(default=True,
                          description='是否计入人均小计；按教练/按房间等共用费用只列示、不计入')


class CurrencySubtotal(BaseModel):
    currency: str = Field(description='ISO 币种')
    total: int = Field(description='该币种 counted 明细之和；不同币种不相加、不换算')


class BudgetBreakdown(BaseModel):
    items: list[BudgetItem] = Field(description='预算明细，必须来自 get_resort_price 的真实价格')
    total: int = Field(description='首个币种的已知单价小计（不是全行程预算）；多币种见 subtotals')
    per_person: int = Field(description='与 total 同口径的人均展示值（当前不加人数乘算）')
    currency: str = Field(default='CNY', description='total 对应的币种')
    subtotals: list[CurrencySubtotal] = Field(default_factory=list, description='按币种分列的小计')
    missing: list[str] = Field(default_factory=list, description='本次行程需要但库内没有可用价格的费用类别')
    complete: bool = Field(default=False, description='是否为完整预算；交通、住宿未能结构化计算前恒为 false')


class Tip(BaseModel):
    text: str = Field(description='避坑提示')
    source_type: str = Field(default='C', description='A/B/C 置信级别')
    source: str = Field(default='', description='出处')


class VideoCard(BaseModel):
    title: str = Field(description='教学视频标题')
    level: str = Field(default='', description='适合水平：新手/进阶/老手')
    board: str = Field(default='', description='单板/双板')
    url: str = Field(default='', description='视频链接')


class TripCard(BaseModel):
    """一份可执行的滑雪行程方案。"""

    resort: str = Field(description='目标雪场名称')
    summary: str = Field(description='一句话结论：为什么推荐这个方案')
    itinerary: list[DayPlan] = Field(description='逐日安排')
    budget: BudgetBreakdown = Field(description='预算表（基于工具返回的真实价格）')
    gear_checklist: list[str] = Field(description='按用户水平与单双板生成的装备清单')
    tips: list[Tip] = Field(default_factory=list, description='避坑提示，尽量带来源')
    teaching: list[VideoCard] = Field(default_factory=list, description='推荐教学视频')
    sources: list[SourceRef] = Field(default_factory=list, description='本次回答引用的全部来源')
    caveats: list[str] = Field(default_factory=list,
                               description='风险与时效提示，如"价格录入于 2026-09-01，可能已变动"')
