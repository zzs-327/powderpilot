"""只加载入库模块与假依赖，不加载 Settings/.env，不访问真实索引。"""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def harness(monkeypatch):
    events = []

    class ServiceException(Exception):
        pass

    # 隔离所有可能间接读取配置或连接外部服务的依赖。
    dependencies = {
        'langchain_core.documents': {'Document': SimpleNamespace},
        'config.exceptions': {'ServiceException': ServiceException},
        'rag.chunker': {'ChunkUtil': SimpleNamespace()},
        'rag.parser': {'ResortParser': SimpleNamespace(), 'ResortRegistry': SimpleNamespace()},
        'rag.store': {'ResortVectorStore': SimpleNamespace()},
    }
    for name, exports in dependencies.items():
        module = ModuleType(name)
        module.__dict__.update(exports)
        monkeypatch.setitem(sys.modules, name, module)

    path = Path(__file__).resolve().parents[1] / 'rag' / 'ingest.py'
    spec = importlib.util.spec_from_file_location('_isolated_ingest_safety', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    chunks = [SimpleNamespace(page_content='雪场资料', metadata={'chunk_id': 'wanlong-1'})]
    state = SimpleNamespace(chunks=chunks, embed_error=None, delete_error=None,
                            upsert_error=None, vectors=[[0.1, 0.2]], current=0,
                            payload=None)

    class FakeEmbedding:
        def embed_documents(self, texts):
            events.append('embed')
            assert texts == [chunk.page_content for chunk in state.chunks]
            if state.embed_error:
                raise state.embed_error
            return state.vectors

    class FakeCollection:
        name = 'fake-resorts'

        def count(self):
            events.append('count')
            return state.current

        def upsert(self, **kwargs):
            events.append('upsert')
            if state.upsert_error:
                raise state.upsert_error
            state.payload = kwargs

    class FakeStore:
        def __init__(self):
            self._embedding_function = FakeEmbedding()
            self._collection = FakeCollection()

        def delete_collection(self):
            events.append('delete')
            if state.delete_error:
                raise state.delete_error
            state.current = 0

    store = FakeStore()

    def get_db(embedding=None):
        events.append('get_db')
        return store

    def build_documents(resort_ids=None, chunk_size=None):
        events.append('build')
        return state.chunks

    monkeypatch.setattr(module.ResortVectorStore, 'get_db', get_db, raising=False)
    monkeypatch.setattr(module.RagIngestService, 'build_documents', build_documents)
    monkeypatch.setattr(module.RagIngestService, 'report', lambda chunks: events.append('report'))
    return SimpleNamespace(service=module.RagIngestService, error=ServiceException,
                           events=events, state=state)


@pytest.mark.parametrize('dry_run', [False, True])
def test_partial_rebuild_rejected_before_loading(harness, dry_run):
    with pytest.raises(harness.error, match='rebuild.*resort_ids'):
        harness.service.ingest(['wanlong'], rebuild=True, dry_run=dry_run)
    assert harness.events == []


@pytest.mark.parametrize('rebuild,dry_run', [(False, False), (True, False), (False, True), (True, True)])
def test_empty_documents_never_open_store(harness, rebuild, dry_run):
    harness.state.chunks = []
    with pytest.raises(harness.error, match='没有可入库的文档'):
        harness.service.ingest(rebuild=rebuild, dry_run=dry_run)
    assert harness.events == ['build']


def test_rebuild_embeds_before_deleting_and_reuses_vectors(harness):
    assert harness.service.ingest(rebuild=True) == 1
    assert harness.events == ['build', 'report', 'get_db', 'embed', 'delete',
                              'get_db', 'count', 'upsert']
    assert harness.state.payload == {
        'ids': ['wanlong-1'], 'documents': ['雪场资料'],
        'metadatas': [{'chunk_id': 'wanlong-1'}],
        'embeddings': harness.state.vectors,
    }


@pytest.mark.parametrize('rebuild', [False, True])
def test_embedding_failure_never_deletes_or_writes(harness, rebuild):
    harness.state.embed_error = RuntimeError('embedding unavailable')
    with pytest.raises(harness.error, match='embedding unavailable'):
        harness.service.ingest(rebuild=rebuild)
    assert harness.events == ['build', 'report', 'get_db', 'embed']


@pytest.mark.parametrize('vectors', [None, [], [[]], [[0.1], [0.2]]])
def test_invalid_embedding_result_never_deletes_or_writes(harness, vectors):
    harness.state.vectors = vectors
    with pytest.raises(harness.error):
        harness.service.ingest(rebuild=True)
    assert harness.events == ['build', 'report', 'get_db', 'embed']


def test_delete_failure_stops_before_reopening_and_writing(harness):
    harness.state.delete_error = RuntimeError('delete denied')
    with pytest.raises(harness.error, match='delete denied'):
        harness.service.ingest(rebuild=True)
    assert harness.events == ['build', 'report', 'get_db', 'embed', 'delete']


def test_incremental_upsert_never_deletes(harness):
    assert harness.service.ingest(['wanlong']) == 1
    assert harness.events == ['build', 'report', 'get_db', 'embed', 'count', 'upsert']


def test_dry_run_never_opens_store_or_embeds(harness):
    assert harness.service.ingest(dry_run=True) == 1
    assert harness.events == ['build', 'report']


def test_upsert_failure_is_reported(harness):
    harness.state.upsert_error = RuntimeError('write denied')
    with pytest.raises(harness.error, match='write denied'):
        harness.service.ingest()
    assert 'delete' not in harness.events


def test_capacity_notice_is_only_an_upper_bound(harness, capsys):
    harness.state.current = 500
    assert harness.service.ingest() == 1
    output = capsys.readouterr().out
    assert '数量上界为 501 条' in output
    assert '并非 upsert 后实际最终量' in output
    assert '不能保证任意条数稳定' in output
    assert '迁移' not in output
    assert '永久不可用' not in output
    assert 'delete' not in harness.events
