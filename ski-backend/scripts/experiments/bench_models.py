# -*- coding: utf-8 -*-
"""跨模型对比：同一套题、同一套 Agent，只换模型，量出"能不能干活"

为什么需要它（而不是看模型排行榜）：
    通用榜单测的是"会不会写代码/答知识题"，跟本项目要的东西**不是一回事**。
    本项目的及格线是三条工程约束，缺一条就跑不起来：
        1. 会不会**调工具**（9 个工具 + 结构化输出全靠 function calling）
        2. 会不会**标来源编号**（不标就被引用校验打回，两次不过直接降级成拒答）
        3. 答得**快不快 / 收不收得住**（实测 glm-4.5-flash 六分钟还在反复调同一个工具）
    这三条只有真跑一遍才知道，所以这里直接跑真 Agent、量真指标。

指标（每题一行，最后按模型汇总）：
    耗时   一轮对话的墙钟时间
    工具   本轮调用了哪些工具（空 = 一次工具都没调，基本等于答非所问）
    编号   回答里出现了几个 [Exxx]
    打回   引用校验打回次数（从 citation logger 实时计数，不解析日志文件）
    降级   是否最终降级成"暂无可靠来源"（用户看到的就是拒答）

用法（在 ski-backend 目录下）：
    # 先设好手里有的 key，没设的候选会自动跳过
    #   Windows:  set SILICONFLOW_API_KEY=sk-xxx
    #   Git Bash: export SILICONFLOW_API_KEY=sk-xxx
    python -m scripts.experiments.bench_models                     # 跑全部可用候选 × 默认 4 题
    python -m scripts.experiments.bench_models --list              # 只看候选清单与 key 状态
    python -m scripts.experiments.bench_models --models qwen3.8-flash
    python -m scripts.experiments.bench_models --questions 万龙雪票多少钱 --rounds 2

报告写到 data/eval/last_model_bench.md。
⚠️ 真调模型、真花钱（免费额度也算额度），默认 4 题 × N 个模型，先看 --list 再决定跑哪些。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
import uuid

# 引用编号：回答里出现的 [E101] 这类
_CITE = re.compile(r'\[E(\d+)\]')
# 降级话术（agent/middleware.py 的 DEGRADE_REPLY 开头）
_DEGRADE_MARK = '没能通过来源校验'

# 候选清单：(展示名, base_url, 读哪个环境变量取 key, 模型名，备注)
#
# 说明：这些都是 OpenAI 兼容端点，换模型**只需要改这三样**，代码一行不用动
#      （agent/agent.py 的 AgentFactory.resolve_model 就是读这几个值）。
# ⚠️ 免费额度是会变的，务必以各家控制台为准；表里的备注只是 2026-09-13 的了解。
CANDIDATES: list[tuple[str, str, str, str, str]] = [
    ('zhipu/glm-4.5-air', 'https://open.bigmodel.cn/api/paas/v4',
     'ZHIPU_API_KEY', 'glm-4.5-air', '智谱 4.5 系列中杯；赠送包常给 1200 万 token；⚠️ 必须关思考'),
    ('zhipu/glm-4.6v', 'https://open.bigmodel.cn/api/paas/v4',
     'ZHIPU_API_KEY', 'glm-4.6v', '智谱多模态（赠送包常给 600 万 token）；文本任务也能用'),
    ('zhipu/glm-4-flash', 'https://open.bigmodel.cn/api/paas/v4',
     'ZHIPU_API_KEY', 'glm-4-flash', '智谱，完全免费；实测能过校验但回答偏短'),
    ('zhipu/glm-4.5-flash', 'https://open.bigmodel.cn/api/paas/v4',
     'ZHIPU_API_KEY', 'glm-4.5-flash', '智谱，免费；⚠️ 实测 6 分钟不收敛（多半是思考模式没关）'),
    ('zhipu/glm-4.6', 'https://open.bigmodel.cn/api/paas/v4',
     'ZHIPU_API_KEY', 'glm-4.6', '智谱 4.6 文本主力；⚠️ 必须关思考（disabled）'),
    ('zhipu/glm-5.3-flash', 'https://open.bigmodel.cn/api/paas/v4',
     'ZHIPU_API_KEY', 'glm-5.3-flash', '智谱 5.3 flash；⚠️ 思考只认 low/high/max（**不能关**），'
                                       '而 agent.py 目前只给 glm-4.5/4.6 转发 thinking，'
                                       '所以它现在跑在"默认思考"上——要用 low 得先改那个正则'),
    ('百炼/qwen3.8-flash', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
     'DASHSCOPE_API_KEY', 'qwen3.8-flash', '阿里百炼，新用户 100 万 token（华北2，90 天）'),
    ('百炼/qwen3.6-flash', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
     'DASHSCOPE_API_KEY', 'qwen3.6-flash', '阿里百炼，同上额度池'),
    ('硅基/Nex-N2-Pro', 'https://api.siliconflow.cn/v1',
     'SILICONFLOW_API_KEY', 'nex-agi/Nex-N2-Pro', '硅基流动，标价 $0；397B MoE，注册送额度'),
    ('硅基/Qwen3-8B', 'https://api.siliconflow.cn/v1',
     'SILICONFLOW_API_KEY', 'Qwen/Qwen3-8B', '硅基流动，免费；8B 小模型，够不够用要实测'),
    ('火山/doubao', 'https://ark.cn-beijing.volces.com/api/v3',
     'ARK_API_KEY', 'doubao-seed-1.6-flash', '火山方舟；⚠️ model 要填控制台的接入点 id（ep-xxx）'),
    ('deepseek/deepseek-chat', 'https://api.deepseek.com/v1',
     'DEEPSEEK_API_KEY', 'deepseek-chat', 'DeepSeek，**不免费**但很便宜；本机那把 key 已失效'),
]

# 默认题目集：四条覆盖四类最容易翻车的路径
DEFAULT_QUESTIONS = [
    '万龙有几条高级道',          # 结构化统计 + 工具路由
    '万龙雪票多少钱',            # 价格 + 时效提醒
    '万龙的数据分别是几手的？',   # 元信息 / 可信度分档（小模型最容易直接答"我不知道"）
    '二世谷雪票多少钱',          # 拒答：本库没收录，**不许编**
]


class _RetryCounter:
    """给 citation logger 挂一个计数器，实时数"打回"次数

    为什么不解析 logs/backend.log：
        那个文件是多个进程共用的，我这一轮的打回记录和别人的混在一起，
        按行号或时间戳去切很容易切错。挂 handler 是精确的。
    """

    def __init__(self) -> None:
        counter = self
        self.count = 0

        class _Handler(logging.Handler):
            """只数"引用校验未通过"这一种记录，别的原样放过"""

            def emit(self, record: logging.LogRecord) -> None:
                try:
                    if '引用校验未通过' in record.getMessage():
                        counter.count += 1
                except Exception:
                    pass

        self._handler = _Handler()
        self._logger = logging.getLogger('citation')
        # 顺手让它别把日志再往下传一层（项目自己的文件 handler 照常工作）
        self._logger.addHandler(self._handler)

    def reset(self) -> None:
        self.count = 0

    def close(self) -> None:
        self._logger.removeHandler(self._handler)


class ModelBench:
    """跨模型对比（静态方法）"""

    @staticmethod
    def load_keys() -> dict[str, str]:
        """取 key：先环境变量，再读项目 .env

        为什么两个来源都认：
            本项目的规矩是"**密钥只放 .env**"（README 安全提醒那条），
            所以试新平台时最自然的动作是往 .env 里加一行 key；
            但临时换 key 用环境变量更顺手（不污染文件、不担心手滑提交）。
            两边都支持，环境变量优先。
        """
        keys = dict(os.environ)
        try:
            from config.config import BASE_DIR

            env_file = BASE_DIR / '.env'
            if env_file.exists():
                for line in env_file.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    name, value = line.split('=', 1)
                    name, value = name.strip(), value.strip()
                    # .env 里的值不覆盖已存在的环境变量（环境变量优先）
                    if name and value and name not in os.environ:
                        keys.setdefault(name, value)
        except Exception:
            pass
        return keys

    @staticmethod
    def available(models: list[str] | None = None) -> list[tuple]:
        """筛出"配了 key 且没被 --models 排除"的候选"""
        keys = ModelBench.load_keys()
        out = []
        for name, base_url, env_key, model, note in CANDIDATES:
            if not keys.get(env_key):
                continue
            if models and not any(m in name or m == model for m in models):
                continue
            out.append((name, base_url, env_key, model, note))
        return out

    @staticmethod
    def print_list() -> None:
        """打印候选清单与 key 状态（不花任何额度）"""
        keys = ModelBench.load_keys()
        print(f'{"候选":22} {"模型名":26} {"key 从哪来":22} 状态  备注')
        print('-' * 120)
        for name, _base, env_key, model, note in CANDIDATES:
            has = ' 有' if keys.get(env_key) else ' 无'
            print(f'{name:22} {model:26} {env_key:22}{has}   {note}')
        print('\n把 key 加到 ski-backend/.env（或设为环境变量）后再跑：'
              'python -m scripts.experiments.bench_models')

    @staticmethod
    async def run_one(base_url: str, api_key: str, model: str,
                      question: str, counter: _RetryCounter) -> dict:
        """跑一道题，返回这一题的指标"""
        # 懒加载：换模型只需要把工厂的 resolve_model 换掉，
        # Agent / 中间件 / 工具全都不用动（这正是当初把配置收在一处的收益）
        from agent.agent import AgentFactory
        from config.config import settings
        from service.agent_service import AgentService

        AgentFactory.resolve_model = staticmethod(lambda: (api_key, base_url, model))
        # 思考模式的取值要按模型家族给（规则与 agent.py 保持一致）：
        #   glm-4.5 / 4.6 → disabled（开着思考会慢到不可用）
        #   glm-5.x       → low（**不能给 disabled**，5.x 只认 low/high/max）
        #   其它           → 空（不指定这个参数）
        if re.match(r'glm-4\.[56]', model):
            settings.LLM_THINKING = 'disabled'
        elif re.match(r'glm-5', model):
            settings.LLM_THINKING = 'low'
        else:
            settings.LLM_THINKING = ''

        # ⚠️ thread_id 每轮唯一（docs/TROUBLESHOOTING.md #17）：复用同一个 id 会读到上一轮的
        #    对话历史，模型会说"这个刚答过"，看着像模型坏了，其实是自己的脚本串了会话
        thread_id = f'bench-{uuid.uuid4().hex[:10]}'
        counter.reset()
        started = time.time()
        record = {'question': question, 'elapsed': 0.0, 'tools': [],
                  'cites': 0, 'retries': 0, 'degraded': False,
                  'length': 0, 'error': '', 'reply': ''}
        try:
            result = await AgentService.achat(1, thread_id, question)
            record['elapsed'] = time.time() - started
            reply = result.get('reply') or ''
            record['reply'] = reply
            record['length'] = len(reply)
            record['cites'] = len(set(_CITE.findall(reply)))
            record['degraded'] = _DEGRADE_MARK in reply
            tools = []
            for message in result['messages']:
                for call in (getattr(message, 'tool_calls', None) or []):
                    tools.append(call.get('name'))
            record['tools'] = tools
        except Exception as exc:
            record['elapsed'] = time.time() - started
            record['error'] = f'{type(exc).__name__}: {exc}'
        record['retries'] = counter.count
        return record

    @staticmethod
    async def run(model_filter: list[str] | None, questions: list[str],
                  rounds: int) -> list[dict]:
        """逐个候选跑题，返回全部记录"""
        targets = ModelBench.available(model_filter)
        if not targets:
            print('没有可跑的候选：先把 key 加到 .env（--list 看清单与变量名）')
            return []

        keys = ModelBench.load_keys()
        counter = _RetryCounter()
        all_records: list[dict] = []
        try:
            for name, base_url, env_key, model, _note in targets:
                print(f'\n{"=" * 70}\n▶ {name}（model={model}）\n{"=" * 70}')
                for question in questions:
                    for round_no in range(1, rounds + 1):
                        record = await ModelBench.run_one(
                            base_url, keys.get(env_key, ''), model, question, counter)
                        record['model'] = name
                        all_records.append(record)
                        if record['error']:
                            print(f'  ✗ {question}  [{record["elapsed"]:.0f}s] {record["error"][:70]}')
                            continue
                        flag = '⚠️降级' if record['degraded'] else '✅'
                        round_tag = f' (第{round_no}轮)' if rounds > 1 else ''
                        print(f'  {flag} {question}{round_tag}  '
                              f'[{record["elapsed"]:.0f}s] 工具={record["tools"] or "无"} '
                              f'编号={record["cites"]} 打回={record["retries"]} '
                              f'字数={record["length"]}')
        finally:
            counter.close()
        return all_records

    @staticmethod
    def summarize(records: list[dict]) -> str:
        """按模型汇总，拼出 markdown 报告"""
        by_model: dict[str, list[dict]] = {}
        for record in records:
            by_model.setdefault(record['model'], []).append(record)

        lines = ['# 模型对比报告（同一套题、同一套 Agent）',
                 '',
                 f'- 题目数：{len({r["question"] for r in records})}'
                 f' ｜ 总轮次：{len(records)}',
                 '- 判定口径：**降级 = 用户看到的是拒答**；打回 = 引用校验没过一次；'
                 '编号 = 回答里出现的 [Exxx] 个数（0 = 没标来源）',
                 '',
                 '| 模型 | 平均耗时 | 降级 | 打回 | 平均编号数 | 平均字数 | 没调工具 |',
                 '|---|---|---|---|---|---|---|']
        for name, items in by_model.items():
            n = len(items)
            avg_t = sum(i['elapsed'] for i in items) / n
            degraded = sum(1 for i in items if i['degraded'])
            retries = sum(i['retries'] for i in items)
            cites = sum(i['cites'] for i in items) / n
            length = sum(i['length'] for i in items) / n
            no_tool = sum(1 for i in items if not i['tools'] and not i['error'])
            lines.append(f'| {name} | {avg_t:.0f}s | {degraded}/{n} | {retries} '
                         f'| {cites:.1f} | {length:.0f} | {no_tool}/{n} |')

        lines += ['', '## 逐题明细', '',
                  '| 模型 | 题目 | 耗时 | 工具 | 编号 | 打回 | 降级 | 字数 |',
                  '|---|---|---|---|---|---|---|---|']
        for record in records:
            if record['error']:
                lines.append(f'| {record["model"]} | {record["question"]} | '
                             f'{record["elapsed"]:.0f}s | — | — | — | ❌ {record["error"][:40]} | — |')
                continue
            lines.append(f'| {record["model"]} | {record["question"]} | '
                         f'{record["elapsed"]:.0f}s | {"/".join(record["tools"]) or "无"} | '
                         f'{record["cites"]} | {record["retries"]} | '
                         f'{"是" if record["degraded"] else "否"} | {record["length"]} |')
        return '\n'.join(lines) + '\n'


def main() -> int:
    from utils.console import enable_utf8_console
    enable_utf8_console()      # 对比表里有 ✅/⚠️/❌
    parser = argparse.ArgumentParser(description='跨模型对比（真调模型）')
    parser.add_argument('--list', action='store_true', help='只打印候选与 key 状态')
    parser.add_argument('--models', nargs='*', default=None,
                        help='只跑这些（名字片段或模型名），如 qwen3.8-flash 硅基')
    parser.add_argument('--questions', nargs='*', default=None, help='自定义题目')
    parser.add_argument('--rounds', type=int, default=1, help='每题跑几轮（默认 1）')
    parser.add_argument('--no-report', action='store_true', help='不写报告文件')
    args = parser.parse_args()

    if args.list:
        ModelBench.print_list()
        return 0

    questions = args.questions or DEFAULT_QUESTIONS
    targets = ModelBench.available(args.models)
    print(f'候选 {len(targets)} 个 × 题目 {len(questions)} 道 × {args.rounds} 轮'
          f'（预计 {len(targets) * len(questions) * args.rounds * 40 // 60} 分钟上下）')
    for name, _b, _k, model, _n in targets:
        print(f'  - {name}  ({model})')

    records = asyncio.run(ModelBench.run(args.models, questions, args.rounds))
    if not records:
        return 1

    report = ModelBench.summarize(records)
    print('\n' + report)
    if not args.no_report:
        from config.config import BASE_DIR
        path = BASE_DIR / 'data' / 'eval' / 'last_model_bench.md'
        path.write_text(report, encoding='utf-8')
        print(f'报告已写入：{path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
