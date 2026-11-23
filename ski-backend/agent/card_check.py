"""行程卡字段级校验：把"反照骗"从正文延伸到**结构化输出**。

为什么需要（2026-10-03 端到端真跑踩到）：
    正文有引用校验（agent/middleware.py 的 citation_check），但行程卡是**另一条出口**——
    此前只靠 _fallback_card 提示词里一句"铁律：不许编造"。实测 glm-4-flash 出的卡：

      1. itinerary 里写「二世谷雪道」——二世谷根本不在收录名单里（上一轮刚问过二世谷，
         模型把话题带进了行程）；
      2. 出行时间是"周末"，预算却按平日价 560 计（证据里同时有周末价 650）；
      3. 「酒店住宿 200 元」「餐饮 100 元」资料里没有，却照样挂着 sources=[E301]，
         合计 2400 与明细之和 1260 也对不上。

    这是本项目最该避免的形状：**看起来有来源，其实数字没有依据**。
    提示词约束是祈愿，机制该管的地方就上机制——所以这里做三件事：

      A. 未收录雪场：卡片文本里出现收录名单外的雪场名 → 违规（复用收录范围守卫）
      B. sources 白名单：卡片引用的 [Exxx] 必须在本轮证据里真的出现过
      C. 预算自洽：每个金额要么在证据里有据，要么必须带 note 标注（如"估算"）；
         total / subtotals 必须等于同币种 counted 明细之和（容差 1）；
         不同币种不能合成一个合计；complete 不能标成 true

    校验结果是**中文违规清单**，可以直接喂回模型让它逐条修正。
    两次都不合格就不出卡——与 _fallback_card 既有的原则一致：宁可没有卡，也不给错卡。
"""
from __future__ import annotations

import re

_ID_RE = re.compile(r'E(\d+)')
_NUMBER_RE = re.compile(r'\d+(?:\.\d+)?')
# 允许的误差：明细与合计之间（浮点/四舍五入）
_TOTAL_TOLERANCE = 1.0


def _as_dict(card) -> dict:
    """TripCard（pydantic）或 dict 都能吃：方便离线测试与后续换结构"""
    if card is None:
        return {}
    dump = getattr(card, 'model_dump', None)
    return dump() if callable(dump) else dict(card)


def _texts_of(node, skip_note: bool = True) -> list[str]:
    """递归取出卡片里所有字符串（默认跳过 note——它是"标注"本身，不是声明）"""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            if skip_note and key == 'note':
                continue
            out.extend(_texts_of(value, skip_note))
        return out
    if isinstance(node, (list, tuple)):
        out = []
        for item in node:
            out.extend(_texts_of(item, skip_note))
        return out
    return []


def _numbers_in(text: str) -> set[float]:
    return {float(match) for match in _NUMBER_RE.findall(text)}


def _grams(text: str, size: int = 2) -> set[str]:
    """字符二元组：中文没有空格，用二元组算"这行说的是不是这个费用项"最省事"""
    cleaned = re.sub(r'\s+', '', text or '')
    if len(cleaned) < size:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + size] for i in range(len(cleaned) - size + 1)}


def _has_basis(item_name: str, amount: float, evidence_text: str) -> bool:
    """金额是否有据：**同一行**里既出现这个数字，又和费用项名称有二元组重合

    为什么不能只查"这个数字在证据里出现过"（2026-10-03 实测的假阴性）：
        卡片里"酒店住宿 200 元"在资料里查无此项，但资料里有人均 200 元的雪具租赁，
        只比数字就把这笔无据费用放过去了。加一层"同一行 + 关键词重合"才拦得住，
        同时仍然允许"周末全天雪票 650"命中"…全天雪票·自带板·周末：650 元"。
    """
    item_grams = _grams(item_name)
    if not item_grams:
        return False
    for line in (evidence_text or '').splitlines():
        if not line.strip() or amount not in _numbers_in(line):
            continue
        if item_grams & _grams(line):
            return True
    return False


def check_card(card, evidence_text: str, allowed_ids: set[int],
               check_budget: bool = True) -> list[str]:
    """返回违规清单（空列表 = 通过）

    :param card: TripCard 或等价的 dict
    :param evidence_text: 本轮工具返回的原文（数字依据从这里面找）
    :param allowed_ids: 本轮合法的证据编号（与中间件同一套白名单）
    :param check_budget: 是否校验预算项"在证据里有据"。
        **数据渲染出来的卡要关掉这一项**（2026-10-04 加）：那些金额直接来自
        MySQL 价格行本身，不需要再用"证据文本里有没有这个数"去防幻觉——
        那条检查是给**模型生成的**预算用的（模型会编酒店/餐饮这类无据费用）。
        合计自洽（C2）与来源白名单（B）仍然保留。
    """
    data = _as_dict(card)
    if not data:
        return []
    issues: list[str] = []

    # ---- A. 未收录雪场 ----
    from rag.parser import ResortRegistry

    card_text = '\n'.join(_texts_of(data))
    unknown = ResortRegistry.detect_unknown(card_text)
    if unknown:
        issues.append(f'卡片里出现了未收录雪场「{unknown}」——本库没有它的资料，'
                      '请只使用已收录雪场，不要把它写进行程或雪道名')

    # ---- B. sources 必须在证据白名单里 ----
    for entry in data.get('sources') or []:
        index = entry.get('index') if isinstance(entry, dict) else entry
        for raw in _ID_RE.findall(str(index or '')):
            if int(raw) not in allowed_ids:
                issues.append(f'sources 引用了本轮证据里不存在的编号 E{raw}，'
                              '要么删掉它，要么换成真实存在的编号')

    # ---- C. 预算有据（数据渲染的卡跳过这一项，见 check_budget 说明）----
    budget = data.get('budget') or {}
    items = budget.get('items') or []
    if check_budget:
        for item in items:
            if not isinstance(item, dict) or item.get('amount') is None:
                continue
            amount = float(item['amount'])
            note = str(item.get('note') or '').strip()
            if not _has_basis(str(item.get('item') or ''), amount, evidence_text) and not note:
                issues.append(f'预算项「{item.get("item") or "未命名"}」的 {amount:g} 元'
                              '在资料里找不到依据，请删掉它，或在 note 里写明是估算')
    # 合计按币种分别核对：只加 counted 明细（多人共用费用只列示），不同币种绝不相加
    sums: dict[str, float] = {}
    for item in items:
        if isinstance(item, dict) and item.get('amount') is not None and item.get('counted', True):
            code = _currency(item.get('currency'))
            sums[code] = sums.get(code, 0.0) + float(item['amount'])
    if items:
        declared = budget.get('total')
        code = _currency(budget.get('currency'))
        if declared is not None and abs(float(declared) - sums.get(code, 0.0)) > _TOTAL_TOLERANCE:
            issues.append(f'预算合计 {float(declared):g} {code} 与该币种明细之和 '
                          f'{sums.get(code, 0.0):g} {code} 对不上，请改成一致')
    for entry in budget.get('subtotals') or []:
        code = _currency(entry.get('currency'))
        if abs(float(entry.get('total') or 0) - sums.get(code, 0.0)) > _TOTAL_TOLERANCE:
            issues.append(f'{code} 小计 {float(entry.get("total") or 0):g} 与该币种明细之和 '
                          f'{sums.get(code, 0.0):g} 对不上，请改成一致')
    if budget.get('subtotals'):
        listed = {_currency(entry.get('currency')) for entry in budget['subtotals']}
        for code in sorted(set(sums) - listed):
            issues.append(f'明细里有 {code} 费用，但小计里没有列出 {code}——不同币种必须分别列示')
    elif len(sums) > 1:
        issues.append(f'明细含多种币种（{"、".join(sorted(sums))}），必须用 subtotals 分别列示，不能合成一个合计')
    if budget.get('complete'):
        issues.append('卡片标成了完整预算，但交通、住宿等尚不能结构化核算——请改为非完整预算')

    return issues


def _currency(value) -> str:
    return str(value or 'CNY').strip().upper() or 'CNY'
