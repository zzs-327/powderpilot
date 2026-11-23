import pytest

from scripts import resort_scorecard as sc


@pytest.mark.local_data('resorts/beidahu.yaml')
def test_benchmark_resort_meets_every_required_item_except_own_photos():
    checks = {c.name: c for c in sc.score(sc.BENCHMARK)}
    missing = [name for name, c in checks.items() if not c.ok]
    assert missing in ([], ['可直接显示的照片'])
    assert checks['官方视频'].detail == '1 条'


@pytest.mark.local_data('resorts/beidahu.yaml', 'resorts/duolemeidi.yaml')
def test_scorecard_flags_gaps_for_thin_resort():
    checks = {c.name: c for c in sc.score('duolemeidi')}
    assert not checks['官方视频'].ok
    assert not checks['分层建议'].ok
    table = sc.render({sc.BENCHMARK: sc.score(sc.BENCHMARK), 'duolemeidi': sc.score('duolemeidi')})
    assert '（样板）' in table and 'duolemeidi' in table
