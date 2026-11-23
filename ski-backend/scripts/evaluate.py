"""评估脚本：把"检索效果还行"变成可复现的数字。

用法（在 ski-backend 目录下）：
    python -m scripts.evaluate              # 跑全部用例，打印报告
    python -m scripts.evaluate -k 5         # 只看 top5 命中率

指标说明：
    hit@1 / hit@3 / hit@5   正确答案是否出现在前 k 条证据里（查全率）
    MRR                     第一条命中的排名倒数均值（越接近 1 越好）
    分层命中率                带画像过滤的用例，top3 是否都是该水平或通用内容

报告同时写入 data/eval/last_report.md，方便写复盘文档。
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import yaml

from config.config import settings
from rag.retriever import retrieve


def audit_refusal_cases(cases: list[dict]) -> list[str]:
    """体检：拒答用例问的雪场，是不是已经被收录了？

    为什么需要（同一个坑踩了两次）：`r02` 问太舞、`r06` 问富龙，
    它们当初都是"未收录 → 应当拒答"；等我们把这两座雪场收录进来之后，
    用例的**预期就自动失效了**——检索能召回到资料，于是判"误召回"，拒答率从 100% 掉到 83%。
    它不会报错、只会静默变红，很容易被当成"检索退化"而查错方向。

    所以每次评估前先扫一遍：拒答用例里出现**已收录雪场名**的，直接点名提醒。
    """
    from rag.parser import ResortRegistry, resort_name_map

    covered = {name for name, _ in resort_name_map().items()}
    problems = []
    for case in cases:
        if case.get('kind') != 'refusal':
            continue
        question = str(case.get('question') or '')
        hit = [name for name in covered if name and name in question]
        if hit and not ResortRegistry.detect_unknown(question):
            problems.append(f"{case['id']}「{question}」问到了已收录雪场 {hit}——"
                            '该用例应先换一座仍未收录的雪场')
    return problems


def load_cases(path: Path | None = None) -> list[dict]:
    path = path or Path(settings.DATA_DIR) / 'eval' / 'golden_set.yaml'
    return yaml.safe_load(path.read_text(encoding='utf-8')) or []


def is_hit(doc, expect_any: list[list[str]]) -> bool:
    """任意一组子串全部出现在同一条 chunk 里，即算命中。"""
    text = doc.page_content
    return any(all(sub in text for sub in group) for group in expect_any)


def first_hit_rank(docs: list, expect_any: list[list[str]]) -> int:
    for rank, doc in enumerate(docs, start=1):
        if is_hit(doc, expect_any):
            return rank
    return 0


def run(k: int = 5, cases: list[dict] | None = None) -> dict:
    cases = load_cases() if cases is None else cases
    rows = []
    hit1 = hit3 = hitk = 0
    mrr_sum = 0.0
    retrieval_total = 0
    refusal_ok = refusal_total = 0
    level_ok = level_total = 0

    skipped = 0
    for case in cases:
        if case.get('pending'):
            # pending 用例：数据已备好但**还没进向量库**（例如向量库规模故障期间新收录的雪场）。
            # 直接跳过、并在报告里说明，避免"数据没入库"被误读成"检索退化"。
            skipped += 1
            continue
        kind = case.get('kind', 'retrieval')
        question = case['question']
        docs = retrieve(question, resort='', level=case.get('level', ''),
                        board=case.get('board', ''), top_n=k)

        if kind == 'refusal':
            refusal_total += 1
            ok = len(docs) == 0
            refusal_ok += 1 if ok else 0
            rows.append({'id': case['id'], 'kind': kind, 'question': question,
                         'result': '✅ 正确拒答' if ok else f'❌ 误召回 {len(docs)} 条',
                         'rank': '-', 'top': docs[0].page_content[:40] if docs else ''})
            continue

        retrieval_total += 1
        rank = first_hit_rank(docs, case['expect_any'])
        coverage_ok = all(any(all(term in doc.page_content for term in group) for doc in docs)
                          for group in case.get('require_groups', []))
        isolation_ok = all(doc.metadata.get('resort_id') == case['expected_resort'] for doc in docs) if case.get('expected_resort') else True
        if not coverage_ok or not isolation_ok:
            rank = 0
        hit1 += 1 if rank == 1 else 0
        hit3 += 1 if 0 < rank <= 3 else 0
        hitk += 1 if 0 < rank <= k else 0
        mrr_sum += (1.0 / rank) if rank else 0.0

        level_note = ''
        if case.get('level'):
            level_total += 1
            wanted = case['level']
            top3 = docs[:3]
            good = all(doc.metadata.get('level') in (wanted, 'all') for doc in top3)
            level_ok += 1 if good else 0
            level_note = '分层✅' if good else '分层❌'

        rows.append({'id': case['id'], 'kind': kind, 'question': question,
                     'result': f'命中@{rank}' if rank else f'未命中（top{k}）',
                     'rank': rank, 'top': docs[0].page_content[:40] if docs else '',
                     'level_note': level_note})

    return {
        'rows': rows,
        'retrieval_total': retrieval_total,
        'hit1': hit1, 'hit3': hit3, 'hitk': hitk, 'k': k,
        'mrr': (mrr_sum / retrieval_total) if retrieval_total else 0.0,
        'refusal_total': refusal_total, 'refusal_ok': refusal_ok,
        'skipped': skipped,
        'level_total': level_total, 'level_ok': level_ok,
    }


def render(result: dict) -> str:
    lines = ['# 检索评估报告', '',
             f'> 生成时间：{dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
             # 只写相对目录名：这份报告会进仓库、会发给别人，
             # 绝对路径属于个人信息泄漏（开源前审查脚本会把它当问题报出来，
             # 见 scripts/pre_open_source_check.py 的第 4 项）
             f'> 语料：{Path(settings.RESORT_DIR).name}/', '']
    lines.append('| 指标 | 数值 |')
    lines.append('|---|---|')
    n = result['retrieval_total']
    if n:
        lines.append(f"| 检索用例数 | {n} |")
        lines.append(f"| hit@1 | {result['hit1']}/{n} = {result['hit1']/n:.0%} |")
        lines.append(f"| hit@3 | {result['hit3']}/{n} = {result['hit3']/n:.0%} |")
        lines.append(f"| hit@{result['k']} | {result['hitk']}/{n} = {result['hitk']/n:.0%} |")
        lines.append(f"| MRR | {result['mrr']:.3f} |")
    if result['refusal_total']:
        lines.append(f"| 拒答正确率 | {result['refusal_ok']}/{result['refusal_total']} "
                     f"= {result['refusal_ok']/result['refusal_total']:.0%} |")
    if result.get('skipped'):
        # pending 用例不计入指标：数据已备好但没进向量库（见docs/TROUBLESHOOTING.md #24）
        lines.append(f"| 跳过（pending） | {result['skipped']} 条 |")
    if result['level_total']:
        lines.append(f"| 分层命中率 | {result['level_ok']}/{result['level_total']} "
                     f"= {result['level_ok']/result['level_total']:.0%} |")
    lines += ['', '## 明细', '', '| id | 类型 | 问题 | 结果 | top1 摘要 |', '|---|---|---|---|---|']
    for row in result['rows']:
        lines.append(f"| {row['id']} | {row['kind']} | {row['question']} | "
                     f"{row['result']} {row.get('level_note', '')} | {row['top']} |")
    return '\n'.join(lines)


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()      # 报告里有 ✅/❌，Windows 控制台默认 GBK 会打印崩掉
    parser = argparse.ArgumentParser(description='检索评估')
    parser.add_argument('-k', type=int, default=5, help='top-k（默认 5）')
    parser.add_argument('--cases', type=Path, help='指定题集，默认原始 golden_set.yaml')
    parser.add_argument('--output', type=Path, help='指定报告路径，默认 last_report.md')
    args = parser.parse_args()
    cases = load_cases(args.cases)
    for problem in audit_refusal_cases(cases):
        print(f'⚠️  拒答用例失效：{problem}')

    result = run(args.k, cases)
    report = render(result)
    print(report)

    out = args.output or Path(settings.DATA_DIR) / 'eval' / 'last_report.md'
    out.write_text(report, encoding='utf-8')
    print(f'\n报告已写入：{out}')


if __name__ == '__main__':
    main()
