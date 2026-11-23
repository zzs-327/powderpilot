"""引用校验的"误伤率"压测：把"感觉偶尔会误杀"变成一个可以对比的数字。

为什么要单独压这个（2026-09-12 实测触发）：
    引用校验是本项目最硬的功能，也是最容易**矫枉过正**的地方。
    实测抓到一次：「先问有几条高级道 → 再追问"那价格呢？"」这种再普通不过的
    两轮对话，第二轮被打回重写、重写又不过，最后降级成"没能通过来源校验"。
    用户看到的是"它不会答"，而不是"它拦住了幻觉"——**误伤比漏报更伤演示**。

    单次现象说明不了概率。所以要有能重复跑、能出百分比、改动前后能对比的脚本。
    它同时也是回归工具：以后动 _check_facts / _derive 的规则，跑一遍就知道有没有变差。

它统计什么（每个场景跑 N 轮）：
    降级率   回答变成"没能通过来源校验"话术的比例 —— 最严重，用户拿到的是拒答
    打回次数 中间件打回重写的次数（从 logs/backend.log 里数，不重复实现判断逻辑）
    命中率   该场景必答的事实数字是否出现在回答里（防止"没被拦但也没答对"）
    平均耗时 每轮秒数 —— 顺便盯住"改完是不是变慢了"

用法（在 ski-backend 目录下）：
    python -m scripts.experiments.stress_citation                  # 全部场景各 3 轮
    python -m scripts.experiments.stress_citation --rounds 5       # 加轮次提高置信度
    python -m scripts.experiments.stress_citation --scenario price_followup
    python -m scripts.experiments.stress_citation --list           # 只看有哪些场景

⚠️ 每轮都要调真模型（1 轮 1~2 次模型调用）。默认 3 轮是"能看出趋势"的最小成本，
   全套跑一次约 10 分钟，**建议后台跑**：
       nohup $PY -u -m scripts.experiments.stress_citation > /tmp/stress.log 2>&1 &
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

from config.config import settings
from service.agent_service import achat

# 降级话术的开头（与 agent/middleware.py 的 DEGRADE_REPLY 对应）
DEGRADE_HEAD = '抱歉，这条回答没能通过来源校验'
# 中间件留痕的关键字（utils/log.py 写到 logs/backend.log）
REJECT_MARK = '引用校验未通过'

# 场景 = demo 里真正会走的那几条路。turns 是本场景的完整多轮对话，
# 因为**误伤大多发生在第二轮**（第一轮证据足、第二轮要靠记忆与追问）。
SCENARIOS: list[dict] = [
    {
        'name': 'runs_count',
        'desc': '结构化统计：有几条高级道（SQL 工具 + 证据编号 E2xx）',
        'turns': ['万龙滑雪场有几条高级道？'],
        'expect': ['19'],          # 高级道 19 条，答错/答不出都算不合格
    },
    {
        'name': 'price_followup',
        'desc': '多轮追问价格（实测出现过降级的那条路径）',
        'turns': ['万龙滑雪场有几条高级道？', '那价格呢？'],
        'expect': ['元'],          # 回答里必须真的给出价格
    },
    {
        'name': 'price_expiry',
        'desc': '单轮价格 + 保鲜期与二手转引提醒（反照骗招牌）',
        'turns': ['万龙雪票多少钱？'],
        'expect': ['560', '过期'],
    },
    {
        'name': 'kb_rag',
        'desc': 'RAG 检索：雪道体验/难度（证据编号 E1xx）',
        'turns': ['金龙道难不难？适合什么水平？'],
        'expect': [],
    },
    {
        'name': 'mcp_weather',
        'desc': '外部实时数据：百度地图 MCP 天气（证据编号 E4xx）',
        'turns': ['崇礼明天天气怎么样？会影响滑雪吗？'],
        'expect': [],
    },
    {
        'name': 'refuse_out_of_scope',
        'desc': '收录范围守卫：未收录雪场应当拒答（且**不能**变成校验降级）',
        'turns': ['二世谷雪票多少钱？'],
        'expect': [],
    },
]


class LogCounter:
    """数 logs/backend.log 里**本进程**新增了多少条"引用校验未通过"

    为什么不从中间件拿返回值：那要改 agent 代码去暴露内部状态。
    中间件已经把原因写进日志了（见 utils/log.py），**脚本只负责数数**，
    判断逻辑仍然只有一份，不会两边跑偏。

    为什么要按 pid 过滤：服务端、本脚本、离线测试都可能同时在写这个文件，
    不区分进程的话，别处产生的打回会被算到自己头上（实测就这样虚高过）。
    """

    def __init__(self) -> None:
        self.path = Path(settings.LOG_DIR) / 'backend.log'
        self.offset = self.path.stat().st_size if self.path.exists() else 0
        self.pid_tag = f':{os.getpid()}]'

    def new_rejects(self) -> list[str]:
        """返回自上次调用以来**本进程**新增的打回记录（顺便把原因带回来）"""
        if not self.path.exists():
            return []
        with self.path.open('r', encoding='utf-8', errors='replace') as handle:
            handle.seek(self.offset)
            fresh = handle.read()
            self.offset = handle.tell()
        return [line.strip() for line in fresh.splitlines()
                if REJECT_MARK in line and self.pid_tag in line]


async def run_scenario(scenario: dict, rounds: int) -> dict:
    """跑一个场景 N 轮，返回统计结果"""
    counter = LogCounter()
    degraded = 0
    missed = 0
    rejects = 0
    cost = 0.0
    notes: list[str] = []

    for index in range(1, rounds + 1):
        # 每轮独立 thread_id：复用同一个会把上一轮的失败记忆带进来（坑 #17）
        thread = f"stress-{scenario['name']}-{index}-{int(time.time())}"
        start = time.time()
        reply = ''
        for turn in scenario['turns']:
            try:
                result = await achat(1, thread, turn)
                reply = result.get('reply') or ''
            except Exception as exc:          # 单个场景失败不该打断整轮压测
                reply = f'[调用失败] {type(exc).__name__}: {exc}'
        cost += time.time() - start

        fresh = counter.new_rejects()
        rejects += len(fresh)
        if DEGRADE_HEAD in reply:
            degraded += 1
            notes.append(f'第 {index} 轮降级｜原因：{fresh[0] if fresh else "（日志无记录）"}')
        elif scenario['expect'] and not all(word in reply for word in scenario['expect']):
            missed += 1
            notes.append(f'第 {index} 轮未被拦但没答到要点（缺 '
                         f'{"、".join(w for w in scenario["expect"] if w not in reply)}）')

    return {
        'name': scenario['name'],
        'desc': scenario['desc'],
        'rounds': rounds,
        'degraded': degraded,
        'missed': missed,
        'rejects': rejects,
        'seconds': cost / rounds if rounds else 0,
        'notes': notes,
    }


def report(results: list[dict]) -> str:
    """打印 + 生成 markdown 报告（写给复盘文档用）"""
    lines = ['| 场景 | 降级率 | 打回次数 | 未答到要点 | 平均耗时 | 说明 |',
             '|---|---|---|---|---|---|']
    for item in results:
        rate = item['degraded'] / item['rounds'] if item['rounds'] else 0
        lines.append(
            f"| {item['name']} | {item['degraded']}/{item['rounds']}"
            f"（{rate:.0%}） | {item['rejects']} | {item['missed']} |"
            f" {item['seconds']:.1f}s | {item['desc']} |")
    total_rounds = sum(item['rounds'] for item in results)
    total_bad = sum(item['degraded'] for item in results)
    lines.append('')
    lines.append(f'**合计**：{total_rounds} 轮里 {total_bad} 轮被降级成拒答'
                 f'（{total_bad / total_rounds:.0%}）；'
                 f'打回重写共 {sum(item["rejects"] for item in results)} 次。')
    lines.append('')
    lines.append('> 打回原因逐条记在 `logs/backend.log`（关键字「引用校验未通过」）：'
                 '那里同时记了本轮调了哪些工具、可用编号、以及被判不合格的回答原文。')
    for item in results:
        if item['notes']:
            lines.append('')
            lines.append(f"**{item['name']} 逐轮明细**")
            lines.extend(f'- {note}' for note in item['notes'])
    return '\n'.join(lines)


async def main_async(args) -> None:
    picked = [s for s in SCENARIOS
              if not args.scenario or s['name'] in args.scenario]
    if not picked:
        print(f'没有匹配的场景：{args.scenario}。用 --list 看可选值。')
        return

    print(f"开始压测：{len(picked)} 个场景 × {args.rounds} 轮"
          f"（预计 {len(picked) * args.rounds * 1.2:.0f} 分钟）\n")
    results = []
    for scenario in picked:
        print(f"▶ {scenario['name']}：{scenario['desc']}")
        item = await run_scenario(scenario, args.rounds)
        results.append(item)
        print(f"  降级 {item['degraded']}/{item['rounds']}，"
              f"打回 {item['rejects']} 次，平均 {item['seconds']:.1f}s\n")

    text = report(results)
    print('\n' + text)
    out = Path(settings.DATA_DIR) / 'eval' / 'last_stress.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('# 引用校验压测报告\n\n' + text + '\n', encoding='utf-8')
    print(f'\n报告已写入 {out}')


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()
    parser = argparse.ArgumentParser(description='引用校验误伤率压测')
    parser.add_argument('--rounds', type=int, default=3, help='每个场景跑几轮')
    parser.add_argument('--scenario', action='append',
                        help='只跑指定场景（可重复传，名字见 --list）')
    parser.add_argument('--list', action='store_true', help='列出所有场景')
    args = parser.parse_args()

    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario['name']:22} {scenario['desc']}")
        return

    # 整个压测跑在**一个**事件循环里：aiosqlite 连接与 asyncio.Lock 不能跨循环复用
    # （见docs/TROUBLESHOOTING.md #15：同步包装 chat() 内部是 asyncio.run，第二轮就炸）
    asyncio.run(main_async(args))


if __name__ == '__main__':
    main()
