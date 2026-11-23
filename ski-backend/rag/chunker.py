"""中文分块：把长文档切成 300~500 字的 chunk，并保留/补齐 metadata。

YAML 生成的语义单元通常很短（一条雪道/一条价格），不会被再次切碎；
只有长文手册（md/pdf）才会走分块器。
"""
from langchain_core.documents import Document

from config.config import settings

# 中文优先按段落/句号切，避免把一句话劈开
DEFAULT_SEPARATORS = ['\n\n', '\n', '。', '！', '？', '；', '.', ' ', '']


class ChunkUtil:
    """递归字符文本分块工具（中文场景：300~500 字、重叠约 1/5）"""

    # 入库前必须齐全的 metadata（缺了就没法分层与溯源）
    REQUIRED_METADATA = ('resort_id', 'doc_type', 'source_type', 'date', 'chunk_id')

    @staticmethod
    def _splitter(chunk_size: int, chunk_overlap: int):
        # 懒加载 langchain 依赖，避免影响无关模块导入
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=DEFAULT_SEPARATORS,
        )

    @staticmethod
    def _reindex(docs: list[Document]) -> list[Document]:
        """被切碎的文档会共享同一个 chunk_id，这里补 -pN 后缀

        RRF 融合用 chunk_id 去重，如果两段不同的内容共用一个 id，
        会被误判成同一条证据而丢掉一段。
        """
        seen: dict[str, int] = {}
        for doc in docs:
            base = doc.metadata.get('chunk_id', 'unknown')
            count = seen.get(base, 0)
            seen[base] = count + 1
            if count:
                doc.metadata['chunk_id'] = f'{base}-p{count}'
        return docs

    @staticmethod
    def split_documents(docs: list[Document],
                        chunk_size: int | None = None,
                        chunk_overlap: int | None = None) -> list[Document]:
        """分块：短文档原样保留，长文档交给 splitter，metadata 自动继承

        注意：splitter 必须"真要切长文时"才构造。
        langchain_text_splitters 是重依赖，本机导入它会卡住；
        YAML 生成的语义单元（一条雪道/一条价格）通常都短于 chunk_size，
        无条件构造会让"全部原样保留"的入库也白白踩上这个导入。
        """
        chunk_size = chunk_size or settings.RAG_CHUNK_SIZE
        chunk_overlap = chunk_overlap or settings.RAG_CHUNK_OVERLAP
        splitter = None
        result: list[Document] = []
        for doc in docs:
            if len(doc.page_content) <= chunk_size:
                result.append(doc)
                continue
            if splitter is None:
                splitter = ChunkUtil._splitter(chunk_size, chunk_overlap)
            result.extend(splitter.split_documents([doc]))
        return ChunkUtil._reindex(result)

    @staticmethod
    def validate_metadata(docs: list[Document]) -> list[Document]:
        """入库前体检：缺 source_type/date 的 chunk 直接报错

        宁可入库失败，也不要让"无来源信息"混进知识库——那样溯源机制就形同虚设。
        """
        problems: list[tuple[str, list[str]]] = []
        for doc in docs:
            missing = [key for key in ChunkUtil.REQUIRED_METADATA
                       if not doc.metadata.get(key)]
            if missing:
                problems.append((str(doc.metadata.get('chunk_id', '?')), missing))
        if problems:
            detail = '；'.join(f'{cid} 缺 {",".join(miss)}' for cid, miss in problems[:5])
            raise ValueError(f'{len(problems)} 条 chunk 的 metadata 不完整：{detail}')
        return docs


# ---------------- 兼容旧调用 ----------------
split_documents = ChunkUtil.split_documents
validate_metadata = ChunkUtil.validate_metadata
