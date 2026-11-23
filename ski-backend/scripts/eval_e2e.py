"""端到端评测：真跑 Agent，算"回答级"指标（不是检索指标）。

为什么必须单独一套（2026-10-05，外部评审指出）：
    `last_report.md` 只有 39 条**检索**题（hit@1/MRR 那类），它证明"资料找得到"，
    但**证明不了回答可信**——引用是否真实存在、数字是否有据、该拒答时是否拒答、
    价格是否带时效提醒，全都不在检索指标里。
    缺了这层，"答案凭什么可信"就只是文档里的一句话。

指标口径（每条题都算，按 kind 汇总）：
    引用存在    回答里至少有一个 [Exxx]
    引用合法    所有 [Exxx] 都在本轮（含回看窗口）的证据白名单里
    grounding   回答里的数字都能在证据里找到（直接复用 agent.middleware._check_facts，
                不另写一套判据——两套判据必然有一天互相矛盾）
    期望命中    原数字/关键词任一命中；可选 expect_groups 每组任一命中、所有组满足
    时效提示    价格类问题是否提到"录入/过期/时效/复核"
    拒答正确    未收录雪场是否被拒答（模型自己说"未收录"或校验降级都算，
                但两列分开报——"自己拒答"比"被校验拦下"更好）
    降级        本轮是否被引用校验降级（= 用户体验失败）

用法（在 ski-backend 目录下）：
    python -m scripts.eval_e2e --cases data/eval/e2e_challenge_set.yaml --output data/eval/challenge_report.md
    python -m scripts.eval_e2e --limit 8                # 冒烟：跑前 8 条
    python -m scripts.eval_e2e --kinds refusal,price_missing
    python -m scripts.eval_e2e --price-in 1.42 --price-out 7.81      # 带成本
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
import uuid
from pathlib import Path
from scripts.eval_policy import passed

import yaml

from config.config import settings

#: 价格类问题应当提到时效（"录入于 X / 可能已过期 / 以官方为准"）
STALENESS_HINTS = ('录入', '过期', '时效', '复核', '以官方', '最新')
REFUSAL_HINTS = ('未收录', '暂无', '没有收录', '没有相关', '无法', '不掌握', '不清楚',
                 '没有该', '查不到', '没有这部分')


def load_questions(kinds: set[str], limit: int, cases: str | Path | None = None) -> list[dict]:
    path = Path(cases) if cases is not None else Path(settings.DATA_DIR) / 'eval' / 'e2e_set.yaml'
    if not path.exists():
        raise SystemExit(f'题集不存在：{path}（默认题集可先跑 python -m scripts.gen_eval_set）')
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    questions = data.get('questions') or []
    if kinds:
        questions = [item for item in questions if item.get('kind') in kinds]
    return questions[:limit] if limit > 0 else questions


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] * (1 - position + low) + ordered[high] * (position - low)


async def ask(thread: str, question: str) -> dict:
    """跑一轮对话并采集判定所需的一切"""
    from service.agent_service import AgentService
    from agent.middleware import _turn_messages

    started = time.time()
    result = await AgentService.achat(1, thread, question)
    elapsed = time.time() - started

    messages = result.get('messages') or []
    input_tokens = output_tokens = 0
    for message in _turn_messages({'messages': messages}):
        usage = getattr(message, 'usage_metadata', None) or {}
        input_tokens += int(usage.get('input_tokens') or 0)
        output_tokens += int(usage.get('output_tokens') or 0)
    return {'reply': str(result.get('reply') or ''), 'card': result.get('structured'),
            'messages': messages, 'seconds': elapsed,
            'input_tokens': input_tokens, 'output_tokens': output_tokens}


def expectation_matches(word, reply: str) -> bool:
    """纯数字期望按完整数值匹配，引用编号不当作答案事实。"""
    text = re.sub(r'\[E\d+\]', '', reply)
    expected = str(word)
    if re.fullmatch(r'\d+(?:\.\d+)?', expected):
        return bool(re.search(r'(?<![\d.])' + re.escape(expected) + r'(?![\d.])', text))
    return expected in text


def judge(item: dict, run: dict) -> dict:
    """按上表口径判定一条题（纯函数，可离线单测）"""
    from agent.middleware import (DEGRADE_REPLY, _check_facts, _evidence_ids,
                                  _lookback_tool_messages, _turn_messages,
                                  _text_of, _factual_claims)

    reply = run['reply']
    messages = run['messages']
    state = {'messages': messages}

    # 与 middleware 一致：本轮有证据才允许回看，不能用旧资料为本轮空手背书。
    turn = _turn_messages(state)
    current_tools = [m for m in turn if getattr(m, 'type', '') == 'tool']
    current_ids = _evidence_ids(current_tools)
    evidence_messages = current_tools + (
        _lookback_tool_messages(state, settings.CITATION_LOOKBACK_TURNS) if current_ids else [])
    evidence_text = '\n'.join(_text_of(m) for m in evidence_messages)
    allowed = _evidence_ids(evidence_messages)

    cited = [int(x) for x in re.findall(r'\[E(\d+)\]', reply)]
    unsupported = (_check_facts(reply, evidence_text, turn) if current_ids
                   else _factual_claims(reply, turn))
    answered = bool(reply.strip())

    expect_numbers = [int(x) for x in (item.get('expect_numbers') or [])]
    expect_keywords = [str(x) for x in (item.get('expect_keywords') or [])]
    if expect_numbers:
        hit = any(expectation_matches(value, reply) for value in expect_numbers)
    elif expect_keywords:
        hit = any(keyword in reply for keyword in expect_keywords)
    else:
        hit = None

    expect_groups = item.get('expect_groups') or []
    groups_hit = all(any(expectation_matches(word, reply) for word in group) for group in expect_groups)
    if expect_groups:
        hit = groups_hit and hit is not False
    if any(re.search(pattern, reply) for pattern in item.get('forbid_patterns', [])):
        hit = False
    if not answered:
        hit = False

    kind = str(item.get('kind') or '')
    return {
        'answered': answered,
        'cited': bool(cited),
        'citation_valid': answered and all(value in allowed for value in cited),
        'grounded': answered and not unsupported,
        'unsupported': unsupported,
        'hit': hit,
        'stale_notice': any(hint in reply for hint in STALENESS_HINTS),
        'refused': any(hint in reply for hint in REFUSAL_HINTS),
        'degraded': DEGRADE_REPLY[:12] in reply,
        'price_question': kind.startswith('price'),
        'seconds': run['seconds'],
        'input_tokens': run['input_tokens'],
        'output_tokens': run['output_tokens'],
    }


def anonymize(text: str) -> str:
    """报告仅存有界文本；隐藏常见凭据、联系方式和会话标识，不保存工具原文。"""
    text = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+',
                  r'\1[REDACTED]', str(text))
    text = re.sub(r'(?i)sk-[a-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[EMAIL]', text)
    text = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[PHONE]', text)
    text = re.sub(r'e2e-[a-zA-Z0-9_-]+', '[THREAD]', text)
    return text[:2000]


async def run_all(questions: list[dict], progress: bool) -> list[dict]:
    # ⚠️ 线程 ID 必须**每次运行唯一**（2026-10-05 踩到，代价是一次错误结论）：
    #    原来写的是 f'e2e-{item["id"]}'，第二次跑同一批题时会话记忆里已经有上一轮的问答，
    #    模型看到"这题我答过"就**不再检索**、直接复述 → 被引用校验拦下 → 降级率虚高
    #    （实测同一批题：首次 26% → 复用线程后 79%，而我用唯一线程复跑两轮是 0%）。
    #    它与"thread_id 必须每轮唯一"是同一类坑：**记忆是有保质期的，评测更不能用脏记忆**。
    run_id = uuid.uuid4().hex[:8]
    rows: list[dict] = []
    for index, item in enumerate(questions, start=1):
        thread = f'e2e-{run_id}-{item["id"]}'
        run = None
        error = ''
        reply = ''
        try:
            run = await ask(thread, str(item['question']))
            if item.get('followup'):
                first_metrics = {key: run[key] for key in
                                 ('seconds', 'input_tokens', 'output_tokens')}
                run = None  # 追问失败时也不能冒用首问结果
                run = await ask(thread, str(item['followup']))
                for key, value in first_metrics.items():
                    run[key] += value
            reply = anonymize(run['reply'])
            verdict = judge(item, run)
        except Exception as exc:                                 # noqa: BLE001
            error = type(exc).__name__  # 不把第三方异常中的凭据/URL写入报告
            verdict = {'answered': bool(reply.strip()),
                       'cited': False, 'citation_valid': False, 'grounded': False,
                       'unsupported': [error], 'hit': False,
                       'stale_notice': False, 'refused': False, 'degraded': True,
                       'price_question': str(item.get('kind', '')).startswith('price'),
                       'seconds': 0.0, 'input_tokens': 0, 'output_tokens': 0}
        verdict.update({'id': item['id'], 'kind': item['kind'],
                        'question': item['question'], 'followup': item.get('followup'),
                        'reply': reply, 'error': error})
        rows.append(verdict)
        if progress:
            mark = '✅' if passed(verdict) else '⚠️'
            print(f'[{index:3d}/{len(questions)}] {mark} {item["kind"]:13s} '
                  f'{str(item["question"])[:26]:28s} 引用{len(re.findall(r"\[E", reply))}'
                  f' {verdict["seconds"]:.0f}s', flush=True)
    return rows


async def run_cli(questions: list[dict], progress: bool) -> list[dict]:
    """独立 CLI 拥有本进程的连接；普通 run_all 不关闭 Web 服务单例。"""
    try:
        return await run_all(questions, progress)
    finally:
        service = sys.modules.get('service.agent_service')
        checkpointer = getattr(service, '_checkpointer', None)
        if checkpointer is not None:
            try:
                await checkpointer.conn.close()
            finally:
                service._checkpointer = None


def write_report(rows: list[dict], price_in: float, price_out: float,
                 output: str | Path | None = None, cases: str | Path | None = None) -> Path:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row['kind'], []).append(row)

    def rate(items: list[dict], key: str) -> str:
        if not items:
            return '—'
        good = sum(1 for item in items if item.get(key))
        return f'{good}/{len(items)}（{good / len(items) * 100:.0f}%）'

    lines = [
        '# 端到端评测报告（回答级指标）',
        '',
        f'- 题目数：{len(rows)}｜来源：`{cases or Path(settings.DATA_DIR) / "eval" / "e2e_set.yaml"}`',
        f'- 模型：{settings.LLM_PROVIDER}/{settings.LLM_MODEL}｜CARD_MODE={settings.CARD_MODE}',
        f'- 重答开关：全错误={getattr(settings, "CITATION_RETRY_ON_FAIL", False)}｜仅漏引用={getattr(settings, "CITATION_RETRY_MISSING_ONLY", False)}',
        '- 口径：引用合法 = 所有 `[Exxx]` 都在本轮+回看窗口的证据白名单里；'
        'grounding = 回答里的数字都能在证据里找到（复用 `agent.middleware._check_facts`）；'
        '拒答 = 模型自己说"未收录/暂无"（与"被校验降级"分开统计）',
        '- 期望组：每组至少命中一个词且所有组满足；仅为词面契约，不代表完整语义忠实度。空回答不通过。',
        '',
        '## 按题型',
        '',
        '| 题型 | 题数 | 引用存在 | 引用合法 | grounding | 期望命中 | 时效提示 | 拒答 | 降级 | P50 耗时 | 平均输入 token |',
        '|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    for kind, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        hits = [item for item in items if item['hit'] is not None]
        price_items = [item for item in items if item['price_question']]
        seconds = [item['seconds'] for item in items]
        avg_in = sum(item['input_tokens'] for item in items) / len(items)
        lines.append(
            f'| {kind} | {len(items)} | {rate(items, "cited")} | {rate(items, "citation_valid")} '
            f'| {rate(items, "grounded")} | {rate(hits, "hit") if hits else "—"} '
            f'| {rate(price_items, "stale_notice") if price_items else "—"} '
            f'| {rate(items, "refused")} | {rate(items, "degraded")} '
            f'| {percentile(seconds, 0.5):.1f}s | {avg_in:.0f} |')

    total_seconds = [row['seconds'] for row in rows]
    total_in = sum(row['input_tokens'] for row in rows)
    total_out = sum(row['output_tokens'] for row in rows)
    lines += [
        '',
        '## 总体',
        '',
        f'- 验收通过：{sum(passed(row) for row in rows)}/{len(rows)}',
        f'- 引用存在：{rate(rows, "cited")}｜引用合法：{rate(rows, "citation_valid")}'
        f'｜grounding 通过：{rate(rows, "grounded")}｜被降级：{rate(rows, "degraded")}',
        f'- 耗时：P50 {percentile(total_seconds, 0.5):.1f}s｜P95 {percentile(total_seconds, 0.95):.1f}s',
        f'- token：输入合计 {total_in}｜输出合计 {total_out}',
        '',
        '> ⚠️ 读表提醒：`price_missing` 这类**期望拒答**的题，"引用存在 0%" 是**正确行为**'
        '（该说"暂无"而不是硬标一个来源）；看它要看"拒答"那一列。',
    ]
    if price_in or price_out:
        cost = (total_in * price_in + total_out * price_out) / 1_000_000
        lines.append(f'- 成本：≈ {cost:.3f} 元（单题 ≈ {cost / max(len(rows), 1):.4f} 元）')

    bad = [row for row in rows if not passed(row)]
    lines += ['', '## 未通过的题（按上面口径）', '']
    if not bad:
        lines.append('- 无')
    for row in bad[:30]:
        reason = []
        if not row['grounded']:
            reason.append(f'无据数字 {row["unsupported"][:3]}')
        if not row['citation_valid']:
            reason.append('引用编号不在白名单')
        if row['degraded']:
            reason.append('被降级')
        if row['hit'] is False:
            reason.append('期望未命中/空回答')
        if row.get('error'):
            reason.append('运行异常 ' + row['error'])
        if not reason:
            reason.append('未满足验收口径（事实引用/价格时效/明确拒答）')
        lines.append(f'- `{row["id"]}` [{row["kind"]}] {row["question"][:40]} → {"；".join(reason)}')

    lines += [
        '',
        '## 阈值（建议，待基线稳定后接入 CI）',
        '',
        '| 指标 | 建议阈值 | 理由 |',
        '|---|---|---|',
        '| 引用合法率 | 100% | 引用不存在的编号是硬错，没有容忍空间 |',
        '| grounding 通过率 | ≥ 95% | 允许极少数表述差异，但不能有系统性无据数字 |',
        '| 拒答正确率（refusal 题） | 100% | 拿相邻雪场数据补答会直接砸掉"可溯源"命题 |',
        '| 降级率 | ≤ 5% | 降级=用户看到拒答，是体验失败 |',
        '| price_missing 类 | 必须命中"暂无" | 收录但缺数据时编价格是最严重的错 |',
        '',
        '> 这份报告是**基线的唯一来源**（与 `last_report.md` 分工：那份管检索，这份管回答）。',
    ]

    lines += ['', '## 逐题验收（匿名化回复摘要）', '']
    for row in rows:
        lines.append(f'### {row["id"]} [{row["kind"]}]')
        lines.append(f'- 问题：{anonymize(row["question"])}')
        if row.get('followup'):
            lines.append(f'- 追问：{anonymize(row["followup"])}')
        lines.append(f'- 期望命中：{row["hit"]}｜异常：{anonymize(row.get("error") or "无")}')
        lines.append('')
        lines.extend('> ' + line for line in (anonymize(row.get('reply') or '') or '（空回答）').splitlines())
        lines.append('')
    path = Path(output) if output is not None else Path(settings.DATA_DIR) / 'eval' / 'last_e2e.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    return path


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()

    parser = argparse.ArgumentParser(description='端到端回答质量评测')
    parser.add_argument('--cases', help='人工 YAML 题集路径（相对当前目录）')
    parser.add_argument('--output', help='报告路径；自定义题集默认写其同名 .md，不覆盖基线')
    parser.add_argument('--limit', type=int, default=0, help='只跑前 N 条（0=全部）')
    parser.add_argument('--kinds', default='', help='只跑这些题型（逗号分隔）')
    parser.add_argument('--price-in', type=float, default=0.0, help='输入单价（元/百万 token）')
    parser.add_argument('--price-out', type=float, default=0.0, help='输出单价（元/百万 token）')
    parser.add_argument('--metrics-output', type=Path, help='仅保存逐题评分与成本，不保存回复/工具/异常原文')
    parser.add_argument('--fail-on-error', action='store_true', help='报告仍落盘；任一题验收失败退出1')
    parser.add_argument('--quiet', action='store_true', help='不逐条打印进度')
    args = parser.parse_args()

    output = args.output or (str(Path(args.cases).with_suffix('.md')) if args.cases else None)
    baseline = Path(settings.DATA_DIR) / 'eval' / 'last_e2e.md'
    if args.cases and output and Path(output).resolve() == baseline.resolve():
        raise SystemExit('人工题集报告不能覆盖 last_e2e.md，请指定其他 --output')

    kinds = {k.strip() for k in args.kinds.split(',') if k.strip()}
    questions = load_questions(kinds, args.limit, args.cases)
    if not questions:
        raise SystemExit('没有匹配的题目')

    print(f'端到端评测：{len(questions)} 条｜模型 '
          f'{settings.LLM_PROVIDER}/{settings.LLM_MODEL}'
          f'{"｜题型 " + ",".join(sorted(kinds)) if kinds else ""}')
    rows = asyncio.run(run_cli(questions, progress=not args.quiet))
    path = write_report(rows, args.price_in, args.price_out, output, args.cases)
    print(f'报告已写入 {path}')
    if args.metrics_output:
        import json
        fields = ('id', 'kind', 'answered', 'cited', 'citation_valid', 'grounded',
                  'hit', 'stale_notice', 'refused', 'degraded', 'price_question',
                  'seconds', 'input_tokens', 'output_tokens')
        args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_output.write_text(json.dumps(
            [{key: row.get(key) for key in fields} for row in rows],
            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    if args.fail_on_error and any(not passed(row) for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
