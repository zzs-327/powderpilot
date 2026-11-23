"""命令行对话：调试主入口（不用前端就能测 Agent）。

用法（在 ski-backend 目录下）：
    python -m scripts.chat_cli                     # 默认用户 1，会话 cli-1
    python -m scripts.chat_cli --user 1 --thread cli-2 --debug

--debug 会打印工具调用与工具返回，用来确认 Agent 是否选对了工具。
输入 exit / quit 退出。

⚠️ 为什么整个会话跑在**一个** asyncio.run 里，而不是每轮调一次同步 chat()：
    异步资源（aiosqlite 连接、asyncio.Lock）都绑定在创建它们的事件循环上。
    每轮 asyncio.run 都会新建一个循环，第二轮就会拿到上一轮缓存的连接并报
        RuntimeError: ... is bound to a different event loop
    所以交互式场景必须在同一个循环里 await achat()。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from service.agent_service import achat, load_profile


def _prepare_stdin() -> None:
    """输入被重定向（管道 / 文件）时按 UTF-8 解码。

    为什么需要（2026-10-03 实测）：Windows 上管道输入用的是本地代码页(cp936)，
    而 PowerShell 写出的是 UTF-8 字节，于是中文问题被解成乱码；更糟的是解码器用了
    surrogateescape，产生 \\udcXX 代理字符，等这条消息要写进 SQLite 会话记忆时抛

        UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcaa'

    ——报错点在"存记忆"，看着像会话/数据库出问题，其实根因在输入解码。
    只在**非交互**场景改写解码方式：真实控制台由 Python 的 Windows 控制台 IO 处理，
    动它反而可能弄坏中文输入。
    """
    if sys.stdin is None or sys.stdin.isatty():
        return
    reconfigure = getattr(sys.stdin, 'reconfigure', None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding='utf-8', errors='replace')
    except (ValueError, OSError):
        pass


async def run_session(args) -> None:
    """在同一个事件循环里跑完整个交互会话"""
    profile = load_profile(args.user)
    print(f'用户 {args.user} 画像：{profile or "未采集（Agent 会渐进式询问）"}')
    print(f'会话 thread_id={args.thread}，输入 exit 退出。\n')

    while True:
        try:
            # input() 会阻塞事件循环，但命令行场景下没有并发请求，可以接受
            message = (await asyncio.to_thread(input, '你：')).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message:
            continue
        if message.lower() in {'exit', 'quit', 'q'}:
            break

        try:
            result = await achat(args.user, args.thread, message)
        except Exception as exc:
            print(f'[出错] {type(exc).__name__}: {exc}\n')
            continue

        if args.debug:
            # 只看本轮：带 checkpointer 时 messages 是整条会话历史，
            # 不过滤会把前几轮的工具调用一起打出来，看着像"这轮调了十几次工具"
            messages = result['messages']
            last_human = max((i for i, m in enumerate(messages)
                              if getattr(m, 'type', '') == 'human'), default=0)
            for msg in messages[last_human:]:
                calls = getattr(msg, 'tool_calls', None)
                if calls:
                    print(f'  [工具调用] {[c["name"] + str(c["args"]) for c in calls]}')
                if getattr(msg, 'type', '') == 'tool':
                    print(f'  [工具返回] {str(msg.content)[:160]}...')
        print(f'情报官：{result["reply"]}')
        if result.get('trip'):
            print(f'  [行程状态] {result["trip"]}')
        card = result.get('structured')
        if card is not None:
            print('  [行程卡] ' + card.model_dump_json(indent=2, exclude_none=True)[:1200])
        print()


def main() -> None:
    from utils.console import enable_utf8_console
    # 必须放在最前：--debug 会打印工具返回原文，里面常带 ⚠️（二手转引标记），
    # GBK 控制台下 print 直接 UnicodeEncodeError 把整段对话崩掉（2026-10-03 实测）
    enable_utf8_console()
    _prepare_stdin()
    parser = argparse.ArgumentParser(description='PowderPilot 命令行对话')
    parser.add_argument('--user', type=int, default=1, help='用户 id（对应 user_profile.user_id）')
    parser.add_argument('--thread', default='cli-1', help='会话 id（同 id 可续聊）')
    parser.add_argument('--debug', action='store_true', help='打印工具调用与返回')
    args = parser.parse_args()
    asyncio.run(run_session(args))


if __name__ == '__main__':
    main()
