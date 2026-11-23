"""语义补召回不得突破雪场隔离或未收录守卫。"""
from langchain_core.documents import Document
from rag.retriever import ResortRetriever
from rag.store import ResortVectorStore
from rag.parser import ResortRegistry


def test_typed_recall_is_resort_scoped(monkeypatch):
    local = Document(page_content='本场总览', metadata={'resort_id': 'a', 'is_summary': True, 'doc_type': 'run', 'chunk_id': 'a:summary'})
    foreign = Document(page_content='其他雪场总览', metadata={'resort_id': 'b', 'is_summary': True, 'doc_type': 'run', 'chunk_id': 'b:summary'})
    monkeypatch.setattr(ResortRegistry, 'detect_unknown', lambda q: False)
    monkeypatch.setattr(ResortRegistry, 'detect', lambda q: 'a')
    monkeypatch.setattr(ResortRetriever, 'vector_search', lambda *a, **kw: [])
    monkeypatch.setattr(ResortRetriever, 'bm25_search', lambda *a, **kw: [])
    monkeypatch.setattr(ResortVectorStore, 'all_documents', lambda *a: [local, foreign])
    assert ResortRetriever.retrieve('本场有几条雪道') == [local]


def test_unknown_resort_never_calls_typed_recall(monkeypatch):
    monkeypatch.setattr(ResortRegistry, 'detect_unknown', lambda q: True)
    def fail(*a, **kw):
        raise AssertionError('未收录题不得读取语料')
    monkeypatch.setattr(ResortVectorStore, 'all_documents', fail)
    assert ResortRetriever.retrieve('未收录雪场有几条雪道') == []


def test_short_location_question_recalls_only_local_overview(monkeypatch):
    local = Document(page_content='北大湖位于吉林市', metadata={'resort_id': 'beidahu', 'doc_type': 'overview'})
    foreign = Document(page_content='万龙位于崇礼', metadata={'resort_id': 'wanlong', 'doc_type': 'overview'})
    monkeypatch.setattr(ResortRegistry, 'detect_unknown', lambda q: False)
    monkeypatch.setattr(ResortRegistry, 'detect', lambda q: 'beidahu')
    monkeypatch.setattr(ResortRetriever, 'vector_search', lambda *a, **kw: [])
    monkeypatch.setattr(ResortRetriever, 'bm25_search', lambda *a, **kw: [])
    monkeypatch.setattr(ResortVectorStore, 'all_documents', lambda *a: [foreign, local])
    assert ResortRetriever.retrieve('北大湖在哪？') == [local]
    assert ResortRetriever.retrieve('北大湖雾凇在哪看？') == []
