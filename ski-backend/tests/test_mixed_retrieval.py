"""混合问题证据合并及挑战题评分契约。"""
from langchain_core.documents import Document
from rag.retriever import ResortRetriever
from scripts import evaluate


def doc(text, chunk='x', resort='wanlong'):
    return Document(page_content=text, metadata={'chunk_id': chunk, 'resort_id': resort})


def test_interleave_keeps_both_intents_and_deduplicates():
    a, b, c = doc('数量', 'a'), doc('价格', 'b'), doc('其他', 'c')
    assert ResortRetriever.interleave([[a, c], [b, a]], 3) == [a, b, c]
    assert ResortRetriever.interleave([], 5) == []


def test_coverage_does_not_pass_when_one_intent_is_missing(monkeypatch):
    monkeypatch.setattr(evaluate, 'retrieve', lambda *a, **kw: [doc('19 条高级道')])
    case = {'id': 'mix', 'question': '数量和价格', 'expect_any': [['19']],
            'require_groups': [['19'], ['560']], 'expected_resort': 'wanlong'}
    assert evaluate.run(cases=[case])['hitk'] == 0
    monkeypatch.setattr(evaluate, 'retrieve', lambda *a, **kw: [doc('19 条高级道'), doc('560 元', 'price')])
    assert evaluate.run(cases=[case])['hitk'] == 1


def test_foreign_evidence_invalidates_challenge_hit(monkeypatch):
    monkeypatch.setattr(evaluate, 'retrieve', lambda *a, **kw: [doc('560 元', resort='taiwu')])
    assert evaluate.run(cases=[{'id': 'isolate', 'question': '票价', 'expect_any': [['560']],
                               'expected_resort': 'wanlong'}])['hitk'] == 0


def test_refusal_audit_allows_unknown_comparisons():
    assert evaluate.audit_refusal_cases([{'id': 'unknown', 'kind': 'refusal',
                                         'question': '二世谷和万龙哪个雪票更便宜'}]) == []
