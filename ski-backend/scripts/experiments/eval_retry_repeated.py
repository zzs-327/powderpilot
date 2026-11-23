"""隔离进程、交替顺序的漏引用重答重复实验；不修改 .env。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


from scripts.eval_policy import passed


def summarize(records):
    lines = ['# 漏引用重答重复实验', '',
             '独立进程、唯一会话、每轮交替顺序。小样本观察，不代表统计显著或普遍语义准确率。', '',
             '| 开关 | 通过/总题 | 降级 | 平均耗时 | 输入token | 输出token |',
             '|---|---|---|---|---|---|']
    for mode in ('off', 'on'):
        rows = [r for run in records if run['mode'] == mode for r in run['rows']]
        if not rows:
            continue
        lines.append(f'| {mode} | {sum(passed(r) for r in rows)}/{len(rows)} | '
                     f'{sum(bool(r.get("degraded")) for r in rows)} | '
                     f'{sum(r["seconds"] for r in rows)/len(rows):.1f}s | '
                     f'{sum(r["input_tokens"] for r in rows)} | {sum(r["output_tokens"] for r in rows)} |')
    lines += ['', '## 逐题重复通过', '', '| 题目 | off | on |', '|---|---|---|']
    ids = sorted({r['id'] for run in records for r in run['rows']})
    for id_ in ids:
        cells = []
        for mode in ('off', 'on'):
            rows = [r for run in records if run['mode'] == mode for r in run['rows'] if r['id'] == id_]
            cells.append(f'{sum(passed(r) for r in rows)}/{len(rows)}')
        lines.append(f'| {id_} | {cells[0]} | {cells[1]} |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path('data/eval/e2e_challenge_set.yaml'))
    parser.add_argument('--fail-on-error', action='store_true', help='汇总仍落盘；任一题失败退出1')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--output', type=Path, default=Path('data/eval/e2e_retry_repeated.md'))
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5:
        parser.error('rounds 必须在1到5之间，限制真实API调用预算')
    if args.output.name in {'last_e2e.md', 'last_report.md'}:
        parser.error('重复实验不能覆盖正式基线报告')
    if not args.cases.is_file():
        parser.error('题集文件不存在')
    root = Path('.test-deps') / ('retry-repeat-' + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    records = []
    for round_ in range(args.rounds):
        for mode in (('off', 'on') if round_ % 2 == 0 else ('on', 'off')):
            stem = root / f'{round_+1}-{mode}'
            env = dict(os.environ, CITATION_RETRY_ON_FAIL='false',
                       CITATION_RETRY_MISSING_ONLY=str(mode == 'on').lower(), PYTHONIOENCODING='utf-8')
            # 文件句柄不是管道：日志留在本机忽略目录，不进入汇总报告。
            with stem.with_suffix('.log').open('w', encoding='utf-8') as log:
                result = subprocess.run([sys.executable, '-m', 'scripts.eval_e2e', '--cases', str(args.cases),
                                         '--output', str(stem.with_suffix('.md')), '--metrics-output',
                                         str(stem.with_suffix('.json'))], env=env, stdout=log,
                                        stderr=log, timeout=600)
            if result.returncode:
                raise SystemExit(f'第{round_+1}轮 {mode} 进程失败；本机日志目录：{root}')
            rows = json.loads(stem.with_suffix('.json').read_text(encoding='utf-8'))
            if not rows:
                raise SystemExit('评测无结果，不能生成成功报告')
            ids = [row['id'] for row in rows]
            if len(ids) != len(set(ids)) or (records and ids != [row['id'] for row in records[0]['rows']]):
                raise SystemExit('各次题目不一致或ID重复，拒绝汇总')
            records.append({'round': round_+1, 'mode': mode, 'rows': rows})
            print(f'轮{round_+1} {mode}: {sum(passed(r) for r in rows)}/{len(rows)}', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    header = (f'> 轮数：{args.rounds}；题集：{args.cases.name}；'
              f'SHA256：{hashlib.sha256(args.cases.read_bytes()).hexdigest()}\n\n')
    args.output.write_text(header + summarize(records), encoding='utf-8')
    args.output.with_suffix('.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'汇总已写入 {args.output}')
    if args.fail_on_error and any(not passed(row) for run in records for row in run['rows']):
        raise SystemExit(1)


if __name__ == '__main__':
    from utils.console import enable_utf8_console
    enable_utf8_console()
    main()
