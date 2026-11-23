"""价格口径：校验、文本渲染、同步落库（临时 SQLite，不连现有 MySQL）"""
import datetime as dt
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rag.parser import ResortParser, list_resort_ids
from rag.pricing import narrow_prices, price_evidence_text, price_text, reference_price_notice, validate_price, validate_prices

RESORT_DIR = Path(__file__).resolve().parents[1] / 'data' / 'resorts'

JPY_SHUTTLE = {
    'item': '新千岁机场→留寿都 冬季接驳·网上预约', 'price': 5000, 'currency': 'JPY',
    'category': 'transfer', 'unit': '单程每座', 'unit_basis': 'per_seat_one_way',
    'valid_from': dt.date(2026, 11, 28), 'valid_to': '2027-03-30', 'tax_status': 'included',
    'includes': ['单程一个座位', '税'], 'excludes': ['unknown'], 'budget_usable': False,
}


def _legacy_text(name: str, row: dict, updated: str) -> str:
    """改造前 rag/parser.py 与 agent/tools.py 的原始写法"""
    return (f'{name}{row.get("item", "")}：{row.get("price", "?")} 元'
            f'/{row.get("unit", "次")}（录入日期 {updated}）')


def _yaml(resort_id: str) -> dict:
    return yaml.safe_load((RESORT_DIR / f'{resort_id}.yaml').read_text(encoding='utf-8')) or {}


@pytest.mark.parametrize('resort_id', list_resort_ids())
def test_existing_yaml_prices_are_valid_and_text_unchanged(resort_id):
    data = _yaml(resort_id)
    validate_prices(resort_id, data.get('prices') or [])
    for row in data.get('prices') or []:
        text = price_text(data['name'], row, '2026-04-06')
        code = str(row.get('currency') or 'CNY').upper()
        if code == 'CNY':
            assert text == _legacy_text(data['name'], row, '2026-04-06')
        else:
            assert f'{row["price"]} {code}' in text and '元' not in text


@pytest.mark.local_data('resorts/wanlong.yaml')
def test_parser_price_chunks_keep_legacy_text():
    docs = [d for d in ResortParser.parse_resort('wanlong') if d.metadata['doc_type'] == 'price']
    data = _yaml('wanlong')
    assert len(docs) == len(data['prices'])
    for doc, row in zip(docs, data['prices']):
        assert doc.page_content == _legacy_text(data['name'], row, doc.metadata['date'])


def test_foreign_currency_text_never_says_yuan():
    text = price_text('留寿都', JPY_SHUTTLE, '2026-10-11')
    assert '5000 JPY/单程每座' in text and '元' not in text
    for phrase in ('适用 2026-11-28～2027-03-30', '往返需另计', '含税', '不含项未逐项说明', '不计入预算小计'):
        assert phrase in text
    lesson = {'item': '英语私教2小时', 'price': 51000, 'currency': 'JPY', 'unit': '每教练',
              'unit_basis': 'per_instructor', 'tax_status': 'unknown'}
    assert '非每人价' in price_text('留寿都', lesson, '') and '税费待确认' in price_text('留寿都', lesson, '')


@pytest.mark.parametrize('patch, message', [
    ({'price': 0}, '正整数'), ({'price': None}, '正整数'), ({'price': True}, '正整数'),
    ({'price': '5000'}, '正整数'), ({'currency': 'jpy'}, 'ISO'), ({'category': 'food'}, 'category'),
    ({'unit_basis': 'per_group'}, 'unit_basis'), ({'tax_status': 'yes'}, 'tax_status'),
    ({'valid_from': '2027-04-01'}, '晚于'), ({'valid_to': '2027/03/30'}, 'YYYY-MM-DD'),
    ({'includes': '税'}, 'includes'), ({'budget_usable': 'false'}, 'budget_usable'),
    ({'data_status': 'checked'}, 'data_status'), ({'item': 'x' * 65}, 'item 超过'),
])
def test_invalid_rows_are_rejected(patch, message):
    errors = validate_price({**JPY_SHUTTLE, **patch})
    assert any(message in error for error in errors), errors


def test_valid_row_passes():
    assert validate_price(JPY_SHUTTLE) == []


def test_reference_notice_only_when_a_row_cannot_enter_budget():
    assert reference_price_notice([{'budget_usable': False}])
    assert '完整旅行总价' in reference_price_notice([JPY_SHUTTLE])
    assert reference_price_notice([{'price': 560}]) == ''
    assert reference_price_notice([]) == ''


def test_usage_conditions_stay_out_of_chunk_text():
    row = {**JPY_SHUTTLE, 'conditions': ['出行前7日网上预约']}
    chunk = price_text('留寿都滑雪场', row, '2026-10-11')
    shown = price_evidence_text('留寿都滑雪场', row, '2026-10-11')
    assert '使用条件' not in chunk and '出行前7日网上预约' not in chunk
    assert shown.startswith(chunk) and '使用条件：出行前7日网上预约' in shown
    plain = {'item': '雪票', 'price': 560, 'unit': '人/天'}
    assert price_evidence_text('万龙滑雪场', plain, '2026-04-06') == price_text(
        '万龙滑雪场', plain, '2026-04-06')


RUSUTSU_ROWS = [
    {'item': '新千岁机场→留寿都·冬季网上单程', 'category': 'transfer', 'price': 5000},
    {'item': '成人5小时雪票·常规季·网上', 'category': 'lift_ticket', 'price': 11600},
    {'item': '标准单板套装·成人1日', 'category': 'rental', 'price': 10800},
    {'item': '英语私教2小时·常规季前段', 'category': 'lesson', 'price': 51000},
]


def test_category_query_does_not_dump_the_whole_price_list():
    rows, mode = narrow_prices(RUSUTSU_ROWS, '接驳')
    assert mode == 'category'
    assert [row['price'] for row in rows] == [5000]
    rows, mode = narrow_prices(RUSUTSU_ROWS, '单板租赁')
    assert mode == 'category' and [row['item'] for row in rows] == ['标准单板套装·成人1日']
    assert narrow_prices(RUSUTSU_ROWS, '双板租赁') == ([], 'board_gap')
    assert narrow_prices(RUSUTSU_ROWS, '住宿') == ([], 'category_gap')
    assert narrow_prices(RUSUTSU_ROWS, '平日') == ([], '')


@pytest.fixture
def session():
    import models  # noqa: F401
    from config.database import Base

    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _resort(prices: list[dict]) -> dict:
    return {'id': 'pricing_test', 'name': '测试雪场', 'region': '测试',
            'provenance': {'data_status': 'transcribed'}, 'prices': prices}


def test_sync_writes_pricing_fields(session):
    from models.price import Price
    from scripts.sync_to_mysql import sync_resort

    legacy = {'item': '全天雪票·平日', 'price': 560, 'unit': '人/天'}
    sync_resort(session, _resort([legacy, {**JPY_SHUTTLE, 'data_status': 'verified'}]))
    rows = session.execute(select(Price).order_by(Price.id)).scalars().all()
    assert [(r.price, r.currency, r.budget_usable, r.data_status) for r in rows] == [
        (560, None, None, 'transcribed'), (5000, 'JPY', False, 'verified')]
    assert rows[1].valid_from == dt.date(2026, 11, 28) and rows[1].valid_to == dt.date(2027, 3, 30)
    assert rows[1].unit_basis == 'per_seat_one_way' and rows[1].excludes == ['unknown']


def test_sync_rejects_bad_rows_before_deleting_old_prices(session):
    from models.price import Price
    from scripts.sync_to_mysql import sync_resort

    sync_resort(session, _resort([{'item': '全天雪票', 'price': 560, 'unit': '人/天'}]))
    session.commit()
    with pytest.raises(ValueError, match=r'pricing_test\.prices\[0\]'):
        sync_resort(session, _resort([{'item': '住宿', 'price': None, 'unit': '晚'}]))
    session.rollback()
    assert [r.price for r in session.execute(select(Price)).scalars()] == [560]
