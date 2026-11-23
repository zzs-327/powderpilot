"""用量与成本测算：把"该充多少钱"变成可复现的数字。

为什么需要（而不是拍脑袋）：
    这个 Agent 一轮对话要调**好几次**模型——意图路由、工具调用、合成回答，
    引用校验不过还要打回重写再调一遍。所以"一次问答烧多少 token"很不直观：
    有人按 1 次调用估算，实际可能差 5~10 倍；充少了跑到一半没钱，充多了压资金。

做法：
    用当前 .env 配的模型真跑 N 个代表性问题，从响应里累计 input/output token，
    再按单价换算成钱，并外推"每 100 / 1000 次问答要多少钱"。

    ⚠️ 单价随供应商调整，所以写在命令行参数里（默认只报 token，不报钱）。
       免费模型（如 glm-4-flash）单价填 0，正好用来测"用量"，不用花钱。
    ⚠️ 必须**整个会话跑在一个 asyncio.run 里**（踩坑记录 #15）：异步 checkpointer
       的连接绑定在创建它的事件循环上，每题一次 asyncio.run 会在第二题直接报
       "is bound to a different event loop"。

用法（在 ski-backend 目录下，需要 MySQL 与有效 key）：
    python -m scripts.experiments.measure_cost                     # 默认 2 题，只报 token
    python -m scripts.experiments.measure_cost -n 3 --price-in 1.07 --price-out 3.55   # 元/百万 token
"""
from __future__ import annotations

import argparse
import asyncio
import time
import uuid

# 代表性问题：覆盖最贵与最便宜的两条路径
DEFAULT_QUESTIONS = [
    '万龙雪票多少钱',                                  # 单工具 + 短回答
    '帮我排个周末去万龙的行程：2 人，人均预算 2000，中级水平、单板',  # 多工具 + 状态机 + 出卡（最贵）
    '二世谷雪票多少钱',                                # 拒答路径
]


def turn_usage(messages: list) -> tuple[int, int, int]:
    """累计"本轮"（最后一条 human 之后）所有 AI 消息的 token 用量。

    为什么要按轮过滤：带 checkpointer 时 messages 是整段会话历史，
    不过滤会把前几轮的用量一起算进来（这正是踩坑记录 #17 说的那件事）。
    """
    last_human = 0
    for index, message in enumerate(messages or []):
        if getattr(message, 'type', '') == 'human':
            last_human = index
    prompt_tokens = completion_tokens = calls = 0
    for message in (messages or [])[last_human:]:
        if getattr(message, 'type', '') != 'ai':
            continue
        usage = getattr(message, 'usage_metadata', None) or {}
        if not usage:
            continue
        calls += 1
        prompt_tokens += int(usage.get('input_tokens') or 0)
        completion_tokens += int(usage.get('output_tokens') or 0)
    return prompt_tokens, completion_tokens, calls


async def run_all(questions: list[str], price_in: float, price_out: float) -> int:
    from service.agent_service import AgentService

    # thread_id 每轮唯一（踩坑记录 #17：复用会读到上一轮记忆，用量也算不准）
    thread = f'cost-{uuid.uuid4().hex[:8]}'
    total_in = total_out = total_calls = 0
    total_seconds = 0.0
    answered = 0

    for index, question in enumerate(questions, start=1):
        started = time.time()
        try:
            result = await AgentService.achat(1, thread, question)
        except Exception as exc:                                # noqa: BLE001
            print(f'[{index}] {question[:24]}… 出错：{type(exc).__name__}: {exc}')
            continue
        elapsed = time.time() - started
        prompt_tokens, completion_tokens, calls = turn_usage(result['messages'])
        total_in += prompt_tokens
        total_out += completion_tokens
        total_calls += calls
        total_seconds += elapsed
        answered += 1
        print(f'[{index}] {question[:22]:22s} 模型调用 {calls} 次｜'
              f'输入 {prompt_tokens:>6} ＋ 输出 {completion_tokens:>5} token｜{elapsed:.0f}s')

    if not answered:
        print('\n⚠️ 没拿到任何用量数据——检查 key 是否有效、模型是否返回 usage 字段')
        return 1

    average_in = total_in / answered
    average_out = total_out / answered
    cost_once = (average_in * price_in + average_out * price_out) / 1_000_000

    print('-' * 72)
    print(f'平均每次问答：输入 {average_in:.0f} ＋ 输出 {average_out:.0f} token'
          f'｜模型调用 {total_calls / answered:.1f} 次｜耗时 {total_seconds / answered:.0f}s')
    if price_in or price_out:
        print(f'平均每次问答成本：{cost_once:.4f} 元')
        print(f'外推：100 次 ≈ {cost_once * 100:.2f} 元｜1000 次 ≈ {cost_once * 1000:.2f} 元'
              f'｜10000 次 ≈ {cost_once * 10000:.2f} 元')
        print(f'（开发期做 500 次问答自测，按上面单价约需 {cost_once * 500:.2f} 元）')
    else:
        print('（没给单价，只报用量。想知道钱数就加 --price-in / --price-out）')
    print('\n提示：换模型后重跑本脚本，就能看出"换强模型贵多少倍"，再决定充多少。')
    return 0


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()

    parser = argparse.ArgumentParser(description='Agent 用量与成本测算')
    parser.add_argument('-n', '--questions', type=int, default=2, help='跑几题（默认 2）')
    parser.add_argument('--price-in', type=float, default=0.0, help='输入单价（元/百万 token）')
    parser.add_argument('--price-out', type=float, default=0.0, help='输出单价（元/百万 token）')
    args = parser.parse_args()

    from config.config import settings

    questions = (DEFAULT_QUESTIONS * 5)[:max(1, args.questions)]
    print(f'模型：{settings.LLM_PROVIDER}/{settings.LLM_MODEL}｜题数：{len(questions)}')
    print(f'单价：输入 {args.price_in} 元/百万 token，输出 {args.price_out} 元/百万 token')
    print(f'本轮上限：模型 {settings.AGENT_MAX_MODEL_CALLS} 次 / 工具 '
          f'{settings.AGENT_MAX_TOOL_CALLS} 次（超了会自动收手）')
    print('-' * 72)

    raise SystemExit(asyncio.run(run_all(questions, args.price_in, args.price_out)))


if __name__ == '__main__':
    main()
