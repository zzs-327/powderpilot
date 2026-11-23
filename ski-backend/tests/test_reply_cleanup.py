"""回复兜底文案：内部消息永远不能漏给用户。

背景（2026-10-03 端到端实测）：结构化输出的副作用消息以
"Returning structured response: …" 开头，里面就是整张卡的原文。
原来这条分支在 card 为空时把它原样返回——而"卡被字段校验打回"之后 card 恰好为 None，
于是用户看到整段内部文本。这类漏洞离线用例抓不到，因为它只在**校验打回**这条新路径上出现。
"""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from service.agent_service import EMPTY_REPLY_FALLBACK, _INTERNAL_PREFIX, AgentService


def _messages(*items):
    return list(items)


def test_internal_prefix_never_leaks_without_current_turn_text():
    messages = _messages(
        HumanMessage(content='先前的闲聊'),
        AIMessage(content='上一轮的回答'),
        HumanMessage(content='帮我排个行程'),
        ToolMessage(content=f'{_INTERNAL_PREFIX}: resort=万龙', name='tool', tool_call_id='c1'),
    )
    reply = AgentService._clean_reply(messages[-1].content, None, messages)
    assert _INTERNAL_PREFIX not in reply
    assert '上一轮的回答' not in reply      # 也不能拿上一轮的回答顶包
    assert reply == EMPTY_REPLY_FALLBACK


def test_current_turn_text_is_used_when_card_is_dropped():
    messages = _messages(
        HumanMessage(content='帮我排个行程'),
        AIMessage(content='我先按你的预算核一遍价格。'),
        ToolMessage(content=f'{_INTERNAL_PREFIX}: resort=万龙', name='tool', tool_call_id='c1'),
    )
    reply = AgentService._clean_reply(messages[-1].content, None, messages)
    assert reply == '我先按你的预算核一遍价格。'


def test_card_summary_is_used_when_card_survives():
    class Card:
        summary = '周末万龙两日行程'

    reply = AgentService._clean_reply(f'{_INTERNAL_PREFIX}: resort=万龙', Card(), [])
    assert reply == '已生成行程卡：周末万龙两日行程'


def test_normal_reply_passes_through():
    assert AgentService._clean_reply('万龙平日雪票 560 元 [E301]', None, []) \
        == '万龙平日雪票 560 元 [E301]'


def test_markdown_source_link_normalizes_without_changing_external_links():
    raw = '原帖：[观看](https://example.com/video/1) [官方账号](E101)'
    assert AgentService._clean_reply(raw, None, []) == '原帖：[观看](https://example.com/video/1) 官方账号 [E101]'
