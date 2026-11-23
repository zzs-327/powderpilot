"""对话接口：普通返回 + SSE 流式两种。

为什么两种：
    uni-app 的 uni.request 收不到 SSE 的分块（小程序端尤其），
    非 H5 端走 POST /chat；H5 用 fetch 读 ReadableStream，走 POST /chat/stream。
    ⚠️ 别把 /chat 删掉：它是"流式用不了时的兜底"，也是命令行/测试的唯一入口。

为什么端点是 async：
    Agent 挂了百度地图 MCP 工具，MCP 工具只能异步调用。整条链路统一成异步后，
    这里用 await 直接消费，不要再套 asyncio.run（在事件循环里会直接报错）。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from config.dependencies import get_current_user
from models.user import User
from schemas.chat import ChatRequest, ChatResponse, EvidenceOut, TripStateOut
from schemas.result import Result, success
from service import agent_service
from utils.log import LogUtil

router = APIRouter(prefix='/chat')

# 流式对话的留痕 logger：客户端中途断开、流里抛异常都记一笔。
# 否则"用户说没答完"和"后端其实报错了"永远分不清（见 utils/log.py 的注释）。
logger = LogUtil.get_logger('chat')


def _sse(payload: dict) -> str:
    """把一个事件字典编码成一条 SSE 消息

    ⚠️ 结尾必须是**两个换行**：SSE 用空行分隔消息，少一个换行前端会一直等
    （表现为"字打了一半就停住"，而且服务端看起来一切正常）。
    ensure_ascii=False 是为了让中文按原样传输——用 \\uXXXX 传输虽然也合法，
    但抓包/日志里没法读，排查时等于又多一层解码。
    """
    return f'data: {json.dumps(payload, ensure_ascii=False)}\n\n'


def _thread_id(request: ChatRequest, user: User) -> str:
    """确定本轮对话的会话 id

    ⚠️ 这里**不能**回退成一个固定值（如 f'chat-{user.id}'），这是踩过的坑：
        原来就是这么写的，等于所有会话永远共用一个 thread。后果是模型会从
        对话记忆里背诵**几周前、数据还没更新时**的答案，还会说"这个刚才查过啦"。
        引用校验虽然拦住了内容，但用户看到的是超时或"暂无可靠来源"。
        教训：**记忆是有保质期的**——数据一改，旧记忆就从资产变成了污染源。

    没传 thread_id 就现场生成一个唯一的，并在响应里回给前端；
    前端存下来即可续聊。这样"续聊"是显式契约，而不是默认行为。
    """
    if request.thread_id:
        return request.thread_id
    return f'chat-{user.id}-{int(time.time())}-{uuid.uuid4().hex[:6]}'


@router.post('', response_model=Result[ChatResponse], summary='对话（一次性返回）')
async def chat(request: ChatRequest, user: User = Depends(get_current_user)):
    thread_id = _thread_id(request, user)
    result = await agent_service.achat(user.id, thread_id, request.message)
    card = result.get('structured')
    return success(data=ChatResponse(
        reply=result['reply'],
        trip=TripStateOut(**result.get('trip') or {}),
        structured=card.model_dump() if card is not None else None,
        thread_id=thread_id,
        # 来源溯源：前端据此把 [E201] 变成可点标签
        evidences=[EvidenceOut(**item) for item in result.get('evidences') or []],
        guidance=result.get('guidance') or {},
    ))


@router.post('/stream', summary='对话（SSE 流式，浏览器用）')
async def chat_stream(request: ChatRequest, user: User = Depends(get_current_user)):
    """流式对话：一个事件一条 SSE 消息，最后以 data: [DONE] 收尾

    事件协议（类型定义在 service/agent_service.py 的 achat_stream）：
        meta      会话 id —— **必须第一条就发**，理由见下
        status    进度（正在查价格…）
        text      正文增量
        reset     清空重来（引用校验打回时）
        trip      行程要素
        card      结构化行程卡
        evidences 本轮来源
        guidance  情境追问、雪场场景入口与已核验教学资源
        error     流内异常

    为什么 meta 要放在最前面：
        thread_id 原来是"前端传什么就用什么"。一旦前端没传（或传空），
        后端现场生成一个新的，而**流式响应里原来根本没有这个字段**——
        前端永远拿不到它，下一轮又生成一个新的 → 表面在续聊，实际每轮都失忆。
        会话 id 是续聊的前置条件，所以第一条事件就得把它交出去。
    """
    thread_id = _thread_id(request, user)

    async def event_generator():
        yield _sse({'type': 'meta', 'thread_id': thread_id})
        try:
            async for event in agent_service.achat_stream(user.id, thread_id,
                                                          request.message):
                yield _sse(event)
        except asyncio.CancelledError:
            # 用户点了"停止"或关掉了页面：这不算错误，但必须留痕。
            # 直接 raise，别在这里再往一条已经断掉的连接里写数据。
            logger.info('客户端断开，流式对话提前结束：thread_id=%s', thread_id)
            raise
        except Exception as exc:  # 流里的异常没法走全局异常处理，自己兜住
            # 超出 super-step 上限单独给一句人话：它在流式路径上的表现是"字打了一半不动了"，
            # 用户完全不知道发生了什么（而这个异常名对用户毫无意义）。
            from langgraph.errors import GraphRecursionError

            if isinstance(exc, GraphRecursionError):
                logger.warning('流式对话超出 recursion_limit：thread_id=%s', thread_id)
                yield _sse({'type': 'error',
                            'message': '这轮我查得太久了（工具调用次数超上限），'
                                       '把问题说得更具体一点（比如指明雪场或数据项）再试一次'})
            else:
                logger.exception('流式对话失败：thread_id=%s', thread_id)
                yield _sse({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})
        yield 'data: [DONE]\n\n'

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            # 反代（nginx）默认会把响应缓冲起来攒够了再发，流式就废了。
            # 本地开发用不到，但这条头是"换到服务器上还能流"的唯一保险。
            'X-Accel-Buffering': 'no',
        },
    )
