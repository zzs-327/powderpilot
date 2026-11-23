"""混合检索：向量 + BM25 双路召回 -> RRF 融合 -> metadata 过滤 -> 水平软加权。

为什么混合：
    "金龙道""面条雪"这类专有名词/圈层词，纯向量容易召偏；
    BM25 能精确命中雪道名，两路互补，RRF 只按排名融合，避开分数量纲不可比的问题。

过滤策略（防翻车）：
    board_type 硬过滤（单板用户不看双板教学）
    level      软加权（分层排序）——硬过滤会在库里缺该层内容时直接空结果
"""
import argparse
import datetime as dt
import re

from langchain_core.documents import Document

from config.config import settings
from rag.parser import ResortRegistry
from rag.store import ResortVectorStore

# ---------------- 分词（中文无需 jieba：相邻双字组合足够 BM25 用） ----------------
_ASCII = re.compile(r'[A-Za-z0-9_]+')
_CJK = re.compile(r'[\u4e00-\u9fff]+')

# 统计类意图（问数量/汇总）——这类问题应该优先命中"雪道总览"chunk
# 注意排除"多少钱/多少元/多少价"——那是价格问题，不该把雪道总览置顶
# 「几条/多少」是要总数；「有哪些/坐什么」是要点名。两类不能共用同一种提权。
_COUNT_INTENT = re.compile(r'(几条|几道|几个|多少(?!钱|元|价)|共|总共|数量)')
_LIST_INTENT = re.compile(r'(有哪些|都有什么|都有哪些|坐什么|有什么(?:雪道|高级道|中级道|初级道|索道|缆车))')
_STAT_INTENT = re.compile(r'(几条|几道|几个|多少(?!钱|元|价)|共|总共|数量|有哪些|都有什么|都有哪些)')


def _missing_price_for(text: str, board: str) -> bool:
    """“没有”前面不远处写着这个板型，才算该板型缺价。"""
    index = text.find('没有')
    if index < 0:
        return False
    return board in text[max(0, index - 16):index]


class ResortRetriever:
    """雪场知识混合检索器：向量 + BM25 -> RRF -> 过滤 -> 分层提权"""

    # ---------------- 分词与过滤条件 ----------------
    @staticmethod
    def tokenize(text: str) -> list[str]:
        """英文按词切，中文按相邻双字切（单字词保留）

        只用双字而不是"单字 + 双字"：单字会产生大量噪声匹配
        （"一家公司"里的"一"能匹配到几乎所有文档），把无关问题也召回来。
        """
        tokens = [word.lower() for word in _ASCII.findall(text)]
        for run in _CJK.findall(text):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
        return tokens

    @staticmethod
    def build_where(resort: str = '', board: str = '') -> dict | None:
        """构造 Chroma metadata 过滤条件（课程同款 filter API）"""
        clauses: list[dict] = []
        if resort:
            clauses.append({'resort_id': {'$eq': resort}})
        if board:
            clauses.append({'$or': [{'board_type': {'$eq': board}},
                                    {'board_type': {'$eq': 'all'}}]})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {'$and': clauses}

    @staticmethod
    def match_metadata(meta: dict, resort: str = '', board: str = '') -> bool:
        """与 build_where 等价的 Python 过滤，供 BM25 使用；两处条件必须同步修改"""
        if resort and meta.get('resort_id') != resort:
            return False
        if board and meta.get('board_type') not in (board, 'all'):
            return False
        return True

    # ---------------- 两路召回 ----------------
    @staticmethod
    def vector_search(query: str, resort: str = '', board: str = '',
                      k: int | None = None, embedding=None) -> list[Document]:
        """向量召回（语义相似），委托给 ResortVectorStore"""
        return ResortVectorStore.vector_search(query, resort=resort, board=board,
                                               k=k, embedding=embedding)

    @staticmethod
    def bm25_search(query: str, resort: str = '', board: str = '',
                    k: int | None = None, embedding=None) -> list[Document]:
        """关键词召回（BM25），专有名词精确匹配的主力"""
        # 懒加载 rank_bm25，避免影响无关模块导入
        from rank_bm25 import BM25Okapi

        k = k or settings.RETRIEVE_TOP_K
        corpus = [doc for doc in ResortVectorStore.all_documents(embedding)
                  if ResortRetriever.match_metadata(doc.metadata, resort, board)]
        if not corpus:
            return []

        query_tokens = set(ResortRetriever.tokenize(query))
        bm25 = BM25Okapi([ResortRetriever.tokenize(doc.page_content) for doc in corpus])
        scores = bm25.get_scores(list(query_tokens))
        ranked = sorted(zip(corpus, scores), key=lambda pair: pair[1], reverse=True)

        # 关键词覆盖闸门：至少要命中 2 个查询词（很短的查询放宽到 1 个）。
        # 否则"二世谷雪票多少钱"会靠"雪票"一个词把万龙的价格召回来——
        # 这就是把无关内容当证据的来源。
        min_overlap = 1 if len(query_tokens) <= 2 else 2
        hits: list[Document] = []
        for doc, score in ranked:
            if score <= 0:
                break
            overlap = len(query_tokens & set(ResortRetriever.tokenize(doc.page_content)))
            if overlap >= min_overlap:
                hits.append(doc)
            if len(hits) >= k:
                break
        return hits

    # ---------------- 融合与排序 ----------------
    @staticmethod
    def _doc_key(doc: Document) -> str:
        return str(doc.metadata.get('chunk_id') or doc.page_content[:120])

    @staticmethod
    def rrf_fuse(result_lists: list[list[Document]], k: int | None = None) -> list[Document]:
        """RRF 融合：score = Σ 1/(k + rank)，只关心排名，不关心两路分数尺度"""
        k = k or settings.RRF_K
        scores: dict[str, float] = {}
        registry: dict[str, Document] = {}
        for docs in result_lists:
            for rank, doc in enumerate(docs):
                key = ResortRetriever._doc_key(doc)
                scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
                registry.setdefault(key, doc)
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [registry[key] for key, _ in ordered]

    @staticmethod
    def level_boost(docs: list[Document], level: str = '') -> list[Document]:
        """水平软加权：匹配用户水平的排前，通用(level=all)居中，不匹配的靠后

        用稳定排序实现"提权"，而不是硬过滤——避免库里缺该层内容时直接空结果。
        """
        if not level:
            return docs
        rank = {level: 0, 'all': 1}
        return sorted(docs, key=lambda d: rank.get(str(d.metadata.get('level', 'all')), 2))

    @staticmethod
    def summary_boost(docs: list[Document], query: str) -> list[Document]:
        """统计意图提权：问"有几条高级道"时，把总览 chunk 排到具体雪道前面

        「有哪些雪道」是要点名，不走这里，否则总览会盖住雪道名单。
        """
        if not _COUNT_INTENT.search(query) or not re.search(r'雪道|高级道|中级道|初级道', query):
            return docs
        if re.search(r'落差|坡度|海拔|长度|多长', query):
            return docs
        return sorted(docs, key=lambda d: 0 if d.metadata.get('is_summary') else 1)

    @staticmethod
    def list_boost(docs: list[Document], query: str) -> list[Document]:
        """点名问题优先给出带名字的雪道或索道，不让总数说明占第一"""
        if not _LIST_INTENT.search(query):
            return docs
        if re.search(r'索道|缆车|魔毯', query):
            kind = 'lift'
        elif re.search(r'雪道|高级道|中级道|初级道', query):
            kind = 'run'
        else:
            return docs
        difficulty = '高级' if '高级' in query else '中级' if '中级' in query else '初级' if '初级' in query else ''

        def rank(doc: Document) -> int:
            if doc.metadata.get('doc_type') != kind:
                return 3
            text = doc.page_content
            named = '「' in text
            if difficulty and difficulty in text and named:
                return 0
            if named and not difficulty:
                return 0
            if difficulty and difficulty in text:
                return 1
            return 2

        return sorted(docs, key=rank)

    @staticmethod
    def count_caveat_boost(docs: list[Document], query: str) -> list[Document]:
        """问条数时，把「不能下断言 / 不能给分级数字」排到普通建议前面"""
        if not _COUNT_INTENT.search(query) or not re.search(r'雪道|高级道|中级道|初级道', query):
            return docs
        if re.search(r'落差|坡度|海拔|长度|多长|总长|公里', query):
            return docs

        def rank(doc: Document) -> int:
            text = doc.page_content
            asks_grade = bool(re.search(r'高级|中级|初级', query))
            if asks_grade and '不能给分级数字' in text:
                return 0
            if not asks_grade and any(phrase in text for phrase in (
                    '不对总数做断言', '不能将分级合计当作雪道总数')):
                return 0
            if doc.metadata.get('is_summary'):
                return 1
            return 2

        return sorted(docs, key=rank)

    @staticmethod
    def price_boost(docs: list[Document], query: str) -> list[Document]:
        """费用问题优先展示价目，押金或退租问题仍保留原排序。

        问某一板型的租赁时，先保留正文里点了这个板型的资料（包括“没有价格、不能用另一板型代替”），
        再展示其他价目。否则“双板租赁多少钱”会被单板价和课程价占满前几条。
        """
        if not re.search(r'多少钱|多少元|价格|价钱|费用|租金', query):
            return docs
        if re.search(r'押金|退款|退还|退租|超时', query):
            return docs
        board = '双板' if '双板' in query else '单板' if '单板' in query else ''
        other = {'双板': '单板', '单板': '双板'}.get(board, '')
        rental = bool(re.search(r'租赁|租具|租板|租金', query))

        # 同一项有「网上 / 窗口」两种价时，问题点了渠道就让同渠道的排前面（只在价目内部生效）
        channel = ('网上' if re.search(r'网上|线上|在线|online', query, re.IGNORECASE)
                   else '窗口' if re.search(r'窗口|柜台|现场', query) else '')

        def rank(doc: Document) -> tuple[int, int]:
            text = doc.page_content
            is_price = doc.metadata.get('doc_type') == 'price'
            off_channel = 1 if channel and is_price and channel not in text else 0
            # “双板……没有价格”紧挨着写，才算这条缺口；后面顺带提到的另一板型不算
            if board and rental and _missing_price_for(text, board):
                return 0, 0
            if board and rental and board in text and other not in text and is_price:
                return 1, off_channel
            if is_price:
                return 2, off_channel
            return 3, 0

        return sorted(docs, key=rank)

    @staticmethod
    def overview_boost(docs: list[Document], query: str) -> list[Document]:
        """位置和联系方式问题优先使用概览，不让泛化来源说明抢首位。"""
        if not ResortRetriever.overview_intent(query):
            return docs
        return sorted(docs, key=lambda d: 0 if d.metadata.get('doc_type') == 'overview' else 1)

    @staticmethod
    def overview_intent(query: str) -> bool:
        """口语「在哪」也查雪场概览；具体观景点、雪道位置仍按原问题检索。"""
        return bool(re.search(r'在哪|在哪里|位于哪个|哪个城市|地址|咨询电话|联系电话', query)
                    and not re.search(r'雪道|索道|缆车|怎么去|交通|道.*在哪|雾凇|打卡|观景', query))

    @staticmethod
    def interleave(batches: list[list[Document]], limit: int) -> list[Document]:
        """各子问题轮流取证据，避免一种意图占满上下文。"""
        result, seen = [], set()
        for index in range(max((len(batch) for batch in batches), default=0)):
            for batch in batches:
                if index >= len(batch):
                    continue
                doc = batch[index]
                key = doc.metadata.get('chunk_id') or (doc.metadata.get('resort_id'), doc.page_content)
                if key not in seen:
                    seen.add(key)
                    result.append(doc)
                    if len(result) >= limit:
                        return result
        return result

    # ---------------- 对外主入口 ----------------
    @staticmethod
    def retrieve(query: str, resort: str = '', level: str = '', board: str = '',
                 top_n: int | None = None, embedding=None) -> list[Document]:
        """混合检索 + 过滤，返回融合后的证据列表

        :param query: 用户问题（尽量保留专有名词）
        :param resort: 雪场 id，如 wanlong；空表示不限
        :param level: beginner / intermediate / advanced；空表示不限（软加权）
        :param board: snowboard / ski；空表示不限（硬过滤）
        :param top_n: 返回条数，默认 settings.RERANK_TOP_N
        :param embedding: 注入自定义嵌入模型（测试用）
        """
        top_n = top_n or settings.RERANK_TOP_N

        # 收录范围守卫：问的是我们没收录的雪场，直接不返回证据
        #（否则"太舞雪票多少钱"会靠"雪票"这个词召回万龙的价格）
        if ResortRegistry.detect_unknown(query):
            return []

        # 用户问题里提到雪场名就自动收敛到该雪场
        resort = resort or ResortRegistry.detect(query)

        clauses = [part.strip() for part in re.split(r'[，,；;？?]', query) if part.strip()]
        count_intent = bool(_STAT_INTENT.search(query) and re.search(r'雪道|高级道|中级道|初级道', query))
        price_intent = bool(re.search(r'多少钱|多少元|价格|价钱|费用|租金', query))
        if len(clauses) > 1 and count_intent and price_intent:
            batches = [ResortRetriever.retrieve(part, resort=resort, level=level, board=board,
                                               top_n=top_n, embedding=embedding) for part in clauses]
            return ResortRetriever.interleave(batches, top_n)

        vector_hits = ResortRetriever.vector_search(query, resort=resort, board=board,
                                                    embedding=embedding)
        keyword_hits = ResortRetriever.bm25_search(query, resort=resort, board=board,
                                                   embedding=embedding)
        # 明确雪场的概览/数量问题补召回对应语义单元，仍经过同一 metadata 隔离。
        typed_hits = []
        location_intent = ResortRetriever.overview_intent(query)
        count_intent = (_COUNT_INTENT.search(query)
                        and re.search(r'雪道|高级道|中级道|初级道', query)
                        and not re.search(r'落差|坡度|海拔|长度|多长', query))
        if resort and (location_intent or count_intent):
            typed_hits = [doc for doc in ResortVectorStore.all_documents(embedding)
                          if ResortRetriever.match_metadata(doc.metadata, resort, board)
                          and ((location_intent and doc.metadata.get('doc_type') == 'overview')
                               or (count_intent and doc.metadata.get('is_summary')))]
        fused = ResortRetriever.rrf_fuse([vector_hits, keyword_hits, typed_hits])
        # 两个都是稳定排序：先按水平分层，再让统计意图的总览 chunk 置顶
        ranked = ResortRetriever.count_caveat_boost(ResortRetriever.list_boost(
            ResortRetriever.price_boost(ResortRetriever.summary_boost(
                ResortRetriever.level_boost(fused, level), query), query), query), query)
        return ResortRetriever.overview_boost(ranked, query)[:top_n]

    @staticmethod
    def retrieve_as_text(query: str, resort: str = '', level: str = '', board: str = '',
                         top_n: int | None = None, embedding=None) -> str:
        """把检索结果拼成带编号的文本（命令行调试用；Agent 路径走 agent/evidence.py）"""
        docs = ResortRetriever.retrieve(query, resort=resort, level=level, board=board,
                                        top_n=top_n, embedding=embedding)
        if not docs:
            return '未检索到相关资料。'
        lines = []
        for index, doc in enumerate(docs, 1):
            meta = doc.metadata
            lines.append(f'[E{index}] ({meta.get("source_type")}｜{meta.get("source")}'
                         f'｜{meta.get("date")})\n{doc.page_content}')
        return '\n\n'.join(lines)


def main() -> None:
    """命令行调试入口：python -m rag.retriever "万龙有几条高级道" --debug"""
    parser = argparse.ArgumentParser(description='混合检索命令行测试')
    parser.add_argument('query', help='查询问题')
    parser.add_argument('--resort', default='', help='雪场 id，如 wanlong')
    parser.add_argument('--level', default='', help='beginner / intermediate / advanced')
    parser.add_argument('--board', default='', help='snowboard / ski')
    parser.add_argument('-k', type=int, default=None, help='返回条数')
    parser.add_argument('--debug', action='store_true', help='分别打印向量与 BM25 的召回')
    args = parser.parse_args()

    if args.debug:
        print('=== 向量召回 ===')
        for doc in ResortRetriever.vector_search(args.query, args.resort, args.board):
            print(f'  {doc.metadata.get("chunk_id")} | {doc.page_content[:70]}')
        print('=== BM25 召回 ===')
        for doc in ResortRetriever.bm25_search(args.query, args.resort, args.board):
            print(f'  {doc.metadata.get("chunk_id")} | {doc.page_content[:70]}')
        print('=== RRF 融合后 ===')

    docs = ResortRetriever.retrieve(args.query, resort=args.resort, level=args.level,
                                    board=args.board, top_n=args.k)
    if not docs:
        print('（无结果——库里没有相关内容，应如实告知用户"暂无可靠来源"）')
        return
    for index, doc in enumerate(docs, 1):
        meta = doc.metadata
        expired = ''
        expire_at = meta.get('expire_at')
        if expire_at and expire_at < dt.date.today().isoformat():
            expired = '｜⚠️可能过期'
        print(f'[E{index}] {meta.get("source_type")}｜{meta.get("source")}'
              f'｜{meta.get("date")}{expired}｜level={meta.get("level")}'
              f'｜{meta.get("doc_type")}')
        print(f'    {doc.page_content}')


# ---------------- 兼容旧调用 ----------------
retrieve = ResortRetriever.retrieve
retrieve_as_text = ResortRetriever.retrieve_as_text
vector_search = ResortRetriever.vector_search
bm25_search = ResortRetriever.bm25_search
rrf_fuse = ResortRetriever.rrf_fuse
tokenize = ResortRetriever.tokenize


if __name__ == '__main__':
    main()
