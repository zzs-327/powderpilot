"""向量库封装：Chroma 集合的获取与向量召回。

入库与检索共用同一个集合名与嵌入模型，否则要么报维度错，要么永远查不到。
"""
from langchain_chroma import Chroma
from langchain_core.documents import Document

from config.config import settings


class ResortVectorStore:
    """Chroma 向量库封装：集合获取 + 带 metadata 过滤的向量召回"""

    @staticmethod
    def get_db(embedding=None) -> Chroma:
        """获取（或创建）Chroma 集合，本地持久化到 settings.CHROMA_DIR

        :param embedding: 可注入自定义嵌入模型（测试用），默认走 Ollama 本地嵌入
        """
        if embedding is None:
            from rag.embedding import OllamaEmbeddingFactory

            embedding = OllamaEmbeddingFactory.create()
        return Chroma(
            collection_name=settings.CHROMA_COLLECTION,
            embedding_function=embedding,
            persist_directory=settings.CHROMA_DIR,
        )

    @staticmethod
    def vector_search(query: str, resort: str = '', board: str = '',
                      k: int | None = None, embedding=None) -> list[Document]:
        """向量召回（语义相似）+ 距离阈值拒答

        距离阈值是"反幻觉"的第一道闸：库里没有的内容（如问二世谷）距离会明显偏大，
        直接丢掉，让 Agent 有机会说"暂无可靠来源"，而不是硬答一个万龙的价格。
        """
        from rag.retriever import ResortRetriever

        k = k or settings.RETRIEVE_TOP_K
        store = ResortVectorStore.get_db(embedding)
        where = ResortRetriever.build_where(resort, board)
        hits = store.similarity_search_with_score(query, k=k, filter=where)
        return [doc for doc, distance in hits if distance <= settings.MAX_DISTANCE]

    @staticmethod
    def all_documents(embedding=None) -> list[Document]:
        """把集合里的全部 chunk 取到内存，供 BM25 建索引（几百条量级毫秒级）"""
        store = ResortVectorStore.get_db(embedding)
        data = store.get(include=['documents', 'metadatas'])
        return [Document(page_content=text, metadata=meta or {})
                for text, meta in zip(data.get('documents') or [],
                                      data.get('metadatas') or [])]


# ---------------- 兼容旧调用 ----------------
get_vector_store = ResortVectorStore.get_db
