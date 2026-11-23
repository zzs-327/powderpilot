"""意图提权不能让无关总览盖住价格、落差等实际证据。"""
from langchain_core.documents import Document
from rag.retriever import ResortRetriever


def test_summary_boost_only_applies_to_run_count():
    evidence = Document(page_content='最大落差有两个口径', metadata={'doc_type': 'provenance'})
    summary = Document(page_content='雪道共48条', metadata={'doc_type': 'run', 'is_summary': True})
    docs = [evidence, summary]
    assert ResortRetriever.summary_boost(docs, '雪场最大落差是多少') == docs
    assert ResortRetriever.summary_boost(docs, '雪道最大落差是多少') == docs
    assert ResortRetriever.summary_boost(docs, '有几条雪道') == [summary, evidence]
    assert ResortRetriever.summary_boost(docs, '有几个缆车') == docs
    assert ResortRetriever.summary_boost(docs, '有哪些雪道') == docs


def test_list_questions_name_runs_and_lifts_before_totals():
    summary = Document(page_content='雪道总览共 9 条', metadata={'doc_type': 'run', 'is_summary': True})
    named = Document(page_content='多乐美地的雪道「罗马道」：难度初级', metadata={'doc_type': 'run'})
    beginner = Document(page_content='雪道「练习道」：难度初级道', metadata={'doc_type': 'run'})
    advanced = Document(page_content='雪道「挑战道」：难度高级道', metadata={'doc_type': 'run'})
    assert ResortRetriever.list_boost([summary, named], '多乐美地滑雪场有哪些雪道')[0] is named
    ranked = ResortRetriever.list_boost([beginner, summary, advanced], '北大湖的高级道有哪些')
    assert ranked[0] is advanced
    provenance = Document(page_content='索道数量：13 条缆车', metadata={'doc_type': 'provenance'})
    lift = Document(page_content='北大湖本条为索道「A索道」', metadata={'doc_type': 'lift'})
    assert ResortRetriever.list_boost([provenance, lift], '北大湖坐什么索道')[0] is lift
    assert ResortRetriever.list_boost([summary, named], '北大湖有什么雪道')[0] is named
    tip = Document(page_content='长龙道新手建议', metadata={'doc_type': 'tip'})
    assert ResortRetriever.list_boost([tip, named], '长龙道有什么要注意的') == [tip, named]


def test_count_caveat_outranks_ordinary_advice():
    advice = Document(page_content='对老手的建议：玄奘大道最大坡度 37 度', metadata={'doc_type': 'tip'})
    caveat = Document(page_content='来源冲突，本库不对总数做断言', metadata={'doc_type': 'tip'})
    assert ResortRetriever.count_caveat_boost([advice, caveat], '丝绸之路有几条雪道')[0] is caveat
    total = Document(page_content='松花湖雪道达到 50 条', metadata={'doc_type': 'provenance'})
    grade_gap = Document(page_content='只能登记总数，不能给分级数字', metadata={'doc_type': 'provenance'})
    assert ResortRetriever.count_caveat_boost([total, grade_gap], '松花湖有多少条雪道')[0] is total
    assert ResortRetriever.count_caveat_boost([total, grade_gap], '松花湖有几条高级道')[0] is grade_gap


def test_price_boost_preserves_deposit_and_refund_intent():
    tip = Document(page_content='雪具押金2000元', metadata={'doc_type': 'tip'})
    price = Document(page_content='租赁200元', metadata={'doc_type': 'price'})
    docs = [tip, price]
    assert ResortRetriever.price_boost(docs, '租一套雪具多少钱') == [price, tip]
    assert ResortRetriever.price_boost(docs, '租雪具押金多少钱') == docs
    assert ResortRetriever.price_boost(docs, '退款费用是多少') == docs
    assert ResortRetriever.price_boost(docs, '高级道有几条') == docs


def test_rental_question_keeps_the_named_board_ahead_of_other_prices():
    """问双板租赁时，说明“没有双板价”的条目要排在单板价和课程价前面。"""
    lesson = Document(page_content='英语私教51000日元', metadata={'doc_type': 'price'})
    snowboard = Document(page_content='标准单板套装10800日元', metadata={'doc_type': 'price'})
    gap = Document(page_content='双板租赁成人一日价格没有可审阅的价格行，不能用单板10800日元代替',
                   metadata={'doc_type': 'tip'})
    docs = [lesson, snowboard, gap]
    ranked = ResortRetriever.price_boost(docs, '留寿都双板租赁一天多少钱')
    assert ranked[0] is gap
    ranked_snow = ResortRetriever.price_boost([gap, snowboard], '单板租赁多少钱')
    assert ranked_snow[0] is snowboard


def test_price_channel_in_question_picks_matching_price_first():
    """问题点了「网上 / 窗口」渠道时，同渠道的价目排前；没点渠道则保持原序。"""
    window = Document(page_content='成人1日雪票·常规季·窗口：16700 JPY', metadata={'doc_type': 'price'})
    online = Document(page_content='成人1日雪票·常规季·网上：13200 JPY', metadata={'doc_type': 'price'})
    tip = Document(page_content='雪票退款说明', metadata={'doc_type': 'tip'})
    docs = [window, online, tip]
    assert ResortRetriever.price_boost(docs, '成人一日雪票网上多少钱')[0] is online
    assert ResortRetriever.price_boost(docs, '成人一日雪票线上价格')[0] is online
    assert ResortRetriever.price_boost([online, window], '成人一日雪票窗口多少钱')[0] is window
    assert ResortRetriever.price_boost(docs, '成人一日雪票多少钱') == [window, online, tip]
    # 渠道只在价目内部排序，不会把非价目顶到价目前面
    assert ResortRetriever.price_boost(docs, '网上价格')[-1] is tip


def test_overview_boost_preserves_specific_route_questions():
    source = Document(page_content='来源说明', metadata={'doc_type': 'provenance'})
    overview = Document(page_content='位于崇礼', metadata={'doc_type': 'overview'})
    docs = [source, overview]
    assert ResortRetriever.overview_boost(docs, '万龙在哪个城市') == [overview, source]
    assert ResortRetriever.overview_boost(docs, '北大湖在哪？') == [overview, source]
    assert ResortRetriever.overview_boost(docs, '金龙道在哪里') == docs
    assert ResortRetriever.overview_boost(docs, '万龙怎么去') == docs
    assert ResortRetriever.overview_boost(docs, '北大湖雾凇在哪看') == docs
