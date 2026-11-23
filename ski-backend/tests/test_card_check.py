"""行程卡字段校验的离线用例（纯函数，不需要模型 / MySQL / Ollama）。

用例 1 复刻的是 2026-10-03 端到端真跑里 glm-4-flash 真实生成的那张卡：
行程里写了「二世谷雪道」、周末出行按平日价算、"酒店住宿 200"没有依据、
合计 2400 与明细之和对不上——**正是"看着有来源、数字没依据"的那种卡**。
它现在是回归用例：这类卡必须被拦下来。
"""
import pytest

from agent.card_check import check_card

pytestmark = pytest.mark.usefixtures("test_registry")

# 本轮工具返回（真实形状：带编号、来源、日期、可信度标记）
EVIDENCE = (
    '[E301] (A 官方｜转引自崇礼票务聚合页｜2026-04-06｜⚠️ 二手转引)\n'
    '万龙滑雪场全天雪票·自带板·平日：560 元/人/天（录入日期 2026-04-06）\n'
    '[E302] (A 官方｜转引自崇礼票务聚合页｜2026-04-06｜⚠️ 二手转引)\n'
    '万龙滑雪场全天雪票·自带板·周末：650 元/人/天（录入日期 2026-04-06）\n'
    '[E303] (A 官方｜转引自崇礼票务聚合页｜2026-04-06｜⚠️ 二手转引)\n'
    '万龙滑雪场雪具租赁（鞋板杖头盔）·1天·平日周末：200 元/人/天（录入日期 2026-04-06）'
)
ALLOWED = {301, 302, 303}


def clean_card() -> dict:
    """合规卡：数字都有据、合计自洽、来源在白名单、不出现未收录雪场"""
    return {
        'resort': '万龙滑雪场',
        'summary': '周末万龙两日行程',
        'itinerary': [{'day': 1, 'plan': '到达万龙滑雪场，适应性滑雪'}],
        'budget': {
            'items': [
                {'item': '周末全天雪票', 'amount': 650, 'note': ''},
                {'item': '雪具租赁', 'amount': 200, 'note': ''},
            ],
            'total': 850,
            'per_person': 850,
        },
        'sources': [{'index': 'E302', 'source': '崇礼票务聚合页', 'source_type': 'A'}],
        'gear_checklist': ['单板', '头盔'],
    }


def observed_bad_card() -> dict:
    """真跑里出现过的那张卡（原文摘录）"""
    return {
        'resort': '万龙滑雪场',
        'summary': '为中级单板爱好者量身定制的周末万龙滑雪行程。',
        'itinerary': [
            {'day': 1, 'plan': '抵达后办理入住，然后前往二世谷雪道体验中级难度滑雪。'},
            {'day': 2, 'plan': '全天在万龙滑雪场滑雪。'},
        ],
        'budget': {
            'items': [
                {'item': '平日全天雪票', 'amount': 560, 'note': ''},
                {'item': '雪具租赁', 'amount': 400, 'note': ''},
                {'item': '酒店住宿', 'amount': 200, 'note': ''},
                {'item': '餐饮', 'amount': 100, 'note': ''},
            ],
            'total': 2400,
            'per_person': 800,
        },
        'sources': [{'index': 'E301', 'source': '万龙滑雪场官网', 'source_type': 'A'}],
        'gear_checklist': ['单板', '滑雪服', '头盔'],
    }


def test_clean_card_passes():
    assert check_card(clean_card(), EVIDENCE, ALLOWED) == []


def test_uncovered_resort_in_itinerary_is_caught():
    """幻想的「二世谷雪道」必须被逮住——这是收录范围守卫在卡片上的复用"""
    issues = check_card(observed_bad_card(), EVIDENCE, ALLOWED)
    assert any('二世谷' in issue for issue in issues), issues


def test_unsupported_amount_without_note_is_caught():
    """无据金额必须被逮住——包括"资料里恰好有同一个数字"的情况

    实测假阴性：卡片写「酒店住宿 200 元」，资料里查无此项，但资料里有人均 200 元的
    雪具租赁；只比数字会放过去，所以校验是"同一行 + 费用项关键词重合"。
    """
    issues = check_card(observed_bad_card(), EVIDENCE, ALLOWED)
    assert any('酒店住宿' in issue for issue in issues), issues
    assert any('餐饮' in issue for issue in issues), issues
    assert any('雪具租赁' in issue for issue in issues), issues


def test_weekday_price_for_weekend_trip_is_not_caught():
    """已知边界：数字有据但**档位选错**（周末出行用平日价）拦不住

    560 确实出现在证据的第一行（平日价），所以它"有据"；要判断"周末该用周末价"
    需要把行程要素（date）也纳入校验，代价是可能误伤"两日行程含一个平日"这种合法情况。
    这里显式记录边界，避免以后误以为它被覆盖了。
    """
    card = clean_card()
    card['budget'] = {
        'items': [{'item': '平日全天雪票', 'amount': 560, 'note': ''}],
        'total': 560,
        'per_person': 560,
    }
    assert check_card(card, EVIDENCE, ALLOWED) == []


def test_amount_with_note_is_allowed():
    """资料里没有的数字，只要在 note 里写明是估算，就允许出现在卡里"""
    card = clean_card()
    card['budget']['items'].append({'item': '住宿（估算）', 'amount': 300, 'note': '估算，非库内数据'})
    card['budget']['total'] = 1150
    assert check_card(card, EVIDENCE, ALLOWED) == []


def test_total_mismatch_is_caught():
    """合计与明细对不上：宁可没有卡，也不给一张"合计=人均=用户报的数"的错卡"""
    card = clean_card()
    card['budget']['total'] = 3000
    issues = check_card(card, EVIDENCE, ALLOWED)
    assert any('合计' in issue for issue in issues), issues


def _jpy_card() -> dict:
    card = clean_card()
    card['budget'] = {
        'items': [
            {'item': '周末全天雪票', 'amount': 650, 'currency': 'CNY', 'note': ''},
            {'item': '接驳单程', 'amount': 5000, 'currency': 'JPY', 'note': '单程'},
            {'item': '私教', 'amount': 51000, 'currency': 'JPY', 'note': '按教练', 'counted': False},
        ],
        'total': 650, 'per_person': 650, 'currency': 'CNY',
        'subtotals': [{'currency': 'CNY', 'total': 650}, {'currency': 'JPY', 'total': 5000}],
        'missing': ['住宿'], 'complete': False,
    }
    return card


def test_per_currency_subtotals_pass_and_shared_cost_is_excluded():
    assert check_card(_jpy_card(), EVIDENCE, ALLOWED, check_budget=False) == []


def test_tampered_currency_subtotal_is_caught():
    card = _jpy_card()
    card['budget']['subtotals'][1]['total'] = 56000       # 偷偷把共用私教加进人均
    issues = check_card(card, EVIDENCE, ALLOWED, check_budget=False)
    assert any('JPY 小计' in issue for issue in issues), issues


def test_cross_currency_total_is_caught():
    card = _jpy_card()
    card['budget']['total'] = 5650                        # CNY + JPY 混加
    issues = check_card(card, EVIDENCE, ALLOWED, check_budget=False)
    assert any('合计' in issue for issue in issues), issues


def test_unlisted_currency_is_caught():
    card = _jpy_card()
    card['budget']['subtotals'] = card['budget']['subtotals'][:1]
    issues = check_card(card, EVIDENCE, ALLOWED, check_budget=False)
    assert any('没有列出 JPY' in issue for issue in issues), issues
    card['budget']['subtotals'] = []
    issues = check_card(card, EVIDENCE, ALLOWED, check_budget=False)
    assert any('多种币种' in issue for issue in issues), issues


def test_complete_budget_claim_is_caught():
    card = _jpy_card()
    card['budget']['complete'] = True
    issues = check_card(card, EVIDENCE, ALLOWED, check_budget=False)
    assert any('完整预算' in issue for issue in issues), issues


def test_source_outside_whitelist_is_caught():
    card = clean_card()
    card['sources'] = [{'index': 'E999', 'source': '某处', 'source_type': 'A'}]
    issues = check_card(card, EVIDENCE, ALLOWED)
    assert any('E999' in issue for issue in issues), issues


def test_dict_and_pydantic_card_behave_the_same():
    """TripCard（pydantic）与 dict 走同一套校验：两边都能吃"""
    from schemas.trip import TripCard

    card = TripCard(**observed_bad_card())
    assert check_card(card, EVIDENCE, ALLOWED) == check_card(
        observed_bad_card(), EVIDENCE, ALLOWED)


# ---------------- 接线层：证据窗口与"自证"排除 ----------------

def _tool(content: str):
    from langchain_core.messages import ToolMessage

    return ToolMessage(content=content, name='tool', tool_call_id='c1')


def _verify(messages: list, card):
    """跑真实的 _verify_card（兜底重写置为 None，避免真调模型）"""
    import asyncio

    from service.agent_service import AgentService

    original = AgentService._fallback_card

    async def no_repair(result, trip, violations=None):
        return None

    AgentService._fallback_card = staticmethod(no_repair)
    try:
        return asyncio.run(AgentService._verify_card(card, {'messages': messages}, {}))
    finally:
        AgentService._fallback_card = original


def test_internal_structured_message_is_not_treated_as_evidence():
    """卡不能"自己给自己作证"

    结构化输出的副作用消息里就是卡的原文（含"教练课 300 元"）。如果把它算进证据，
    这笔无据费用会被判成"有依据"，校验形同虚设——所以接线层必须把它剔掉。
    """
    from langchain_core.messages import HumanMessage

    card = clean_card()
    card['budget'] = {
        'items': [{'item': '教练课', 'amount': 300, 'note': ''}],
        'total': 300,
        'per_person': 300,
    }
    messages = [
        HumanMessage(content='排个行程'),
        _tool('[E301] (A 官方｜万龙官网｜2026-04-06)\n万龙滑雪场全天雪票·平日：560 元/人/天'),
        _tool('Returning structured response: 教练课 300 元 总价 300 元'),
    ]
    assert _verify(messages, card) is None      # 300 元无据 -> 不出卡


def test_card_may_cite_evidence_from_previous_turns():
    """出卡那一轮常常没有检索证据（只有 set_trip_info），价格是前几轮查的

    所以证据窗口跟引用校验一样要回看 N 轮，否则"编号不存在"会把每张卡都打回，
    行程卡功能等于废掉。
    """
    from langchain_core.messages import HumanMessage

    card = clean_card()
    card['budget'] = {
        'items': [{'item': '周末全天雪票', 'amount': 650, 'note': ''}],
        'total': 650,
        'per_person': 650,
    }
    card['sources'] = [{'index': 'E301', 'source': '万龙官网', 'source_type': 'A'}]
    messages = [
        HumanMessage(content='万龙雪票多少钱'),
        _tool('[E301] (A 官方｜万龙官网｜2026-04-06)\n万龙滑雪场全天雪票·周末：650 元/人/天'),
        HumanMessage(content='帮我排个周末行程'),
        _tool('已记录出行日期：周末。还缺：滑几天'),
    ]
    assert _verify(messages, card) is not None
