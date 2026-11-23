"""名称错序只补查同场候选价目，不宣称目标价已命中。"""
from contextlib import nullcontext
from agent import tools


def test_mismatched_item_returns_same_resort_candidates(monkeypatch):
    calls = []
    price = dict(resort_name="万龙", item="全天雪票·自带板·平日", price=560, unit="人/天", updated_at="2026-06-11", expire_at="2026-09-09", source_type="A", source="策展来源", data_status="transcribed")
    class Service:
        def __init__(self, session): pass
        def get_prices(self, resort, item=""):
            calls.append((resort, item))
            return [] if item else [price]
    monkeypatch.setattr(tools, "SessionLocal", lambda: nullcontext(None))
    monkeypatch.setattr(tools, "ResortService", Service)
    result = tools.get_resort_price.func("wanlong", "自带板平日全天雪票")
    assert calls == [("wanlong", "自带板平日全天雪票"), ("wanlong", "")]
    assert "不代表请求项已命中" in result
    assert "560" in result and "[E" in result and "策展来源" in result


def test_missing_resort_never_borrows_other_prices(monkeypatch):
    calls = []
    class Service:
        def __init__(self, session): pass
        def get_prices(self, resort, item=""):
            calls.append(resort)
            return []
    monkeypatch.setattr(tools, "SessionLocal", lambda: nullcontext(None))
    monkeypatch.setattr(tools, "ResortService", Service)
    result = tools.get_resort_price.func("unknown", "全天雪票")
    assert calls == ["unknown", "unknown"]
    assert "未查询到" in result and "[E" not in result


def test_named_board_without_a_price_does_not_borrow_the_other_board(monkeypatch):
    rows = [
        dict(resort_name='留寿都滑雪场', item='标准单板套装·成人1日', price=10800, unit='套/日',
             updated_at='2026-10-11', expire_at='', source_type='A', source='官网', data_status='verified',
             category='rental', currency='JPY'),
        dict(resort_name='留寿都滑雪场', item='成人5小时雪票·常规季·网上', price=11600, unit='人/5小时',
             updated_at='2026-10-11', expire_at='', source_type='A', source='官网', data_status='verified',
             category='lift_ticket', currency='JPY'),
    ]
    class Service:
        def __init__(self, session): pass
        def get_prices(self, resort, item=''):
            return [] if item else rows
    monkeypatch.setattr(tools, 'SessionLocal', lambda: nullcontext(None))
    monkeypatch.setattr(tools, 'ResortService', Service)
    alpine = tools.get_resort_price.func('留寿都', '双板租赁')
    assert '另一种板型' in alpine and '标准单板套装' not in alpine and '[E' not in alpine
    assert '不能用单板' in alpine
    lodging = tools.get_resort_price.func('留寿都', '住宿')
    assert '其他类别' in lodging and '房价' in lodging and '10800' not in lodging and '[E' not in lodging
    shuttle_rows = [dict(rows[0], item='新千岁机场→留寿都·冬季网上单程', price=5000, category='transfer', unit='单程每座')]
    class Shuttle:
        def __init__(self, session): pass
        def get_prices(self, resort, item=''):
            return [] if item else shuttle_rows + rows
    monkeypatch.setattr(tools, 'ResortService', Shuttle)
    shuttle = tools.get_resort_price.func('留寿都', '接驳')
    assert '5000' in shuttle and '10800' not in shuttle and '11600' not in shuttle
    assert '不代表请求项已命中' in shuttle


def test_collected_resort_without_price_is_not_unknown(monkeypatch):
    class Service:
        def __init__(self, session): pass
        def get_prices(self, resort, item=""): return []
    monkeypatch.setattr(tools, "SessionLocal", lambda: nullcontext(None))
    monkeypatch.setattr(tools, "ResortService", Service)
    for term in ('北大湖', 'beidahu'):
        result = tools.get_resort_price.func(term, "自带板平日全天")
        assert "本库已收录" in result and "暂无价格" in result
        assert "未查询到" not in result
