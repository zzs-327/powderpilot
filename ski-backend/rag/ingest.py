"""文档向量化流水线：解析 -> 分块 -> metadata 体检 -> 入 Chroma。

用法（在 ski-backend 目录下）：
    python -m rag.ingest --dry-run          # 只解析与体检，不调用嵌入模型（推荐先跑）
    python -m rag.ingest --rebuild          # 全量重建（清空集合后重灌）
    python -m rag.ingest wanlong            # 只重灌指定雪场（按 chunk_id 幂等覆盖）
    python -m rag.ingest                    # 全量增量重灌

幂等说明：chunk_id 作为向量库主键，重灌同一条不会产生重复数据。
"""
import argparse
import collections

from langchain_core.documents import Document

from config.exceptions import ServiceException
from rag.chunker import ChunkUtil
from rag.parser import ResortParser, ResortRegistry
from rag.store import ResortVectorStore


class RagIngestService:
    """雪场档案入库流水线：解析 -> 分块 -> 体检 -> 写入向量库"""

    @staticmethod
    def build_documents(resort_ids: list[str] | None = None,
                        chunk_size: int | None = None) -> list[Document]:
        """加载 -> 分块 -> metadata 体检（不涉及嵌入，可离线跑）"""
        docs = ResortParser.parse_all(resort_ids)
        chunks = ChunkUtil.split_documents(docs, chunk_size=chunk_size)
        return ChunkUtil.validate_metadata(chunks)

    @staticmethod
    def report(chunks: list[Document]) -> None:
        """打印入库报告，方便肉眼确认（这一步别省，元数据错了一眼就能看出来）"""
        by_type = collections.Counter(chunk.metadata['doc_type'] for chunk in chunks)
        by_resort = collections.Counter(chunk.metadata['resort_id'] for chunk in chunks)
        by_level = collections.Counter(chunk.metadata['level'] for chunk in chunks)
        by_source = collections.Counter(chunk.metadata['source_type'] for chunk in chunks)
        print(f'共 {len(chunks)} 条 chunk')
        print('  按类型：', dict(by_type))
        print('  按雪场：', dict(by_resort))
        print('  按水平：', dict(by_level))
        print('  按来源：', dict(by_source))
        print('  样例：')
        for chunk in chunks[:3]:
            meta = chunk.metadata
            print(f'    [{meta["chunk_id"]}] {meta["doc_type"]}/{meta["level"]}/'
                  f'{meta["board_type"]} | {meta["source_type"]} | {meta["date"]}'
                  f' | {chunk.page_content[:60]}')

    @staticmethod
    def ingest(resort_ids: list[str] | None = None, rebuild: bool = False,
               embedding=None, dry_run: bool = False,
               chunk_size: int | None = None) -> int:
        """执行入库，返回写入的 chunk 数；重建仅允许全量输入。"""
        if rebuild and resort_ids:
            raise ServiceException('rebuild 不能与 resort_ids 组合；全量重建请不指定雪场')

        chunks = RagIngestService.build_documents(resort_ids, chunk_size=chunk_size)
        if not chunks:
            raise ServiceException('没有可入库的文档，未删除集合或写入向量库')
        RagIngestService.report(chunks)
        if dry_run:
            print('（--dry-run：未调用嵌入模型，未写入向量库）')
            return len(chunks)

        try:
            store = ResortVectorStore.get_db(embedding)
            ids = [chunk.metadata['chunk_id'] for chunk in chunks]
            texts = [chunk.page_content for chunk in chunks]
            metadatas = [chunk.metadata for chunk in chunks]
            # 嵌入失败时保留原集合；空或不完整的结果也不能用于重建。
            embeddings = store._embedding_function.embed_documents(texts)
            if len(embeddings) != len(chunks) or any(len(vector) == 0 for vector in embeddings):
                raise ServiceException('嵌入结果为空或数量不匹配，未删除集合或写入向量库')

            if rebuild:
                store.delete_collection()
                print('已清空原集合')
                store = ResortVectorStore.get_db(embedding)

            # upsert 会覆盖已有 ID，相加只是不考虑覆盖的数量上界。
            current_count = store._collection.count()
            upper_bound = current_count + len(chunks)
            if upper_bound > 450:
                print(f'⚠️  向量库容量提示：当前 {current_count} 条，本次 {len(chunks)} 条；'
                      f'不考虑 ID 覆盖的数量上界为 {upper_bound} 条，并非 upsert 后实际最终量。')
                print('   保留 Chroma；容量与删除风险需结合版本、环境和实测评估，不能保证任意条数稳定。')

            # 按 chunk_id 幂等覆盖，不在增量写入前删除已有记录。
            store._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )
        except ServiceException:
            raise
        except Exception as exc:
            raise ServiceException(f'向量入库失败：{exc}') from exc

        print(f'入库完成：{len(chunks)} 条 -> {store._collection.name}')
        return len(chunks)


def main() -> None:
    from utils.console import enable_utf8_console

    enable_utf8_console()
    parser = argparse.ArgumentParser(description='把雪场档案灌入 Chroma 向量库')
    parser.add_argument('resort_ids', nargs='*', help='雪场 id，留空表示全部')
    parser.add_argument('--rebuild', action='store_true', help='清空集合后全量重建')
    parser.add_argument('--dry-run', action='store_true', help='只解析与体检，不嵌入')
    parser.add_argument('--chunk-size', type=int, default=None, help='分块大小')
    args = parser.parse_args()

    print('可用雪场：', ResortRegistry.list_resort_ids())
    RagIngestService.ingest(args.resort_ids or None, rebuild=args.rebuild,
                            dry_run=args.dry_run, chunk_size=args.chunk_size)


# ---------------- 兼容旧调用 ----------------
build_documents = RagIngestService.build_documents
ingest = RagIngestService.ingest
report = RagIngestService.report


if __name__ == '__main__':
    main()
