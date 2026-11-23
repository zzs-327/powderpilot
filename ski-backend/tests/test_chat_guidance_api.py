"""新增引导字段在 HTTP / SSE 出口保持一致，不依赖真实模型或数据库。"""
import asyncio
from types import SimpleNamespace

import pytest

from api import chat as chat_api
from schemas.chat import ChatRequest


GUIDANCE = {
    'suggestions': [{'id': 'prepare', 'label': '出发准备', 'question': '第一次滑雪需要准备什么？'}],
    'scenes': [{'resort_id': 'wanlong', 'name': '万龙滑雪场'}],
    'learning': {'notice': '暂无已核验视频', 'resources': []},
    'media': [{'resort_id': 'wanlong', 'title': '原页面', 'kind': 'photo',
               'url': 'https://example.org/article', 'source': '发布方',
               'checked_at': '2026-10-07', 'verification': 'page_checked',
               'rights': 'link_only', 'notice': '未观看内容，不代表实时雪况'}],
}


def test_chat_http_keeps_guidance(monkeypatch):
    async def fake_chat(*args):
        return {'reply': '回答', 'trip': {}, 'evidences': [], 'guidance': GUIDANCE}
    monkeypatch.setattr(chat_api.agent_service, 'achat', fake_chat)
    response = asyncio.run(chat_api.chat(ChatRequest(message='万龙', thread_id='test'), SimpleNamespace(id=1)))
    body = response.model_dump()
    assert body['code'] == 200 and 'msg' in body
    assert body['data']['guidance']['scenes'][0]['resort_id'] == 'wanlong'
    assert body['data']['guidance']['suggestions'][0]['question'] == GUIDANCE['suggestions'][0]['question']
    assert body['data']['guidance']['media'][0]['verification'] == 'page_checked'


def test_chat_sse_keeps_guidance_before_done(monkeypatch):
    async def fake_stream(*args):
        yield {'type': 'text', 'text': '回答'}
        yield {'type': 'guidance', 'guidance': GUIDANCE}
    monkeypatch.setattr(chat_api.agent_service, 'achat_stream', fake_stream)
    async def collect():
        response = await chat_api.chat_stream(ChatRequest(message='万龙', thread_id='test'), SimpleNamespace(id=1))
        return ''.join([chunk async for chunk in response.body_iterator])
    text = asyncio.run(collect())
    assert text.index('"type": "meta"') < text.index('"type": "guidance"') < text.index('[DONE]')
    assert '第一次滑雪需要准备什么？' in text
    assert 'page_checked' in text and '未观看内容，不代表实时雪况' in text


def test_chat_sse_cancellation_does_not_emit_done(monkeypatch):
    async def fake_stream(*args):
        yield {'type': 'text', 'text': '半截'}
        raise asyncio.CancelledError()
    monkeypatch.setattr(chat_api.agent_service, 'achat_stream', fake_stream)
    chunks = []
    async def collect():
        response = await chat_api.chat_stream(ChatRequest(message='万龙', thread_id='test'), SimpleNamespace(id=1))
        async for chunk in response.body_iterator:
            chunks.append(chunk)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect())
    text = ''.join(chunks)
    assert 'guidance' not in text and '[DONE]' not in text
