"""行程卡"数据渲染"的离线用例（纯函数，不需要模型/MySQL/Ollama）。

背景：卡片原来由模型生成（ToolStrategy）+ 字段校验 + 不合格重写，
实测"排行程"那题在弱模型上烧完 40 步 recursion_limit。改成数据渲染后：
预算来自 MySQL 价格行、来源来自证据信封、行程是模板骨架、装备查规则表。
这组用例盯的就是"渲染出来的东西必须自洽、可溯源、且过得了校验"。
"""
import pytest


from agent.card_builder import (build_card, build_itinerary, build_sources,
                               pick_budget, pick_gear, select_budget)
from agent.card_check import check_card

pytestmark = pytest.mark.usefixtures("test_registry")

# 真实形状的价格行（ResortService.get_prices 的返回）
PRICES = [
    {'item': '全天雪票·自带板·平日', 'price': 560, 'unit': '人/天',
     'updated_at': '2026-04-06', 'expire_at': '2026-11-01',
     'source': '转引自崇礼票务聚合页', 'source_type': 'A', 'data_status': 'transcribed'},
    {'item': '全天雪票·自带板·周末', 'price': 650, 'unit': '人/天',
     'updated_at': '2026-04-06', 'expire_at': '2026-11-01',
     'source': '转引自崇礼票务聚合页', 'source_type': 'A', 'data_status': 'transcribed'},
    {'item': '雪具租赁（鞋板杖头盔）·1天·平日周末', 'price': 200, 'unit': '人/天',
     'updated_at': '2026-04-06', 'expire_at': '2026-11-01',
     'source': '转引自崇礼票务聚合页', 'source_type': 'A', 'data_status': 'transcribed'},
]
# 证据信封（EvidenceDigest.of_messages 的返回形状）
EVIDENCES = [
    {'id': 301, 'label': 'A 官方', 'source': '转引自崇礼票务聚合页',
     'date': '2026-04-06', 'status': '⚠️ 二手转引（非官网直采）',
     'status_key': 'transcribed', 'warn': '可能已过期',
     'snippet': '万龙滑雪场全天雪票·自带板·周末：650 元/人/天'},
]
TRIP = {'date': '周末', 'days': 2, 'people': 2, 'budget': 2000, 'board': '单板'}


def test_pick_budget_prefers_date_tier():
    """周末出行要挑到周末价——错用平日价是最容易被用户发现的错"""
    items, total = pick_budget(PRICES, '周末')
    assert items[0].amount == 650, [i.item for i in items]
    assert items[1].amount == 200          # 租赁
    assert total == 850


def test_pick_budget_empty_when_no_prices():
    """库里没价格就空着——宁缺勿造，不用估算值填充"""
    items, total = pick_budget([], '周末')
    assert items == [] and total == 0


def test_gear_by_board_and_level():
    assert any('护臀' in item for item in pick_gear('单板', '初级'))
    assert any('护具' in item for item in pick_gear('双板', '老手'))
    # 水平写法不统一（画像叫"进阶"、提示词叫"中级"）也要认得出
    assert pick_gear('单板', '进阶') == pick_gear('单板', 'intermediate')
    # 板型未知 → 通用清单（不猜）
    assert pick_gear('', '初级') == pick_gear('other', '初级')


def test_itinerary_follows_days():
    plans = build_itinerary(3, '万龙滑雪场')
    assert [plan.day for plan in plans] == [1, 2, 3]
    assert '抵达' in plans[0].plan and '返程' in plans[-1].plan


def test_sources_come_from_evidence():
    sources = build_sources(EVIDENCES)
    assert sources[0].index == 'E301'
    assert sources[0].source_type == 'A'
    assert sources[0].date == '2026-04-06'


def test_built_card_passes_card_check():
    """关键集成断言：数据渲染出来的卡必须过得了校验器"""
    card = build_card(resort_name='万龙滑雪场', trip=TRIP, evidences=EVIDENCES,
                      prices=PRICES, data_status_note='二手转引，未经官网直采')
    issues = check_card(card, evidence_text='', allowed_ids={301}, check_budget=False)
    assert issues == [], issues


def test_built_card_still_fails_on_bad_source():
    """关掉预算检查 ≠ 关掉全部检查：来源编号仍必须真实存在"""
    card = build_card(resort_name='万龙滑雪场', trip=TRIP,
                      evidences=[dict(EVIDENCES[0], id=999)],
                      prices=PRICES)
    issues = check_card(card, evidence_text='', allowed_ids={301}, check_budget=False)
    assert any('E999' in issue for issue in issues), issues


def test_caveats_are_honest():
    """卡片必须自己说清三件事：数据几手、预算不含天数乘算、行程是模板"""
    card = build_card(resort_name='万龙滑雪场', trip=TRIP, evidences=EVIDENCES,
                      prices=PRICES, data_status_note='二手转引，未经官网直采')
    text = '｜'.join(card.caveats)
    assert '二手转引' in text            # 数据可信度
    assert '乘算' in text                # 预算是人均单价合计
    assert '模板骨架' in text            # 行程不是模型定制的
    assert '录入于' in text              # 时效提醒

    empty = build_card(resort_name='万龙滑雪场', trip=TRIP, evidences=EVIDENCES,
                       prices=[])
    assert any('没有价格数据' in item for item in empty.caveats)


def test_summary_is_data_derived():
    card = build_card(resort_name='万龙滑雪场', trip=TRIP, evidences=EVIDENCES,
                      prices=PRICES)
    assert '万龙滑雪场' in card.summary and '2 天' in card.summary
    assert '850' in card.summary          # 数字来自价格行，不是模型说的


@pytest.mark.parametrize('date_text', ['', '日期待定', '2026-12-20', '春节', '夜场'])
def test_unknown_or_unmatched_date_does_not_use_first_price(date_text):
    items, total = pick_budget(PRICES, date_text)
    assert items == []
    assert total == 0


@pytest.mark.parametrize('label', ['平日周末', '平日/周末'])
def test_combined_date_tier_matches(label):
    prices = [{'item': f'全天雪票·{label}', 'price': 300}]
    assert pick_budget(prices, '周末')[1] == 300
    assert pick_budget(prices, '平日')[1] == 300


def test_holiday_takes_priority_over_weekend():
    prices = PRICES + [{'item': '全天雪票·节假日', 'price': 700}]
    items, total = pick_budget(prices, '春节周末')
    assert [item.amount for item in items] == [700]
    assert total == 700


@pytest.mark.parametrize('ticket_name', ['全天雪票·含雪具·平日', '含雪具租赁的雪票·平日'])
def test_included_gear_does_not_add_separate_rental(ticket_name):
    prices = [{'item': ticket_name, 'price': 300}, PRICES[2]]
    items, total = pick_budget(prices, '平日')
    assert [item.item for item in items] == [ticket_name]
    assert total == 300


def test_ticket_without_gear_still_adds_matching_rental():
    prices = [{'item': '全天雪票·不含雪具·周末', 'price': 300},
              {'item': '雪具租赁·平日', 'price': 50}, PRICES[2]]
    items, total = pick_budget(prices, '周末')
    assert [item.amount for item in items] == [300, 200]
    assert total == 500


def test_rental_fallback_does_not_duplicate_ticket():
    items, total = pick_budget([{'item': '全天雪票·含雪具·平日', 'price': 300}], '平日')
    assert len(items) == 1
    assert total == 300


def test_sample_price_is_not_a_real_quote():
    prices = [dict(row, data_status='sample') for row in PRICES]
    card = build_card(resort_name='示例雪场', trip=TRIP, evidences=[], prices=prices)
    assert card.budget.items == []
    assert card.budget.total == 0
    assert '价格待确认' in card.summary
    assert any('sample' in note and '排除' in note for note in card.caveats)


def test_sample_row_cannot_hide_matching_real_price():
    prices = [dict(PRICES[1], price=1, data_status='sample')] + PRICES
    assert pick_budget(prices, '周末')[1] == 850


@pytest.mark.parametrize('trip', [{}, {'days': 1}, dict(TRIP, days=5, people=4)])
def test_subtotal_is_not_total_trip_budget_even_without_days(trip):
    card = build_card(resort_name='万龙滑雪场', trip=dict(trip, date='周末'),
                      evidences=[], prices=PRICES)
    assert card.budget.total == card.budget.per_person == 850
    assert '已知单价小计' in card.summary
    assert '非全行程预算' in card.summary
    text = '｜'.join(card.caveats)
    assert all(word in text for word in ['交通', '住宿', '教学', '不能视作0元', '人民币', '乘算'])


def test_empty_budget_is_pending_not_free():
    card = build_card(resort_name='万龙滑雪场', trip=dict(TRIP, date='春节'),
                      evidences=[], prices=PRICES)
    assert card.budget.items == []
    assert '价格待确认' in card.summary
    assert '非零元预算' in card.summary
    assert any('价格待确认' in note for note in card.caveats)


def test_one_day_template_returns_without_requiring_hotel():
    plans = build_itinerary(1, '万龙滑雪场')
    assert len(plans) == 1
    assert '抵达' in plans[0].plan and '返程' in plans[0].plan
    assert '入住' not in plans[0].plan and '住宿' not in plans[0].plan


def test_template_does_not_assert_afternoon_hard_snow():
    plans = build_itinerary(3, '万龙滑雪场')
    assert all('雪面偏硬' not in plan.plan for plan in plans)
    assert '当天雪况' in plans[1].plan


def test_budget_currency_defaults_to_cny():
    """未标注币种的国内价格行 → CNY，不把金额语义改成外币"""
    items, total = pick_budget(PRICES, '周末')
    assert total == 850
    assert all(item.currency == 'CNY' for item in items)
    card = build_card(resort_name='万龙滑雪场', trip=TRIP, evidences=EVIDENCES,
                      prices=PRICES)
    assert card.budget.currency == 'CNY'
    assert '人民币' in '｜'.join(card.caveats)


def test_mixed_currencies_are_listed_separately_not_added():
    """雪票 CNY + 租赁 JPY：两条都保留，分币种小计，绝不相加"""
    prices = [
        {'item': '全天雪票·自带板·周末', 'price': 650, 'currency': 'CNY'},
        {'item': '雪具租赁·平日周末', 'price': 10800, 'currency': 'JPY'},
    ]
    items, total = pick_budget(prices, '周末')
    assert [(item.amount, item.currency) for item in items] == [(650, 'CNY'), (10800, 'JPY')]
    assert total == 650
    card = build_card(resort_name='万龙滑雪场', trip=TRIP, evidences=[], prices=prices)
    assert [(s.currency, s.total) for s in card.budget.subtotals] == [('CNY', 650), ('JPY', 10800)]
    assert '650 元 + 10800 日元' in card.summary and '未换算' in card.summary
    assert any('未换算、未相加' in note for note in card.caveats)
    assert check_card(card, evidence_text='', allowed_ids=set(), check_budget=False) == []


# 留寿都形状的结构化价格行（金额取自候选审阅材料，仅作测试数据）
RUSUTSU = [
    {'item': '成人5小时票·常规季', 'price': 11600, 'currency': 'JPY', 'category': 'lift_ticket',
     'unit': '人/5小时', 'unit_basis': 'per_person', 'valid_from': '2026-12-19', 'valid_to': '2027-03-31',
     'tax_status': 'unknown'},
    {'item': '单板标准全套租赁·1日', 'price': 22300, 'currency': 'JPY', 'category': 'rental',
     'unit': '套/日', 'unit_basis': 'per_set'},
    {'item': '机场→雪场接驳', 'price': 5000, 'currency': 'JPY', 'category': 'transfer',
     'unit': '单程每座', 'unit_basis': 'per_seat_one_way',
     'valid_from': '2026-11-28', 'valid_to': '2027-03-30', 'tax_status': 'included'},
    {'item': '英语私教2小时·常规季', 'price': 51000, 'currency': 'JPY', 'category': 'lesson',
     'unit': '每教练', 'unit_basis': 'per_instructor', 'valid_from': '2026-12-19', 'valid_to': '2027-03-31'},
]
RUSUTSU_TRIP = {'date': '2027-01-15', 'days': 3, 'people': 2, 'board': '单板', 'level': '新手'}


def test_structured_prices_need_a_concrete_date_inside_validity():
    items, _ = select_budget(RUSUTSU, '下周末', level='新手', days=3)
    assert all(item.category != 'lift_ticket' for item in items)
    items, _ = select_budget(RUSUTSU, '2026-12-01', level='新手', days=3)
    assert [item.category for item in items] == ['rental', 'transfer']   # 票与课不在适用期
    items, missing = select_budget(RUSUTSU, '2027年1月15日', level='新手', days=3)
    assert [item.category for item in items] == ['lift_ticket', 'rental', 'transfer', 'lesson']
    assert missing == ['返程交通（已知为单程价）', '住宿']


def test_shared_costs_are_listed_but_not_counted():
    card = build_card(resort_name='样板雪场', trip=RUSUTSU_TRIP, evidences=[], prices=RUSUTSU)
    lesson = next(item for item in card.budget.items if item.category == 'lesson')
    assert lesson.counted is False and '非每人价' in lesson.note and '未计入' in lesson.note
    assert card.budget.total == 11600 + 22300 + 5000 and card.budget.currency == 'JPY'
    assert '38900 日元' in card.summary and '元' not in card.summary.replace('日元', '')
    assert card.budget.complete is False and '住宿' in card.budget.missing
    assert any('多人共用' in note for note in card.caveats)
    # 收录范围守卫会拦未收录雪场名，校验这一步借用已收录的名字，只验预算自洽
    named = build_card(resort_name='万龙滑雪场', trip=RUSUTSU_TRIP, evidences=[], prices=RUSUTSU)
    assert check_card(named, evidence_text='', allowed_ids=set(), check_budget=False) == []


def test_lodging_and_lesson_only_when_needed():
    lodging = {'item': '酒店双人房·1晚', 'price': 30000, 'currency': 'JPY', 'category': 'lodging',
               'unit': '间/晚', 'unit_basis': 'per_room'}
    items, missing = select_budget(RUSUTSU + [lodging], '2027-01-15', level='进阶', days=1)
    assert {item.category for item in items} == {'lift_ticket', 'rental', 'transfer'}
    assert '住宿' not in missing and '教学' not in missing
    items, missing = select_budget(RUSUTSU + [lodging], '2027-01-15', level='进阶', days=2)
    assert 'lodging' in {item.category for item in items} and '住宿' not in missing


def test_usable_price_is_preferred_over_a_reference_price():
    prices = [
        {'item': '全天雪票·周末', 'price': 650, 'category': 'lift_ticket'},
        {'item': '参考雪票', 'price': 999, 'category': 'lift_ticket', 'budget_usable': False},
    ]
    items, total = pick_budget(prices, '周末')
    assert [item.item for item in items] == ['全天雪票·周末']
    assert items[0].counted is True and total == 650


def test_reference_prices_are_listed_but_not_summed():
    prices = [dict(row, budget_usable=False) for row in RUSUTSU]
    card = build_card(resort_name='样板雪场', trip=RUSUTSU_TRIP, evidences=[], prices=prices)
    assert [item.category for item in card.budget.items] == [
        'lift_ticket', 'rental', 'transfer', 'lesson']
    assert all(item.counted is False for item in card.budget.items)
    assert card.budget.subtotals == [] and card.budget.total == 0
    assert '参考单价' in card.summary and '价格待确认' not in card.summary
    assert all('不计入预算小计' in item.note for item in card.budget.items)
    assert '雪票' not in card.budget.missing and '住宿' in ''.join(card.budget.missing)
    named = build_card(resort_name='万龙滑雪场', trip=RUSUTSU_TRIP, evidences=[], prices=prices)
    assert check_card(named, evidence_text='', allowed_ids=set(), check_budget=False) == []


@pytest.mark.local_data('resorts/wanlong.yaml')
def test_legacy_wanlong_card_budget_unchanged():
    card = build_card(resort_name='万龙滑雪场', trip=TRIP, evidences=EVIDENCES, prices=PRICES)
    assert [(item.amount, item.counted) for item in card.budget.items] == [(650, True), (200, True)]
    assert [(s.currency, s.total) for s in card.budget.subtotals] == [('CNY', 850)]
    assert card.summary.endswith('｜已知单价小计 850 元（非全行程预算）')
    assert [item.note for item in card.budget.items] == ['人/天｜录入 2026-04-06'] * 2
    assert card.budget.missing == ['交通接驳', '住宿']   # 两日行程，库里没有住宿价
    assert any('买错票种' in tip.text and tip.source for tip in card.tips)


@pytest.mark.local_data('resorts/rusutsu.yaml')
def test_curated_tips_follow_the_named_resort_not_the_price_rows():
    card = build_card(resort_name='留寿都滑雪场', trip=RUSUTSU_TRIP, evidences=[], prices=[])
    texts = [tip.text for tip in card.tips]
    assert any('双板' in text and '不能用单板' in text for text in texts)
    assert any('房价' in text and 'Westin' in text for text in texts)
    assert check_card(card, evidence_text='', allowed_ids=set(), check_budget=False) == []
    blank = build_card(resort_name='样板雪场', trip=RUSUTSU_TRIP, evidences=[], prices=[])
    assert blank.tips == []
