"""从档案确定性生成扩展检索题；不覆盖原始人工 golden set。"""
from pathlib import Path
import yaml
from config.config import settings
from scripts.gen_eval_set import load_archives
from rag.parser import ResortRegistry


def build_cases(archives: list[dict]) -> list[dict]:
    cases = []
    def add(stem, suffix, question, expected):
        # expected_resort：问题里点了雪场名，就断言"返回的每条 chunk 都属于该雪场"。
        # retrieve() 会自己从问题里识别雪场并做 metadata 隔离，所以不加 resort 参数也能成立；
        # 这条断言（evaluate.py 的 isolation_ok）此前 0 条用例使用，等于没被测过。
        cases.append({'id': f'{stem}:{suffix}', 'kind': 'retrieval',
                      'question': question, 'expect_any': [expected],
                      'expected_resort': stem})

    for data in archives:
        stem, name = data['_stem'], data['name']
        alias = (data.get('aliases') or [name])[0]
        if data.get('region'):
            for i, question in enumerate([f'{alias}在哪里', f'{alias}位于哪个地区', f'{alias}的地址在哪']):
                add(stem, f'location{i}', question, [name, data['region']])
        official = data.get('official') or {}
        if official.get('runs_total'):
            add(stem, 'count', f'{alias}一共有几条雪道', [name, str(official['runs_total'])])
        for level, count in (data.get('runs_summary') or {}).items():
            label = {'beginner': '初级', 'intermediate': '中级', 'advanced': '高级'}.get(level)
            if label:
                add(stem, level, f'{alias}有几条{label}道', [name, f'{label}道 {count} 条'])
        for i, run in enumerate((data.get('runs') or [])[:2]):
            add(stem, f'run{i}', f'{alias}的{run["name"]}是什么难度', [name, run['name'], run['difficulty']])
        for i, lift in enumerate((data.get('lifts') or [])[:1]):
            add(stem, f'lift{i}', f'{alias}的{lift["name"]}有什么参数', [name, lift['name']])
    # 拒答题：名单里的雪场一旦被收录，这里会直接抛错——体检在评估之前先跑
    for name in ['二世谷', '南山滑雪场', '亚布力', '军都山滑雪场',
                 '可可托海', '吉克普林', '长白山', '帽儿山滑雪场']:
        if ResortRegistry.detect(name):
            raise ValueError(f'拒答题的雪场已收录：{name}')
        cases.append({'id': f'refusal:{name}', 'kind': 'refusal', 'question': f'{name}雪票多少钱'})
    return cases


def main():
    from utils.console import enable_utf8_console
    enable_utf8_console()
    cases = build_cases(load_archives())
    out = Path(settings.DATA_DIR) / 'eval' / 'generated_golden_set.yaml'
    out.write_text(yaml.safe_dump(cases, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'生成 {len(cases)} 题 → {out.name}（不覆盖原始题集）')


if __name__ == '__main__':
    main()
