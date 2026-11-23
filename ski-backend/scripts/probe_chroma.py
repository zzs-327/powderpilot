"""探针：复现坑 #24 —— chromadb 1.5.9 向量数超阈值时 HNSW 段持久化失败。

用假向量（随机 1024 维）直写原生 chromadb，不依赖 Ollama / langchain。
用法：
    python scripts/probe_chroma.py [N]     # 默认 600 条
"""
import os
import random
import subprocess
import shutil
import sys

import chromadb

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_DIR = os.path.join(HERE, '..', 'probe_tmp')

READER = '''
import chromadb, sys
c = chromadb.PersistentClient(path=sys.argv[1])
col = c.get_collection("probe")
print("new-process count:", col.count())
r = col.query(query_embeddings=[[0.1]*1024], n_results=3)
print("new-process query ok:", len(r["ids"][0]))
'''


def main(n: int = 600, batch: int = 0) -> None:
    # 1. 清掉旧探针目录，保证每次都是全新建库
    d = os.path.abspath(PROBE_DIR)
    if os.path.isdir(d):
        shutil.rmtree(d)

    # 2. 写入 N 条假向量（维度与 HNSW 段行为无关，随机即可）
    #    batch=0 一次性写入；batch>0 模拟 langchain-chroma 分批 add 的真实路径
    c = chromadb.PersistentClient(path=d)
    col = c.get_or_create_collection('probe')
    random.seed(42)
    ids = [f'id{i}' for i in range(n)]
    vecs = [[random.random() for _ in range(1024)] for _ in range(n)]
    docs = [f'doc {i}' for i in range(n)]
    metas = [{'r': 'wanlong'}] * n
    if batch:
        for i in range(0, n, batch):
            col.upsert(ids=ids[i:i + batch], embeddings=vecs[i:i + batch],
                       documents=docs[i:i + batch], metadatas=metas[i:i + batch])
    else:
        col.upsert(ids=ids, embeddings=vecs, documents=docs, metadatas=metas)
    print(f'chromadb {chromadb.__version__} upserted {col.count()} (batch={batch or "single"})')

    # 3. 同进程查一次（能查 ≠ 持久化成功）
    r = col.query(query_embeddings=[[0.5] * 1024], n_results=3)
    print('same-process query ok:', len(r['ids'][0]))

    # 4. 新进程加载——坑 #24 的案发现场就在这一步
    p = subprocess.run([sys.executable, '-c', READER, d],
                       capture_output=True, text=True)
    print('--- new process ---')
    print(p.stdout.strip())
    if p.returncode != 0:
        print('FAILED, stderr tail:')
        print(p.stderr.strip()[-800:])
    else:
        print('PASS')


def probe_langchain(n: int, batch: int, mode: str = 'ingest') -> None:
    """走 langchain-chroma 的真实路径复现（假 embedding，不调 Ollama）

    mode:
      ingest  = 复刻真实 ingest：delete(ids) + add_documents（幂等清理路径）
      add     = 纯 add_documents（无 delete）
      incr    = 先灌 n//2 条，再增量 add 其余（模拟只重灌一个雪场）
      upsert  = 修复方案：原生 collection.upsert（同 id 覆盖，无 delete）
      rebuild = rag.ingest --rebuild 的真实路径：delete_collection + 重新 upsert
                （坑 #24 的修复只改了 delete(ids)，**这一条没被测过**，而它是文档里
                 推荐的"重建向量库"命令，必须确认是否也会炸）
    """
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings

    class FakeEmbedding(Embeddings):
        """确定性假嵌入：同 id 永远同向量，跨进程可复现"""

        def _vec(self, text: str) -> list[float]:
            seed = sum(ord(ch) for ch in text)
            import random as _r
            r = _r.Random(seed)
            return [r.random() for _ in range(1024)]

        def embed_documents(self, texts):
            return [self._vec(t) for t in texts]

        def embed_query(self, text):
            return self._vec(text)

    d = os.path.abspath(PROBE_DIR)
    if os.path.isdir(d):
        shutil.rmtree(d)
    emb = FakeEmbedding()
    docs = [Document(page_content=f'doc {i}', metadata={'r': 'wanlong'}) for i in range(n)]
    ids = [f'probe-{i}' for i in range(n)]
    store = Chroma(collection_name='probe', embedding_function=emb, persist_directory=d)
    if mode == 'ingest':
        # 复刻真实 ingest：先 delete 同 id（幂等清理），再 add_documents
        try:
            store.delete(ids=ids)
        except Exception as exc:
            print('delete (expect fail on empty):', exc)
        store.add_documents(docs, ids=ids)
    elif mode == 'upsert':
        # 修复方案：原生 upsert，同 id 覆盖，天然幂等，不走 delete
        texts = [doc.page_content for doc in docs]
        store._collection.upsert(
            ids=ids,
            embeddings=emb.embed_documents(texts),
            documents=texts,
            metadatas=[doc.metadata for doc in docs],
        )
    elif mode == 'incr':
        half = n // 2
        store.add_documents(docs[:half], ids=ids[:half])
        store.add_documents(docs[half:], ids=ids[half:])
    elif mode == 'rebuild':
        # 复刻 rag.ingest --rebuild：先整体删集合，再重新建 + upsert
        store.delete_collection()
        store = Chroma(collection_name='probe', embedding_function=emb, persist_directory=d)
        texts = [doc.page_content for doc in docs]
        store._collection.upsert(
            ids=ids,
            embeddings=emb.embed_documents(texts),
            documents=texts,
            metadatas=[doc.metadata for doc in docs],
        )
    else:
        store.add_documents(docs, ids=ids)
    print(f'langchain-chroma mode={mode} -> {store._collection.count()} 条')

    READER_LC = '''
import sys
from langchain_chroma import Chroma
class FakeEmbedding:
    def _vec(self, text):
        import random as _r
        r = _r.Random(sum(ord(ch) for ch in text))
        return [r.random() for _ in range(1024)]
    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]
    def embed_query(self, text):
        return self._vec(text)
store = Chroma(collection_name="probe", embedding_function=FakeEmbedding(), persist_directory=sys.argv[1])
hits = store.similarity_search_with_score("doc 1", k=3)
print("new-process query ok:", len(hits))
'''
    p = subprocess.run([sys.executable, '-c', READER_LC, d], capture_output=True, text=True)
    print('--- new process (langchain-chroma) ---')
    print(p.stdout.strip())
    if p.returncode != 0:
        print('FAILED, stderr tail:')
        print(p.stderr.strip()[-500:])
    else:
        print('PASS')


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--langchain' in args:
        args = [a for a in args if a != '--langchain']
        mode = args.pop(0) if args and args[0] in ('ingest', 'add', 'incr', 'upsert', 'rebuild') else 'ingest'
        probe_langchain(int(args[0]) if args else 600, 0, mode)
    else:
        main(int(args[0]) if args else 600,
             int(args[1]) if len(args) > 1 else 0)
