"""价格行的口径校验与文本渲染（YAML 同步、RAG 分块、价格工具共用一处）

设计说明见 docs/PRICING_MODEL.md。要点：
  - 金额必须是正整数；**未知金额不写成价格行**，这里直接报错，不兜底成 0。
  - 币种按原币记录，不做换算；缺省 CNY 只是为了兼容老数据。
  - 老数据（只有 item/price/unit 的人民币行）渲染出的文本必须与改造前**逐字一致**，
    否则全部价格 chunk 的嵌入都会变化，需要重建向量库并重跑评估。
"""
from __future__ import annotations

import datetime as dt
import re

DEFAULT_CURRENCY = 'CNY'

CATEGORIES = ('lift_ticket', 'rental', 'lesson', 'transfer', 'lodging', 'other')

UNIT_BASIS_LABEL = {
    'per_person': '按人计',
    'per_instructor': '按教练计（非每人价）',
    'per_room': '按房间计',
    'per_seat_one_way': '单程每座（往返需另计）',
    'per_set': '按套计',
    'per_item': '按件计',
    'unknown': '',
}

TAX_STATUS_LABEL = {'included': '含税', 'excluded': '不含税', 'unknown': '税费待确认'}

DATA_STATUSES = ('verified', 'transcribed', 'sample')

#: 与 models/price.py 的列宽一致；超长会让 MySQL 抛 DataError，导致整行静默丢失
FIELD_LIMITS = {'item': 64, 'unit': 64, 'source': 128, 'source_url': 512, 'season': 32}

_CURRENCY_RE = re.compile(r'^[A-Z]{3}$')
_LIST_FIELDS = ('includes', 'excludes', 'conditions')


def to_date(value) -> dt.date | None:
    """YAML 里的日期可能被解析成 date，也可能是字符串；转不了返回 None"""
    if isinstance(value, dt.date):
        return value
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def currency_of(row: dict) -> str:
    return str(row.get('currency') or DEFAULT_CURRENCY).strip().upper()


def budget_usable(row: dict) -> bool:
    """空值按"可用"处理，保持老数据的行为；只有显式 false 才排除"""
    return row.get('budget_usable') is not False


# 用户口语和条目原名经常对不上：问「接驳」时条目写的是「机场→雪场单程」
_CATEGORY_WORDS = (
    ('transfer', ('接驳', '班车', '巴士', '机场交通')),
    ('lift_ticket', ('雪票', '门票', '缆车票')),
    ('rental', ('租赁', '租具', '租板', '租金')),
    ('lesson', ('教练', '私教', '课程')),
    ('lodging', ('住宿', '酒店', '房价')),
)


def _query_categories(query: str) -> set[str]:
    return {category for category, words in _CATEGORY_WORDS if any(word in query for word in words)}


def _named_board(query: str) -> str:
    alpine, snowboard = '双板' in query, '单板' in query
    if alpine and not snowboard:
        return '双板'
    if snowboard and not alpine:
        return '单板'
    return ''


def narrow_prices(rows: list[dict], query: str) -> tuple[list[dict], str]:
    """条目名未命中时，按费用类别和板型缩小候选

    返回 (行, 方式)。方式为 category 表示可以列这些行；board_gap 表示问的板型没有
    对应价格；category_gap 表示问的费用类别没有价格行。这两种都不能改用别的价格顶上。
    空字符串表示缩不了，保持原来的整表候选。
    """
    query = (query or '').strip()
    if not query or not rows:
        return [], ''
    categories = _query_categories(query)
    board = _named_board(query)
    if not categories and not board:
        return [], ''

    def _in_category(row: dict) -> bool:
        if row.get('category') in categories:
            return True
        name = str(row.get('item') or '')
        return any(word in name for category, words in _CATEGORY_WORDS
                   if category in categories for word in words)

    chosen = [row for row in rows if _in_category(row)] if categories else list(rows)
    rental = 'rental' in categories or any(word in query for word in ('租赁', '租具', '租板', '租金'))
    if board and rental:
        named = [row for row in chosen if board in str(row.get('item') or '')]
        if not named:
            return [], 'board_gap'
        return named, 'category'
    if board:
        other = '单板' if board == '双板' else '双板'
        chosen = [row for row in chosen
                  if board in str(row.get('item') or '') or other not in str(row.get('item') or '')]
    if not chosen:
        if board:
            return [], 'board_gap'
        if categories:
            return [], 'category_gap'
        return [], ''
    return chosen, 'category'


def reference_price_notice(rows: list) -> str:
    """有任一价格不能计入预算时，给工具返回加一句总提醒

    每行口径已经写了「不计入预算小计」，但模型仍可能把整表加总。
    没有这种行时返回空串，国内旧雪场的工具结果保持原样。
    """
    if not any(not budget_usable(row) for row in rows or []):
        return ''
    return ('以下价格里有条目标明不计入预算小计。可以列示原币金额、单位和适用期；'
            '不能把它们相加称为完整旅行总价，不能把缺失费用当成0，'
            '也不能把不同币种换算后相加。')


def validate_price(row: dict) -> list[str]:
    """校验一条 YAML 价格行，返回问题列表（空列表表示合格）"""
    errors: list[str] = []
    amount = row.get('price')
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        errors.append(f'金额必须是正整数（未知金额不要写成价格行）：{amount!r}')
    if not str(row.get('item') or '').strip():
        errors.append('缺少 item')
    if row.get('currency') is not None and not _CURRENCY_RE.match(str(row['currency'])):
        errors.append(f'currency 须为 3 位大写 ISO 代码：{row["currency"]!r}')
    if row.get('category') is not None and row['category'] not in CATEGORIES:
        errors.append(f'category 不在 {CATEGORIES} 内：{row["category"]!r}')
    if row.get('unit_basis') is not None and row['unit_basis'] not in UNIT_BASIS_LABEL:
        errors.append(f'unit_basis 不在 {tuple(UNIT_BASIS_LABEL)} 内：{row["unit_basis"]!r}')
    if row.get('tax_status') is not None and row['tax_status'] not in TAX_STATUS_LABEL:
        errors.append(f'tax_status 不在 {tuple(TAX_STATUS_LABEL)} 内：{row["tax_status"]!r}')
    if row.get('data_status') is not None and row['data_status'] not in DATA_STATUSES:
        errors.append(f'data_status 不在 {DATA_STATUSES} 内：{row["data_status"]!r}')
    if row.get('budget_usable') is not None and not isinstance(row['budget_usable'], bool):
        errors.append(f'budget_usable 须为 true/false：{row["budget_usable"]!r}')
    start, end = row.get('valid_from'), row.get('valid_to')
    for name, value in (('valid_from', start), ('valid_to', end)):
        if value is not None and to_date(value) is None:
            errors.append(f'{name} 不是 YYYY-MM-DD 日期：{value!r}')
    if to_date(start) and to_date(end) and to_date(start) > to_date(end):
        errors.append(f'valid_from 晚于 valid_to：{start} > {end}')
    for name in _LIST_FIELDS:
        value = row.get(name)
        if value is not None and not (isinstance(value, list)
                                      and all(isinstance(x, str) and x.strip() for x in value)):
            errors.append(f'{name} 须为非空字符串列表：{value!r}')
    for name, limit in FIELD_LIMITS.items():
        if len(str(row.get(name) or '')) > limit:
            errors.append(f'{name} 超过 {limit} 字符')
    return errors


def validate_prices(resort_id: str, rows: list[dict]) -> None:
    """整场校验：有任何一行不合格就抛错并报出行号，不跳过、不兜底"""
    problems = [f'{resort_id}.prices[{index}]（{row.get("item", "")}）：{error}'
                for index, row in enumerate(rows or [])
                for error in validate_price(row)]
    if problems:
        raise ValueError('价格行不合规：\n' + '\n'.join(problems))


def price_qualifiers(row: dict) -> list[str]:
    """口径短语：适用期、计价对象、含税、包含/不包含、是否计入预算"""
    parts: list[str] = []
    start, end = to_date(row.get('valid_from')), to_date(row.get('valid_to'))
    if start or end:
        parts.append(f'适用 {start or "?"}～{end or "?"}')
    if row.get('season'):
        parts.append(str(row['season']))
    basis = UNIT_BASIS_LABEL.get(str(row.get('unit_basis') or ''), '')
    if basis:
        parts.append(basis)
    if row.get('tax_status'):
        parts.append(TAX_STATUS_LABEL[row['tax_status']])
    if row.get('includes'):
        parts.append('含：' + '、'.join(row['includes']))
    if row.get('excludes'):
        excludes = [x for x in row['excludes'] if x != 'unknown']
        parts.append('不含：' + '、'.join(excludes) if excludes else '不含项未逐项说明')
    if not budget_usable(row):
        parts.append('仅供参考，不计入预算小计')
    return parts


def price_text(resort_name: str, row: dict, updated: str) -> str:
    """一条价格的证据文本

    人民币老数据保持 "{雪场}{项}：{金额} 元/{单位}（录入日期 …）" 原样；
    外币写 ISO 代码（如 5000 JPY），绝不写成"元"。
    使用条件不写进这里：这段文本同时是向量库分块，改它就要重嵌入。
    """
    code = currency_of(row)
    money = f'{row.get("price", "?")} 元' if code == DEFAULT_CURRENCY else f'{row.get("price", "?")} {code}'
    extra = ''.join('｜' + part for part in price_qualifiers(row))
    return (f'{resort_name}{row.get("item", "")}：{money}'
            f'/{row.get("unit", "次")}{extra}（录入日期 {updated}）')


def price_evidence_text(resort_name: str, row: dict, updated: str) -> str:
    """查价工具给模型的文本：在分块文本后附上使用条件

    没有 conditions 时与 price_text 逐字相同。条件只服务当轮回答，不进向量库。
    """
    text = price_text(resort_name, row, updated)
    raw = row.get('conditions') or []
    if not isinstance(raw, list):
        return text
    conditions = [str(item).strip() for item in raw if str(item).strip()]
    if not conditions:
        return text
    return text + '。使用条件：' + '；'.join(conditions)
