from agent.tools import get_resort_lifts, TOOLS
from agent.middleware import EVIDENCE_BLOCK
from rag.parser import ResortParser


def test_lifts_empty_is_local_archive_evidence_not_official():
    text = get_resort_lifts.func('太舞')
    assert '暂未收录' in text and '不代表现实没有缆车' in text
    assert 'taiwu.yaml' in text and '非官方运营资料' in text
    assert '[E' in text
    assert '22 条' not in text and '16 公里' not in text


def test_populated_lifts_preserve_parsed_evidence():
    text = get_resort_lifts.func('duolemeidi')
    docs = [d for d in ResortParser.parse_resort('duolemeidi') if d.metadata['doc_type'] == 'lift']
    assert docs and '1号缆车' in text and '1328' in text
    for doc in docs:
        assert doc.page_content in text
        assert str(doc.metadata['source']) in text
        assert str(doc.metadata['date']) in text
    assert '不保证全量或实时开放' in text


def test_unknown_and_ambiguous_inputs_never_borrow():
    for term in ['../../wanlong', '二世谷', '太舞和万龙', '万龙滑雪场今天怎么样']:
        text = get_resort_lifts.func(term)
        assert '未识别' in text and '[E' not in text


def test_lift_tool_registered_unique_block():
    assert get_resort_lifts in TOOLS
    assert EVIDENCE_BLOCK['get_resort_lifts'] == 600
    assert len(set(EVIDENCE_BLOCK.values())) == len(EVIDENCE_BLOCK)
