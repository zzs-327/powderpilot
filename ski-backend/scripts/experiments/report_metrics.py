"""账本汇总：把 `logs/turns.jsonl` 读成延迟、成本、降级率等可引用数字。

为什么单独一个脚本（2026-10-05）：
    "我们很快/很便宜"这句话在面试、答辩、企业沟通里都会被追问细节。
    有了账本之后要能**一句话给出 P95 与单次成本**，而不是现场跑一遍。

用法（在 ski-backend 目录下）：
    python -m scripts.experiments.report_metrics                        # 只看用量
    python -m scripts.experiments.report_metrics --price-in 1.42 --price-out 7.81   # 元/百万 token
    python -m scripts.experiments.report_metrics --last 50              # 只看最近 50 轮

口径说明：
    - 一轮 = 一次用户提问（含该轮所有模型调用与工具调用）
    - 成本按"输入 token × 单价 + 输出 token × 单价"估算；**不含**可能存在的缓存折扣
    - 账本只记数字与工具名，不记用户原话（见 agent/metrics.py 的取舍说明）
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def percentile(values: list[float], ratio: float) -> float:
    """简单百分位（线性插值）——样本几十条，够用且不需要 numpy"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def load_rows(path: Path, last: int) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue                      # 写了一半的行（进程被杀）直接跳过
    return rows[-last:] if last > 0 else rows


def summary(rows: list[dict], price_in: float, price_out: float) -> None:
    print(f'轮数：{len(rows)}')
    if not rows:
        print('（还没有账本数据。先跑几轮对话：python -m scripts.cli 或调用 /api/v1/chat）')
        print('  账本文件：logs/turns.jsonl')
        return
    print(f'时间范围：{rows[0].get("ts", "?")}  →  {rows[-1].get("ts", "?")}')

    seconds = [float(row.get('seconds') or 0) for row in rows]
    inputs = [int(row.get('input_tokens') or 0) for row in rows]
    outputs = [int(row.get('output_tokens') or 0) for row in rows]
    calls = [int(row.get('model_calls') or 0) for row in rows]
    degraded = sum(1 for row in rows if row.get('degraded'))
    cards = sum(1 for row in rows if row.get('card'))
    cites = [int(row.get('citations') or 0) for row in rows]

    print()
    print(f'耗时：P50 {percentile(seconds, 0.5):.1f}s｜P95 {percentile(seconds, 0.95):.1f}s'
          f'｜最大 {max(seconds):.1f}s')
    print(f'模型调用：平均 {sum(calls) / len(calls):.1f} 次/轮｜最多 {max(calls)} 次')
    print(f'token：平均输入 {sum(inputs) / len(inputs):.0f}｜平均输出 {sum(outputs) / len(outputs):.0f}')
    print(f'引用编号：平均 {sum(cites) / len(cites):.1f} 个/轮｜0 个的轮次 {sum(1 for c in cites if c == 0)}')
    print(f'降级为拒答：{degraded}/{len(rows)}（{degraded / len(rows) * 100:.0f}%）'
          f'｜出卡：{cards}/{len(rows)}')

    if price_in or price_out:
        total = (sum(inputs) * price_in + sum(outputs) * price_out) / 1_000_000
        print(f'成本：合计 ≈ {total:.3f} 元｜单轮平均 ≈ {total / len(rows):.4f} 元')

    print()
    print('按模型：')
    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(str(row.get('model') or '?'), []).append(row)
    for model, group in sorted(by_model.items(), key=lambda item: -len(item[1])):
        group_seconds = [float(row.get('seconds') or 0) for row in group]
        group_calls = sum(int(row.get('model_calls') or 0) for row in group) / len(group)
        group_degraded = sum(1 for row in group if row.get('degraded'))
        line = (f'  {model:24s} {len(group):3d} 轮｜平均 {sum(group_seconds) / len(group):5.1f}s'
                f'｜{group_calls:.1f} 次模型调用｜降级 {group_degraded}')
        if price_in or price_out:
            group_cost = (sum(int(r.get('input_tokens') or 0) for r in group) * price_in
                          + sum(int(r.get('output_tokens') or 0) for r in group) * price_out) / 1_000_000
            line += f'｜≈ {group_cost / len(group):.4f} 元/轮'
        print(line)

    print()
    tool_counter = Counter(tool for row in rows for tool in (row.get('tools') or []))
    if tool_counter:
        print('工具调用频次：' + '、'.join(f'{name}×{count}' for name, count in tool_counter.most_common()))


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()

    from config.config import settings

    parser = argparse.ArgumentParser(description='账本汇总')
    parser.add_argument('--last', type=int, default=0, help='只看最近 N 轮（0 = 全部）')
    parser.add_argument('--price-in', type=float, default=0.0, help='输入单价（元/百万 token）')
    parser.add_argument('--price-out', type=float, default=0.0, help='输出单价（元/百万 token）')
    args = parser.parse_args()

    path = Path(settings.LOG_DIR) / 'turns.jsonl'
    print(f'账本：{path}')
    print(f'当前模型：{settings.LLM_PROVIDER}/{settings.LLM_MODEL}')
    summary(load_rows(path, args.last), args.price_in, args.price_out)


if __name__ == '__main__':
    main()
