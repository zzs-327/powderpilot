"""端到端评测题集生成器：从策展档案**确定性派生**题目（含期望值）。

为什么从 YAML 派生，而不是手写一份题目清单（2026-10-05）：
    外部评审指出"只有 39 条检索题，证明不了端到端回答质量"。
    手写题集的问题是**会过期**：数据一改、雪场一加，题目与期望值就各说各话，
    最后没人再跑它。从真相源派生则相反——加一座雪场，题目自动多十几条。
    这也让"改数据 → 重跑评测"成为一条命令的事（对应 AGENTS.md 的约定 4/5）。

题集覆盖的十类（前四类是重点，因为它们对应项目的核心命题）：

    price / price_tier   有价格的雪场问票价 → 期望**带具体数字 + 引用编号 + 时效提示**
    price_missing        收录但**没有价格数据**的雪场问票价 → 期望明确说"暂无"，**不许编**
    runs_*               雪道条数 → 期望数字与档案一致（或如实说明口径分歧）
    level_*              适合水平 → 期望给出可执行建议且有来源
    meta                 数据是几手的 → 期望如实说出可信度档位
    lifts / tips         索道与注意事项 → 期望有具体内容与来源
    refusal              **未收录**雪场 → 期望拒答（不拿相邻雪场数据补答）
    isolation            跨雪场隔离 → 期望数字都能在证据里找到（隐含"没串场"）
    followup             多轮追问 → 期望第二轮仍能正确引用（跨轮编号）

用法：
    python -m scripts.gen_eval_set                 # 写入 data/eval/e2e_set.yaml
    python -m scripts.gen_eval_set --print         # 只打印摘要与样例
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import yaml

from config.config import settings

#: 未收录的雪场（用来出拒答题）。刻意选国内外知名雪场：越"看起来该知道"，
#: 越能试出模型会不会拿相邻雪场的数据补答。
UNCOLLECTED = ['二世谷', '长白山万达', '亚布力', '阿斯彭', '圣莫里茨', '札幌国际',
               '惠斯勒', '霞慕尼']

REFUSAL_KEYWORDS = ['未收录', '暂无', '没有收录', '没有相关', '无法', '不掌握', '不清楚',
                    '没有该', '查不到', '没有这部分']

LEVELS = (('beginner', '新手'), ('intermediate', '中级'), ('advanced', '老手'))


def load_archives() -> list[dict]:
    """读所有雪场档案（跳过 _template）"""
    out: list[dict] = []
    for path in sorted(Path(settings.RESORT_DIR).glob('*.yaml')):
        if path.stem.startswith('_'):
            continue
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        data['_stem'] = path.stem
        out.append(data)
    return out


def _alias(data: dict) -> str:
    """用别名提问更像真人说话（档案里 aliases 就是为这个准备的）"""
    aliases = data.get('aliases') or []
    return str(aliases[0]) if aliases else str(data.get('name') or data['_stem'])


def _runs_counts(data: dict) -> dict:
    summary = data.get('runs_summary') or {}
    return {key: int(value) for key, value in summary.items() if isinstance(value, int)}


def _tier_of(item: str) -> str:
    for keyword in ('平日', '周末', '节假日', '夜场', '季卡'):
        if keyword in item:
            return keyword
    return ''


def build_questions(archives: list[dict]) -> list[dict]:
    questions: list[dict] = []
    counter = 0

    def add(kind: str, resort: str, question: str, *, expect_numbers=(),
            expect_keywords=(), note: str = '', followup: str = '') -> None:
        nonlocal counter
        counter += 1
        questions.append({
            'id': f'q{counter:03d}',
            'kind': kind,
            'resort': resort,
            'question': question,
            'followup': followup,
            'expect_numbers': [int(x) for x in expect_numbers],
            'expect_keywords': list(expect_keywords),
            'note': note,
        })

    for data in archives:
        stem = data['_stem']
        alias = _alias(data)
        name = str(data.get('name') or alias)
        prices = data.get('prices') or []
        lifts = data.get('lifts') or []
        tips = data.get('tips') or []
        counts = _runs_counts(data)
        total = sum(counts.values())

        # 1.价格：有数据 → 期望命中具体金额
        if prices:
            values = [row['price'] for row in prices if isinstance(row.get('price'), (int, float))]
            add('price', stem, f'{alias}雪票多少钱？', expect_numbers=values[:12],
                note=f'{name} 共 {len(prices)} 条价格')
            # 按档位细问（平日/周末/节假日），逼它别混档
            for tier in ('平日', '周末', '节假日'):
                hit = [row['price'] for row in prices
                       if isinstance(row.get('price'), (int, float)) and tier in str(row.get('item') or '')]
                if hit:
                    add('price_tier', stem, f'{alias}{tier}的雪票多少钱？', expect_numbers=hit[:4],
                        note=f'{tier}档')
            # 按项目细问（租赁 / 含雪具 / 夜场）：这类问题最容易串行串档
            for keyword, question in (('租赁', f'{alias}雪具租赁多少钱？'),
                                      ('含雪具', f'{alias}含雪具的雪票多少钱？'),
                                      ('夜场', f'{alias}有夜场票吗？多少钱？')):
                hit = [row['price'] for row in prices
                       if isinstance(row.get('price'), (int, float)) and keyword in str(row.get('item') or '')]
                if hit:
                    add('price_item', stem, question, expect_numbers=hit[:4], note=f'{keyword}项')
        # 2.价格缺失：收录但没价格 → 必须说"暂无"，绝不能编
        else:
            add('price_missing', stem, f'{alias}雪票多少钱？', expect_keywords=['暂无', '没有', '未收录', '没有收录'],
                note=f'{name} 收录但无价格数据（这是一道"该说没有"的题）')

        # 3.雪道条数（两个难度 + 总数）
        for key, label in (('advanced', '高级道'), ('intermediate', '中级道')):
            if counts.get(key):
                add('runs_count', stem, f'{alias}有几条{label}？',
                    expect_numbers=[counts[key]], note=f'{name} {label} {counts[key]} 条')
        if total:
            add('runs_total', stem, f'{alias}一共有几条雪道？', expect_numbers=[total],
                note=f'{name} 分难度合计 {total} 条（注意与"来源声明总数"可能不同口径）')

        # 4.适合水平
        for key, label in LEVELS[:2]:
            if counts.get(key) or (data.get('by_level') or {}).get(key):
                add('level', stem, f'{alias}适合{label}吗？',
                    note=f'{name} {label}（档案里有 by_level 说明）')

        # 5.元信息：数据是几手的
        add('meta', stem, f'{alias}的数据是几手的？',
            expect_keywords=['已核对', '多源印证', '二手转引', '示例', '官方', '转引'],
            note=f'{name} 可信度档位')

        # 6.索道 / 注意事项（有数据才出题）
        if lifts:
            add('lifts', stem, f'{alias}有哪些索道？',
                expect_keywords=[str(row.get('name')) for row in lifts if row.get('name')][:6],
                note=f'{name} 收录 {len(lifts)} 条索道')
        if tips:
            add('tips', stem, f'{alias}有什么注意事项？',
                note=f'{name} 收录 {len(tips)} 条提示')

    # 7.拒答：未收录雪场
    for index, place in enumerate(UNCOLLECTED):
        kind = 'refusal'
        add(kind, '', f'{place}的雪票多少钱？', expect_keywords=REFUSAL_KEYWORDS,
            note=f'{place} 本库未收录，应当拒答而不是拿相邻雪场数据补答')

    # 8.跨雪场隔离：拿两个都有价格的雪场交叉问（正反各一次，避免"第一顺位"偏差）
    priced = [data for data in archives if data.get('prices')]
    for index, data in enumerate(priced):
        other = priced[(index + 1) % len(priced)]
        values = [row['price'] for row in data.get('prices') or []
                  if isinstance(row.get('price'), (int, float))]
        add('isolation', data['_stem'],
            f'{_alias(data)}和{_alias(other)}，哪个雪票更便宜？',
            expect_numbers=values[:8],
            note=f'答案里出现的金额必须都能在证据里找到（跨雪场串数据会立刻暴露）')
    for index, data in enumerate(reversed(priced)):
        other = priced[(index + 1) % len(priced)]
        values = [row['price'] for row in data.get('prices') or []
                  if isinstance(row.get('price'), (int, float))]
        add('isolation', data['_stem'],
            f'{_alias(other)}和{_alias(data)}哪个贵？',
            expect_numbers=values[:8], note='反向再问一次，排除位置偏差')

    # 9.多轮追问：跨轮编号与上下文（这是"追问被判成编造"那类坑的回归测试）
    #   9a 每个雪场都来一组"高级道 → 中级道"，考的是同一轮证据内的编号沿用
    for data in archives:
        alias = _alias(data)
        counts = _runs_counts(data)
        if counts.get('advanced') and counts.get('intermediate'):
            add('followup', data['_stem'], f'{alias}有几条高级道？',
                followup=f'那{alias}呢，中级道有几条？',
                expect_numbers=[counts['advanced'], counts['intermediate']],
                note='同一轮内追问，编号不能乱')
    #   9b 有价格的雪场：雪道 → 价格（跨工具、跨轮引用）
    for data in archives:
        alias = _alias(data)
        prices = data.get('prices') or []
        if not prices:
            continue
        values = [row['price'] for row in prices if isinstance(row.get('price'), (int, float))]
        add('followup', data['_stem'], f'{alias}有几条雪道？', followup=f'那{alias}雪票多少钱？',
            expect_numbers=values[:8], note='第二轮要能沿用上一轮编号而不被判成编造')

    return questions


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()

    parser = argparse.ArgumentParser(description='生成端到端评测题集')
    parser.add_argument('--print', action='store_true', help='只打印摘要，不写文件')
    args = parser.parse_args()

    archives = load_archives()
    questions = build_questions(archives)

    kinds: dict[str, int] = {}
    for item in questions:
        kinds[item['kind']] = kinds.get(item['kind'], 0) + 1

    print(f'档案 {len(archives)} 座 → 题目 {len(questions)} 条')
    for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f'  {kind:14s} {count:3d}')
    if questions:
        print('\n样例：')
        for item in questions[:3] + questions[-2:]:
            print(f'  [{item["kind"]}] {item["question"]}'
                  + (f' → 追问：{item["followup"]}' if item['followup'] else '')
                  + (f'（期望数字 {item["expect_numbers"][:3]}）' if item['expect_numbers'] else ''))

    if args.print:
        return

    path = Path(settings.DATA_DIR) / 'eval' / 'e2e_set.yaml'
    path.write_text(yaml.safe_dump({
        'generated_at': date.today().isoformat(),
        'source': 'data/resorts/*.yaml（确定性派生，改数据后重新生成即可）',
        'count': len(questions),
        'questions': questions,
    }, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'\n已写入 {path}')


if __name__ == '__main__':
    main()
