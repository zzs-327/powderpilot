"""样板雪场对照表：拿每个雪场的 YAML 与样板（北大湖）逐项比较，列出还差什么。

用法（在 ski-backend 目录下）：
    python -m scripts.resort_scorecard              # 全部雪场
    python -m scripts.resort_scorecard taiwu         # 只看一个
    python -m scripts.resort_scorecard --write       # 同时写 data/eval/resort_scorecard.md

只读 YAML 与 golden set，不连数据库、不调模型。
检查项的门槛取自北大湖样板（2026-10-11）；「价格」不设硬门槛，
但没有价格时必须在 provenance / tips 里写明原因（未知 ≠ 0）。
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from config.config import settings
from utils.console import enable_utf8_console

BENCHMARK = 'beidahu'
RESORT_DIR = Path(settings.DATA_DIR) / 'resorts'
GOLDEN = Path(settings.DATA_DIR) / 'eval' / 'golden_set.yaml'


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _load(resort_id: str) -> dict:
    data = yaml.safe_load((RESORT_DIR / f'{resort_id}.yaml').read_text(encoding='utf-8'))
    return data if isinstance(data, dict) else {}


def _golden_count(resort_id: str, names: list[str]) -> int:
    cases = yaml.safe_load(GOLDEN.read_text(encoding='utf-8')) or []
    return sum(1 for case in cases if isinstance(case, dict) and (
        case.get('expected_resort') == resort_id
        or any(name and name in str(case.get('question', '')) for name in names)))


def score(resort_id: str) -> list[Check]:
    data = _load(resort_id)
    names = [str(data.get('name') or ''), *[str(a) for a in data.get('aliases') or []]]
    runs = data.get('runs') or []
    lifts = data.get('lifts') or []
    prices = data.get('prices') or []
    tips = [t for t in data.get('tips') or [] if isinstance(t, dict)]
    levels = [k for k, v in (data.get('by_level') or {}).items() if isinstance(v, dict) and v.get('summary')]
    media = [m for m in data.get('media') or [] if isinstance(m, dict)]
    kinds = [m.get('kind') for m in media]
    official_video = [m for m in media if m.get('kind') == 'video'
                      and re.search(r'(?<!非)官方', str(m.get('source', '')))]
    trail_map = [m for m in media if m.get('kind') == 'map' or m.get('category') == 'trail_map']
    authorized = [m for m in media if m.get('rights') == 'authorized'] + list(data.get('photos') or [])
    sourced_tips = [t for t in tips if t.get('source') and t.get('date')]
    no_price_reason = any(re.search(r'价格|价目|票价', str(t.get('text', ''))) for t in tips) or bool(
        re.search(r'价格|价目', str((data.get('provenance') or {}).get('caveat', '')) + str(
            ((data.get('provenance') or {}).get('corroboration') or ''))))
    golden = _golden_count(resort_id, names)
    status = str((data.get('provenance') or {}).get('data_status') or '')

    return [
        Check('来源状态', status in ('verified', 'transcribed'), status or '未标注'),
        Check('雪道明细', len(runs) >= 10, f'{len(runs)} 条'),
        Check('索道明细', len(lifts) >= 3, f'{len(lifts)} 条'),
        Check('价格', bool(prices) or no_price_reason,
              f'{len(prices)} 条' if prices else ('无价格，已写明原因' if no_price_reason else '无价格且没写原因')),
        Check('分层建议', len(levels) == 3, '、'.join(levels) or '无'),
        Check('避坑与提示', len(tips) >= 5 and len(sourced_tips) == len(tips),
              f'{len(tips)} 条，带来源和日期 {len(sourced_tips)} 条'),
        Check('雪道图', bool(trail_map), f'{len(trail_map)} 张'),
        Check('官方视频', bool(official_video), f'{len(official_video)} 条'),
        Check('视频推荐', kinds.count('video') >= 3, f'{kinds.count("video")} 条'),
        Check('图文 / 打卡', kinds.count('photo') >= 2, f'{kinds.count("photo")} 条'),
        Check('可直接显示的照片', bool(authorized), f'{len(authorized)} 张（需本人拍摄或作者授权）'),
        Check('评估题', golden >= 4, f'{golden} 题'),
    ]


def render(rows: dict[str, list[Check]]) -> str:
    headers = [c.name for c in next(iter(rows.values()))]
    lines = ['| 雪场 | 达标 | ' + ' | '.join(headers) + ' |',
             '|---|---|' + '---|' * len(headers)]
    for resort_id, checks in rows.items():
        mark = ' （样板）' if resort_id == BENCHMARK else ''
        passed = sum(c.ok for c in checks)
        cells = [('✅ ' if c.ok else '❌ ') + c.detail for c in checks]
        lines.append(f'| {resort_id}{mark} | {passed}/{len(checks)} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def main() -> None:
    enable_utf8_console()
    parser = argparse.ArgumentParser(description='样板雪场对照表')
    parser.add_argument('resort_ids', nargs='*', help='雪场 id，留空表示全部')
    parser.add_argument('--write', action='store_true', help='写入 data/eval/resort_scorecard.md')
    args = parser.parse_args()
    ids = args.resort_ids or sorted(p.stem for p in RESORT_DIR.glob('*.yaml') if not p.stem.startswith('_'))
    ids = [BENCHMARK] + [i for i in ids if i != BENCHMARK]
    table = render({i: score(i) for i in ids})
    print(table)
    if args.write:
        out = Path(settings.DATA_DIR) / 'eval' / 'resort_scorecard.md'
        out.write_text('# 样板雪场对照表\n\n> 由 `python -m scripts.resort_scorecard --write` 生成；'
                       f'样板为 {BENCHMARK}。\n\n' + table + '\n', encoding='utf-8')
        print(f'\n已写入 {out}')


if __name__ == '__main__':
    main()
