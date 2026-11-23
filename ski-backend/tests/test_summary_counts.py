"""来源总数与分级合计是两种口径，不能相互替代。"""
import pytest

from rag.ingest import RagIngestService
from scripts.gen_golden_set import build_cases
from scripts.gen_eval_set import load_archives


@pytest.mark.local_data('resorts/jiangjunshan.yaml')
def test_jiangjunshan_summary_preserves_count_disagreement():
    docs = RagIngestService.build_documents(['jiangjunshan'])
    summary = next(d for d in docs if d.metadata.get('is_summary'))
    assert '来源口径总数 61 条' in summary.page_content
    assert '分级统计合计 48 条' in summary.page_content
    assert '不能将分级合计当作雪道总数' in summary.page_content
    assert '共 48 条雪道' not in summary.page_content
    assert summary.metadata['data_status'] == 'transcribed'


def test_generated_cases_are_deterministic_and_cover_all_resorts():
    archives = load_archives()
    cases = build_cases(archives)
    assert cases == build_cases(archives)
    assert any(case['kind'] == 'retrieval' for case in cases)
    assert len({c['id'] for c in cases}) == len(cases)
    for archive in archives:
        assert any(c['id'].startswith(archive['_stem'] + ':') for c in cases)
