"""统一验收策略的纯函数契约，不依赖模型或配置。"""
import pytest

from scripts.eval_policy import passed


def row(**changes):
    value = dict(kind='run_parameter', answered=True, citation_valid=True,
                 grounded=True, cited=True, stale_notice=True, refused=False,
                 degraded=False, error='', hit=True, price_question=False)
    value.update(changes)
    return value


def test_fact_requires_citation():
    assert passed(row())
    assert not passed(row(cited=False))


def test_price_requires_staleness_notice():
    assert passed(row(kind='price_mixed', price_question=True))
    assert not passed(row(kind='price_mixed', price_question=True, stale_notice=False))
    assert passed(row(stale_notice=False))


@pytest.mark.parametrize('kind', ['refusal', 'price_missing', 'lift_missing', 'run_missing'])
def test_expected_refusal_allows_no_citation_but_requires_refused(kind):
    assert passed(row(kind=kind, cited=False, refused=True,
                      price_question=True, stale_notice=False))
    assert not passed(row(kind=kind, cited=False, refused=False))
    assert not passed(row(kind=kind, cited=True, refused=False))


@pytest.mark.parametrize('changes', [
    {'degraded': True}, {'error': 'RuntimeError'}, {'answered': False},
    {'citation_valid': False}, {'grounded': False}, {'hit': False},
])
def test_failure_conditions_never_count_as_success(changes):
    assert not passed(row(**changes))
    assert not passed(row(kind='refusal', refused=True, cited=False, **changes))


def test_hit_none_keeps_legacy_no_expectation_compatibility():
    assert passed(row(hit=None))
    assert passed(row(kind='refusal', hit=None, refused=True, cited=False))
    assert not passed(row(hit=None, cited=False))
    assert not passed({})
