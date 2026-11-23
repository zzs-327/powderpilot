"""行程卡：从**数据**渲染，而不是让模型编。

为什么改成这样（2026-10-04 实测）：
    原来卡片由模型通过 `ToolStrategy(TripCard)` 生成，再走字段校验，不合格喂回重写。
    实测"排行程"那题在免费弱模型上**烧完了 recursion_limit=40 步**（生成 → 校验 → 重写
    的循环），而且模型会：编预算项（酒店 200 / 餐饮 100 查无依据）、把未收录雪场
    （二世谷雪道）写进行程。这些不是模型的"失误"，而是**让模型发明结构化数据**的必然代价。

现在的分工：
    ┌─ 数据渲染（本模块，零模型调用）─────────────────────────────┐
    │ 预算   ← MySQL 价格表（带录入日期、保鲜期、来源、可信度档位）  │
    │ 来源   ← 本轮证据信封（谁说的、哪天的、几手数据）              │
    │ 提醒   ← 保鲜期 + 可信度档位 + 模板化行程的说明                │
    │ 行程骨架 ← 按出行要素模板化（结构化，不是编的）                │
    │ 装备清单 ← 按单双板/水平查规则表                              │
    │ 避坑   ← 该雪场 YAML 里已写明的注意事项（带来源，不让模型现编）  │
    └──────────────────────────────────────────────────────────┘
    ┌─ 模型负责 ─┐
    │ 正文回答（它本来就在写），不再负责编结构化字段 │
    └────────────┘

    收益：出卡路径 **0 次额外模型调用**，也没有"校验 → 重写"的循环；
    代价：行程是**模板骨架**（不是模型精心安排的），这一点必须在 caveats 里说明，
    不能假装它是定制方案。要恢复"模型写卡"用 CARD_MODE=model（见 config）。
"""
from __future__ import annotations

import datetime as dt
import re

from rag.pricing import budget_usable, price_qualifiers, to_date
from schemas.trip import (BudgetBreakdown, BudgetItem, CurrencySubtotal, DayPlan,
                          SourceRef, Tip, TripCard)

#: 装备清单规则表：按单双板 × 水平。不放进提示词让模型现编——这类清单是常识，
#: 用规则表输出才稳定（也不会把"护臀"漏掉或把"雪崩三件套"塞给新手）。
GEAR_RULES: dict[str, dict[str, list[str]]] = {
    '单板': {
        'beginner': ['单板（建议先租）', '软底雪鞋', '头盔', '护臀', '护膝', '雪镜',
                     '防水手套', '雪服', '防晒霜'],
        'intermediate': ['单板', '雪鞋', '头盔', '护臀', '护膝', '雪镜', '手套',
                         '雪服', '小背包'],
        'advanced': ['单板（自带更顺手）', '雪鞋', '头盔', '护具', '雪镜', '手套',
                     '雪服', '背包'],
    },
    '双板': {
        'beginner': ['双板+雪杖（建议先租）', '雪鞋', '头盔', '护膝', '雪镜',
                     '防水手套', '雪服', '防晒霜'],
        'intermediate': ['双板+雪杖', '雪鞋', '头盔', '护膝', '雪镜', '手套',
                         '雪服', '小背包'],
        'advanced': ['双板+雪杖（自带更顺手）', '雪鞋', '头盔', '护具', '雪镜',
                     '手套', '雪服', '背包'],
    },
}
DEFAULT_GEAR = ['头盔', '雪镜', '防水手套', '雪服', '防晒霜']

#: 水平写法 → 规则表的 key（画像里叫"进阶"，提示词里叫"中级"，统一到一处才不出错）
LEVEL_ALIASES: dict[str, tuple[str, ...]] = {
    'beginner': ('beginner', '新手', '初级'),
    'intermediate': ('intermediate', '进阶', '中级'),
    'advanced': ('advanced', '老手', '高级'),
}

#: 日期档位关键词：用来把"周末出行"匹配到"周末价"，而不是错用平日价

DATE_TIERS = (('节假日', ('节假日', '元旦', '春节')), ('夜场', ('夜场',)),
              ('周末', ('周末',)), ('平日', ('平日',)))

#: 费用类别 → 缺失清单里的中文名
CATEGORY_LABELS = {'lift_ticket': '雪票', 'rental': '租赁', 'transfer': '交通接驳',
                   'lodging': '住宿', 'lesson': '教学', 'other': '其他'}
#: 多人共用的计价对象：只列示，不计入人均小计（51000/教练 与 650/人 相加没有意义）
SHARED_BASIS = {'per_instructor', 'per_room'}

_ISO_DATE_RE = re.compile(r'(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})')

#: 币种展示：价格行未标注币种时缺省 CNY
CURRENCY_LABELS = {
    'CNY': '人民币',
    'JPY': '日元',
    'EUR': '欧元',
    'USD': '美元',
}
CURRENCY_UNITS = {
    'CNY': '元',
    'JPY': '日元',
    'EUR': '欧元',
    'USD': '美元',
}


def normalize_currency(value: object) -> str:
    """空/缺失 → CNY；统一大写。不在这里做汇率换算。"""
    code = str(value or 'CNY').strip().upper() or 'CNY'
    return code


def currency_label(code: str) -> str:
    code = normalize_currency(code)
    return CURRENCY_LABELS.get(code, code)


def currency_unit(code: str) -> str:
    code = normalize_currency(code)
    return CURRENCY_UNITS.get(code, code)


def pick_gear(board: str | None, level: str | None) -> list[str]:
    """按单双板与水平给装备清单（缺参数就退回通用清单）"""
    board_rules = GEAR_RULES.get((board or '').strip())
    if not board_rules:
        return list(DEFAULT_GEAR)
    text = (level or '').strip().lower()
    for key, aliases in LEVEL_ALIASES.items():
        if any(alias in text for alias in aliases):
            return list(board_rules[key])
    return list(board_rules['beginner'])


def _date_tier(date_text: str) -> str:
    for tier, keywords in DATE_TIERS:
        if any(keyword in (date_text or '') for keyword in keywords):
            return tier
    return ''


def _trip_date(date_text: str) -> dt.date | None:
    """出行要素里写了具体日期（2026-12-20 / 2026年12月20日）才取；"下周末"这类不猜"""
    match = _ISO_DATE_RE.search(date_text or '')
    if not match:
        return None
    try:
        return dt.date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _category(row: dict) -> str:
    """有 category 列用列；老数据按品名推断（只认雪票与租赁，其余不猜）"""
    if row.get('category'):
        return str(row['category'])
    name = str(row.get('item') or '')
    if '雪票' in name:
        return 'lift_ticket'
    if '租赁' in name or '雪具' in name:
        return 'rental'
    return ''


def _is_beginner(level: str) -> bool:
    text = (level or '').strip().lower()
    return any(alias in text for alias in LEVEL_ALIASES['beginner'])


def select_budget(prices: list[dict], date_text: str = '', *, level: str = '',
                  days: int | None = None) -> tuple[list[BudgetItem], list[str]]:
    """从**库内价格行**挑出预算项与缺失类别——挑不到就是空，绝不用模型编数字

    日期：写了适用期（valid_from/valid_to）的行，只有具体出行日期落在区间内才取，
    不回退到其他季；没写适用期的老数据按"平日/周末/节假日"档位匹配，档位未知时留空。
    租赁只取匹配档位或未限定日期的独立项目，含雪具雪票不再叠加租赁。
    交通有就列；住宿只在多日行程列；教学只在新手时列。
    sample 价格不列示。budget_usable=false 的已知单价可以列示，但 counted 为 false，
    不进入任何币种小计，也不能当成缺失费用为 0。不乘天数或人数。
    币种：读取行内 currency，缺省 CNY；不同币种分别小计，不相加、不换算、不丢弃。
    """
    tier = _date_tier(date_text)
    day = _trip_date(date_text)
    items: list[BudgetItem] = []
    beginner = _is_beginner(level)
    multi_day = bool(days) and int(days) > 1
    if tier or day:
        prices = [row for row in prices
                  if str(row.get('data_status') or '').strip().lower() != 'sample'
                  and row.get('price')]
    else:
        prices = []

    def _matches_date(row: dict, *, allow_undated: bool = False) -> bool:
        start, end = to_date(row.get('valid_from')), to_date(row.get('valid_to'))
        if start or end:
            return bool(day) and (start is None or start <= day) and (end is None or day <= end)
        name = str(row.get('item') or '')
        row_tiers = {key for key, keywords in DATE_TIERS
                     if any(keyword in name for keyword in keywords)}
        return (bool(tier) and tier in row_tiers) or (allow_undated and not row_tiers)

    def _row_note(row: dict, *, shared: bool) -> str:
        parts = [str(row.get('unit') or '').strip()]
        if row.get('updated_at'):
            parts.append(f"录入 {row['updated_at']}")
        code = normalize_currency(row.get('currency'))
        if code != 'CNY':
            parts.append(code)
        parts.extend(price_qualifiers(row))
        if shared:
            parts.append('多人共用费用，未计入人均小计')
        return '｜'.join(part for part in parts if part)

    def _item_from(row: dict) -> BudgetItem:
        shared = str(row.get('unit_basis') or '') in SHARED_BASIS
        reference = not budget_usable(row)
        return BudgetItem(
            item=str(row['item']),
            amount=int(row['price']),
            currency=normalize_currency(row.get('currency')),
            note=_row_note(row, shared=shared),
            category=_category(row),
            counted=not shared and not reference,
        )

    def _prefer_usable(rows: list[dict]) -> list[dict]:
        usable = [row for row in rows if budget_usable(row)]
        return usable or rows

    def _first(category: str, *, allow_undated: bool = True) -> dict | None:
        matches = [row for row in prices if _category(row) == category
                   and _matches_date(row, allow_undated=allow_undated)]
        return next(iter(_prefer_usable(matches)), None)

    ticket = _first('lift_ticket', allow_undated=False)
    if ticket:
        items.append(_item_from(ticket))

    ticket_name = str((ticket or {}).get('item') or '')
    includes_gear = bool(re.search(r'(?<!不)含(?:基础)?雪具', ticket_name)) or any(
        '雪具' in str(part) for part in (ticket or {}).get('includes') or [])
    rental_rows = _prefer_usable([row for row in prices if _category(row) == 'rental'
                                  and _matches_date(row, allow_undated=True)])
    rental = next((row for row in rental_rows
                   if '租赁' in str(row.get('item') or '')), None)
    if rental is None:
        rental = next(iter(rental_rows), None)
    if rental and not includes_gear:
        items.append(_item_from(rental))

    transfer = _first('transfer')
    lodging = _first('lodging') if multi_day else None
    lesson = _first('lesson') if beginner else None
    for row in (transfer, lodging, lesson):
        if row:
            items.append(_item_from(row))

    missing: list[str] = []
    if not ticket:
        missing.append(CATEGORY_LABELS['lift_ticket'])
    if not rental and not includes_gear:
        missing.append(CATEGORY_LABELS['rental'])
    if not transfer:
        missing.append(CATEGORY_LABELS['transfer'])
    elif str(transfer.get('unit_basis') or '') == 'per_seat_one_way':
        missing.append('返程交通（已知为单程价）')
    if multi_day and not lodging:
        missing.append(CATEGORY_LABELS['lodging'])
    if beginner and not lesson:
        missing.append(CATEGORY_LABELS['lesson'])
    return items, missing


def subtotals_of(items: list[BudgetItem]) -> list[CurrencySubtotal]:
    """按币种分别合计 counted 明细，保持首次出现的顺序"""
    totals: dict[str, int] = {}
    for item in items:
        if item.counted:
            totals[item.currency] = totals.get(item.currency, 0) + item.amount
    return [CurrencySubtotal(currency=code, total=total) for code, total in totals.items()]


def pick_budget(prices: list[dict], date_text: str = '', *, level: str = '',
                days: int | None = None) -> tuple[list[BudgetItem], int]:
    """预算项 + 首个币种的已知单价小计（多币种请用 subtotals_of）"""
    items, _ = select_budget(prices, date_text, level=level, days=days)
    subtotals = subtotals_of(items)
    return items, subtotals[0].total if subtotals else 0


def build_itinerary(days: int | None, resort_name: str) -> list[DayPlan]:
    """模板化行程骨架（**不是**模型定制的安排，caveats 里会说明）"""
    total = max(1, min(int(days or 2), 7))
    plans: list[DayPlan] = []
    for day in range(1, total + 1):
        if total == 1:
            plan = (f'抵达{resort_name}：取装备、热身后适应性滑雪，'
                    '下午收拾并归还装备返程（预留返程时间）')
        elif day == 1:
            plan = (f'抵达{resort_name}：按需安排住宿、取装备，下午适应性滑雪'
                    '（先初级道热雪，别一上来就冲难度）')
        elif day == total:
            plan = f'{resort_name}最后一天：上午滑雪，下午收拾装备返程（留出还装备的时间）'
        else:
            plan = (f'{resort_name}全天滑雪：上午练技术、下午刷雪道'
                    '（根据当天雪况调整强度，注意观察冰面）')
        plans.append(DayPlan(day=day, plan=plan))
    return plans


def build_sources(evidences: list[dict]) -> list[SourceRef]:
    """来源直接取本轮证据信封——不新增、不臆造（编号对不上会被 card_check 拦下）"""
    sources: list[SourceRef] = []
    for item in evidences or []:
        label = str(item.get('label') or '')
        sources.append(SourceRef(
            index=f"E{item.get('id')}",
            source_type=label[:1] or 'C',
            source=str(item.get('source') or ''),
            date=str(item.get('date') or ''),
        ))
    return sources


def build_caveats(*, resort_name: str, prices: list[dict], data_status_note: str = '',
                  trip: dict | None = None, has_sources: bool = True,
                  evidences: list[dict] | None = None) -> list[str]:
    """风险与时效提示——由数据推出来，不是模型"想到"的"""
    caveats: list[str] = []
    if data_status_note:
        # ⚠️ 只取第一段：`data_status_note` 在本库里可以长到两千字（含交叉印证全文），
        #    整段塞进卡片提醒会变成一堵字墙（实测）。长说明属于"来源面板"，不属于卡片。
        first = re.split(r'[｜|。\n]', data_status_note.strip())[0].strip()
        if first:
            caveats.append(f'数据可信度：{first[:80]}')
    updated = sorted({str(row.get('updated_at') or '') for row in prices
                      if row.get('updated_at')})
    expired = [row for row in prices if str(row.get('expire_at') or '')]
    if updated:
        caveats.append(f'{resort_name}价格录入于 {updated[0]}，出行前请以官方渠道复核')
    if any(str(item.get('warn') or '') for item in evidences or []):
        caveats.append('部分来源标注"可能已过期"，请以官方最新信息为准')
    if expired:
        caveats.append('价格保鲜期见各明细备注，过期后仅作参考')
    if not prices:
        caveats.append('本库没有价格数据，预算项为空（宁缺勿造，不用估算值填充）')
    if not has_sources:
        caveats.append('本轮未取得可引用的资料来源，来源列表为空')
    if any(str(row.get('data_status') or '').strip().lower() == 'sample' for row in prices):
        caveats.append('sample 示例价格不作真实报价，已排除，请以官方价格为准')
    trip = trip or {}
    budget_items, _ = select_budget(prices, str(trip.get('date') or ''),
                                    level=str(trip.get('level') or ''), days=trip.get('days'))
    if prices and not budget_items:
        caveats.append('暂无可匹配出行日期档位的非示例价格，预算项为空，价格待确认')
    subtotals = subtotals_of(budget_items)
    codes = [s.currency for s in subtotals] or ['CNY']
    label = '、'.join(currency_label(code) for code in codes)
    caveats.append(f'合计仅为已知单价小计（{label}），不是全行程预算；'
                   f'交通、住宿、教学、餐饮等未收录费用待确认，不能视作0{currency_unit(codes[0])}')
    if len(codes) > 1:
        caveats.append('不同币种分别列示，未换算、未相加；如需比较请自行按当日汇率核算')
    if any('多人共用' in item.note for item in budget_items):
        caveats.append('按教练、按房间计价的费用为多人共用，只列示、未计入人均小计')
    if any('不计入预算小计' in item.note for item in budget_items):
        caveats.append('标为参考单价的金额只列示，未计入小计，不能相加称为完整旅行总价')
    caveats.append('未按天数或人数自动乘算；请按各项计价单位、适用日期和实际需求另行核算')
    caveats.append('行程为按天数套用的模板骨架，具体雪道安排请结合当天雪况与运营公告')
    return caveats


def _curated_tips(resort_name: str) -> list[Tip]:
    """把该雪场 YAML 里的避坑带上卡，来源和日期留在 source 里"""
    from rag.parser import ResortRegistry

    tips: list[Tip] = []
    for row in ResortRegistry.curated_tips(resort_name):
        source = row['source']
        if row['date'] and row['date'] not in source:
            source = f'{source} · {row["date"]}' if source else row['date']
        tips.append(Tip(text=row['text'], source_type=row['source_type'], source=source))
    return tips


def build_card(*, resort_name: str, trip: dict, evidences: list[dict],
               prices: list[dict], data_status_note: str = '') -> TripCard:
    """把数据渲染成一张 TripCard（**零模型调用**）"""
    people = int(trip.get('people') or 0)
    level = str(trip.get('level') or '')
    board = str(trip.get('board') or '')
    days = trip.get('days')
    items, missing = select_budget(prices, str(trip.get('date') or ''), level=level, days=days)
    subtotals = subtotals_of(items)
    total = subtotals[0].total if subtotals else 0
    currency = subtotals[0].currency if subtotals else 'CNY'

    summary_bits = [resort_name]
    if days:
        summary_bits.append(f'{days} 天')
    if people:
        summary_bits.append(f'{people} 人')
    if board:
        summary_bits.append(board)
    summary = '·'.join(summary_bits)
    if subtotals:
        money = ' + '.join(f'{s.total} {currency_unit(s.currency)}' for s in subtotals)
        scope = '分币种列示、未换算；非全行程预算' if len(subtotals) > 1 else '非全行程预算'
        summary += f'｜已知单价小计 {money}（{scope}）'
    elif items and any('不计入预算小计' in item.note for item in items):
        summary += '｜以下含参考单价，未计入小计，不能相加（非全行程预算）'
    elif items:
        summary += '｜已知费用均为多人共用项，未计入人均小计（非全行程预算）'
    else:
        summary += '｜价格待确认（非零元预算）'

    return TripCard(
        resort=resort_name,
        summary=summary,
        itinerary=build_itinerary(days, resort_name),
        budget=BudgetBreakdown(items=items, total=total, per_person=total,
                               currency=currency, subtotals=subtotals,
                               missing=missing, complete=False),
        gear_checklist=pick_gear(board, level),
        tips=_curated_tips(resort_name),
        teaching=[],
        sources=build_sources(evidences),
        caveats=build_caveats(resort_name=resort_name, prices=prices,
                              data_status_note=data_status_note, trip=trip,
                              has_sources=bool(evidences), evidences=evidences),
    )
