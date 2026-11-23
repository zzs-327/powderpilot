# -*- coding: utf-8 -*-
"""流式事件协议离线验证（不调模型、不需要 MySQL / Ollama / API key）

为什么要有它：
    `achat_stream` 是前端打字机效果的**唯一数据源**，而它的行为很难在浏览器里穷举——
    "引用校验打回时前端要清空气泡"这条路径要真触发一次幻觉才会出现，
    等演示当天才发现没生效就晚了。这里用一个假 agent 把各种流式分块喂进去，
    把每条分支都压一遍。

    ⚠️ 假 agent 只替换 `AgentService.aget_agent`，被测的是**真实的 achat_stream**。

跑法（在 ski-backend 目录下）：
    python -m scripts.test_chat_stream
"""
import asyncio
import sys

from langchain_core.messages import (AIMessage, HumanMessage, SystemMessage,
                                     ToolMessage)

from agent.middleware import RETRY_MARK
from service.agent_service import AgentService

PASSED = 0
FAILED = 0


def ok(condition: bool, name: str) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f'  [OK] {name}')
    else:
        FAILED += 1
        print(f'  [FAIL] {name}')


class FakeAgent:
    """按脚本吐 (mode, payload) 的假 agent：形状与 langgraph 的 astream 一致"""

    def __init__(self, script):
        self.script = script

    async def astream(self, *args, **kwargs):
        for mode, payload in self.script:
            yield mode, payload


def install(script) -> None:
    """把 AgentService 换成"假 agent + 不出网的兜底出卡" """

    async def fake_get_agent(user_id=None):
        return FakeAgent(script)

    async def fake_fallback_card(result, trip):
        return None

    AgentService.aget_agent = staticmethod(fake_get_agent)
    AgentService._fallback_card = staticmethod(fake_fallback_card)


async def run(script) -> list[dict]:
    install(script)
    events = []
    async for event in AgentService.achat_stream(1, 'thread-test', '万龙雪票多少钱'):
        events.append(event)
    return events


def types_of(events) -> list[str]:
    return [e['type'] for e in events]


def text_of(events) -> str:
    return ''.join(e.get('text', '') for e in events if e['type'] == 'text')


# ---------------- 用例 1：正常一轮（工具进度 + 正文 + 行程 + 证据） ----------------

def case_normal() -> None:
    print('\n[1] 正常一轮：进度事件 + 正文增量 + 收尾事件')
    tool_msg = ToolMessage(
        content='[E1] (A 官方｜万龙官网｜2026-04-06｜⚠️ 二手转引（非官网直采）)\n'
                '平日全天雪票 560 元/人',
        name='get_resort_price', tool_call_id='c1')
    script = [
        ('messages', (SystemMessage(content='ignored'), {})),
        ('messages', (tool_msg, {})),
        ('messages', (AIMessage(content='万龙平日全天雪票'), {})),
        ('messages', (AIMessage(content=' 560 元/人 [E301]。'), {})),
        ('values', {'messages': [HumanMessage(content='万龙雪票多少钱'), tool_msg,
                                 AIMessage(content='万龙平日全天雪票 560 元/人 [E301]。')],
                    'structured_response': None}),
    ]
    events = asyncio.run(run(script))
    types = types_of(events)

    ok(types[0] == 'status', '第一条事件是进度（首字之前不能白屏）')
    ok(events[0].get('text', '').startswith('正在理解'), '进度文案 = 正在理解你的问题…')
    status = [e for e in events if e['type'] == 'status']
    ok(any('正在查价格' in e.get('text', '') for e in status),
       '工具消息 -> 进度事件（正在查价格…）')
    ok(text_of(events) == '万龙平日全天雪票 560 元/人 [E301]。', '正文增量按顺序拼接，无丢字无重复')
    ok('trip' in types, '收尾有 trip 事件')
    ok('evidences' in types, '收尾有 evidences 事件')
    ev = [e for e in events if e['type'] == 'evidences'][0]['evidences']
    ok(ev and ev[0]['id'] == 1, '证据编号被解析出来（E1）')
    ok(ev and ev[0]['status_key'] == 'transcribed', '证据档位解析正确（二手转引）')
    ok('reset' not in types, '正常一轮不该出现 reset')


# ---------------- 用例 2：引用校验打回（chunk 里能看到 SystemMessage） ----------------

def case_reset_by_chunk() -> None:
    print('\n[2] 引用校验打回：必须发 reset（否则两版答案叠在一起）')
    script = [
        ('messages', (AIMessage(content='万龙平日 999 元。'), {})),
        ('messages', (SystemMessage(content=f'{RETRY_MARK}\n你上一条回答存在以下问题：…'), {})),
        ('messages', (AIMessage(content='万龙平日 560 元 [E301]。'), {})),
        ('values', {'messages': [HumanMessage(content='万龙雪票多少钱'),
                                 SystemMessage(content=RETRY_MARK),
                                 AIMessage(content='万龙平日 560 元 [E301]。')]}),
    ]
    events = asyncio.run(run(script))
    types = types_of(events)
    ok(types.count('reset') == 1, 'reset 恰好发一次（chunk 与 values 两条路不重复计）')

    # ⚠️ 契约要说清楚：被打回的那一版**已经流出去了，收不回来**——
    #    所以事件流里必然同时存在两版正文，这不是 bug，而是流式 + 后置校验的固有代价。
    #    真正要保证的是：reset 之后推的必须是重写版，前端据此清空气泡即可。
    reset_at = types.index('reset')
    text_events = [e for e in events if e['type'] == 'text']
    bad_before = ''.join(e['text'] for e in events[:reset_at] if e['type'] == 'text')
    after_reset = ''.join(e['text'] for e in events[reset_at:] if e['type'] == 'text')

    ok('999' in bad_before, '被打回的那一版确实先流出去了（所以前端必须清空）')
    ok(after_reset == '万龙平日 560 元 [E301]。',
       'reset 之后推的全是重写版（前端清空后剩下的就是它）')
    ok('999' not in after_reset, '清空后不会残留旧版本的数字')
    ok(text_events[-1]['text'] in after_reset, '最后一条正文属于重写版')


# ---------------- 用例 3：只有 values 里能看到打回标记（兜底那条路） ----------------

def case_reset_by_values() -> None:
    print('\n[3] 打回标记只在 values 里出现时，兜底路径也要发 reset')
    script = [
        ('messages', (AIMessage(content='编的答案。'), {})),
        ('values', {'messages': [HumanMessage(content='万龙雪票多少钱'),
                                 SystemMessage(content=RETRY_MARK)]}),
        ('messages', (AIMessage(content='真实答案 [E301]。'), {})),
    ]
    events = asyncio.run(run(script))
    ok(types_of(events).count('reset') == 1, 'values 兜底路径发出 reset')


# ---------------- 用例 4：上一轮的打回标记不该误伤本轮 ----------------

def case_stale_retry_mark() -> None:
    print('\n[4] 上一轮留下的打回标记，不能把本轮判成"正在重写"')
    old_turn = [HumanMessage(content='上一轮的问题'),
                SystemMessage(content=RETRY_MARK),
                AIMessage(content='上一次降级了')]
    script = [
        ('messages', (AIMessage(content='正常回答 [E301]。'), {})),
        ('values', {'messages': old_turn + [HumanMessage(content='新问题'),
                                            AIMessage(content='正常回答 [E301]。')]}),
    ]
    events = asyncio.run(run(script))
    ok('reset' not in types_of(events), '历史里的 RETRY_MARK 不触发 reset')


# ---------------- 用例 5：内部消息前缀必须被吞掉 ----------------

def case_internal_prefix() -> None:
    print('\n[5] 结构化输出的内部消息（Returning structured response）不能给用户看')
    script = [
        ('messages', (AIMessage(content='Returning structured'), {})),
        ('messages', (AIMessage(content=' response with tool calls'), {})),
        ('values', {'messages': [HumanMessage(content='排个行程'),
                                 AIMessage(content='Returning structured response')],
                    'structured_response': None}),
    ]
    events = asyncio.run(run(script))
    ok('Returning' not in text_of(events), '内部前缀整段被吞掉')
    ok(text_of(events) == '', '这一轮没有正文（前端会显示"已生成行程卡"）')


# ---------------- 用例 6：未知工具名也要有兜底进度 ----------------

def case_unknown_tool() -> None:
    print('\n[6] 没登记过的工具名也要给一句人话进度')
    tool_msg = ToolMessage(content='（无证据）', name='some_new_tool', tool_call_id='c9')
    script = [
        ('messages', (tool_msg, {})),
        ('values', {'messages': [HumanMessage(content='x'), tool_msg]}),
    ]
    events = asyncio.run(run(script))
    status = [e for e in events if e['type'] == 'status']
    ok(any(e.get('text') == '正在查资料…' for e in status), '未知工具 -> "正在查资料…"')
    ok(all(e.get('name', '') != '' for e in status if 'name' in e), '进度事件带工具名，便于前端排查')


def case_final_degradation_replaces_draft() -> None:
    print('\n[7] 最终引用校验拒答：覆盖已流出的草稿和重写残片')
    from agent.middleware import DEGRADE_REPLY

    for retry in (False, True):
        human = HumanMessage(content='北大湖在哪？')
        draft = AIMessage(content='北大湖位于长春市。')
        messages = [human, draft]
        script = [('messages', (draft, {}))]
        if retry:
            mark = SystemMessage(content=RETRY_MARK)
            rewrite = AIMessage(content='抱歉，由于工具调用失败，无法提供雾凇信息。')
            messages += [mark, rewrite]
            script += [('messages', (mark, {})), ('messages', (rewrite, {}))]
        messages.append(AIMessage(content=DEGRADE_REPLY))
        script.append(('values', {'messages': messages}))
        events = asyncio.run(run(script))

        visible = ''
        for event in events:
            if event['type'] in ('reset', 'replace'):
                visible = event.get('text', '')
            elif event['type'] == 'text':
                visible += event['text']
        ok(visible == DEGRADE_REPLY, f'重写={retry}：气泡只保留最终拒答，不拼接错误草稿')
        ok(not any(e['type'] == 'guidance' for e in events), '引用校验拒答不附资源卡')


def case_markdown_citation_is_replaced() -> None:
    print('\n[8] Markdown 来源编号在流式收尾时与非流式一致')
    raw = '只核对页面，未观看视频。[来源](E101)'
    script = [
        ('messages', (AIMessage(content=raw), {})),
        ('values', {'messages': [HumanMessage(content='视频链接'), AIMessage(content=raw)]}),
    ]
    events = asyncio.run(run(script))
    replacements = [e['text'] for e in events if e['type'] == 'replace']
    ok(replacements == [AgentService._clean_reply(raw, None, [])], '流式最终正文使用与非流式相同的编号格式')
    ok(bool(replacements) and '[E101]' in replacements[0], '前端收到可点击的来源编号')


def main() -> int:
    from utils.console import enable_utf8_console
    enable_utf8_console()      # pytest 也会调这里（tests/test_offline_suites.py）
    print('=' * 64)
    print('流式事件协议离线验证（假 agent，不调模型）')
    print('=' * 64)
    case_normal()
    case_reset_by_chunk()
    case_reset_by_values()
    case_stale_retry_mark()
    case_internal_prefix()
    case_unknown_tool()
    case_final_degradation_replaces_draft()
    case_markdown_citation_is_replaced()
    print('\n' + '=' * 64)
    print(f'结果：{PASSED} 项通过 / {FAILED} 项失败')
    print('=' * 64)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
