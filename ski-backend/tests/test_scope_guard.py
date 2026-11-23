"""收录范围守卫：问到没收录的雪场，直接不返回任何证据。

为什么这条最重要：拒答率最初只有 75%——"太舞雪票多少钱"会靠"雪票"这个词
召回万龙的价格。守卫的作用就是让**没有资料**和**资料相邻**两件事不再混淆。

好消息是这条守卫在检索入口的第一行就短路了（rag/retriever.py），
所以这些用例**不需要 Ollama**，换机器也能跑。
"""
from rag.parser import ResortRegistry
from rag.retriever import retrieve


def test_uncovered_resort_is_detected():
    assert ResortRegistry.detect_unknown('二世谷雪票多少钱')
    assert ResortRegistry.detect_unknown('南山滑雪场有几条雪道')


def test_uncovered_resort_returns_no_evidence():
    """未收录雪场：连向量库都不查，直接空列表（下游据此拒答）。"""
    assert retrieve('二世谷雪票多少钱') == []
    assert retrieve('南山滑雪场有几条雪道', resort='nanshan') == []


def test_covered_resort_is_not_flagged_unknown():
    """已收录雪场不能被守卫误伤——误伤比漏报更伤演示。"""
    assert not ResortRegistry.detect_unknown('万龙有几条高级道')
    assert not ResortRegistry.detect_unknown('丝绸之路初级道雪票多少钱')


def test_generic_question_is_not_flagged_unknown():
    """不含雪场名的问题（教学、装备）不该被判成"未收录"。"""
    assert not ResortRegistry.detect_unknown('换刃怎么练')
    assert not ResortRegistry.detect_unknown('新手第一次去雪场要注意什么')
