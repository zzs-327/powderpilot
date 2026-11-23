"""雪场档案 → 知识图谱导出（YAML → graph.json，幂等、可复现）。

用法（在 ski-backend 目录下）：
    python -m scripts.export_graph              # 全部雪场 → data/graph/graph.json
    python -m scripts.export_graph wanlong      # 只导出指定雪场（单雪场视图用）
    python -m scripts.export_graph --no-front   # 只写后端产物，不复制到前端 static

产出两份（默认都写，免得前端拿到旧图）：
    ski-backend/data/graph/graph.json        后台产物（真相源导出的图）
    ski-backend/ski-front/static/graph/graph.json   前端 H5 直接读的静态文件
    为什么前端要自己存一份：项目没有 npm/package.json，走不了 import echarts 那套；
    静态文件在 H5 开发与构建下都能读（见 ski-front/utils/graph.js 的加载器注释），
    也不必给 Nginx 加一条反代规则。

为什么要有这一步：
    对话给不了"可探索 + 可溯源"。本库的实体与关系是策展时**人工写进 YAML** 的，
    导成节点/边之后前端能画出来、点开看来源——这是"反照骗"落到界面上的样子。
    所以：不要用 LLM 再抽一遍实体（省事、但等于花钱买幻觉），也不需要图数据库
    （现在没有多跳查询，一份静态 JSON 就够）。

图的形状（刻意只做 4 种边）：
    resort --has_run--> run --suitable_for--> level
    resort --has_lift--> lift --serves--> level

有意不做的事（都不是遗漏，是取舍）：
    * board 节点（单板/双板）：目前只有教学视频卡带板型，接上它要多两种边，
      在单雪场视图里还会变成孤立节点飘着；等真做"板型/视频"视图时再加。
    * 难度没映射到三档的雪道（公园 / 地形公园 / 连接道）：**宁可少画一条边，也不猜**。
      它们会逐条记进 stats.unmapped_runs 并在控制台点名，不会被静默吞掉。
    * 缆车没写 serves 的（12 座里只有 2 座写了）：不建 serves 边，只记 stats.lifts_without_serves。
    * 档案里的同名条目（yunding 的"空中技巧"重复了两行）：**不让两个节点共用一个 id**。
      参数完全相同的合并成一条（记进 stats.duplicate_nodes），参数不同的加 #N 后缀区分。
    * 任何时间戳：同一份 YAML 必须导出**字节完全相同**的文件，否则每次导出都是一个
      "新版本"、diff 全是噪声。expire_at 由前端和当天比较——写死 expired 反而会随时间失真。

契约（graph.json，前端按这个读，不要再各写一套）：
    {
      "schema_version": 1,
      "stats": {计数 + 两份"没画出来的东西"的名单},
      "nodes": [{"id", "type", "label", "resort_id", "attrs", "evidence"}],
      "edges": [{"source", "target", "type", "resort_id", "evidence"}]
    }
    * id 形如 resort:wanlong / run:wanlong:金龙道 / lift:wanlong:1索 / level:advanced
    * level 是全局节点（跨雪场共用），它的 resort_id 为空串；
      单雪场视图按 resort_id 过滤节点与边即可（level 节点由边带出来）
    * evidence 复用现有证据信封字段（source / source_type / date / expire_at / data_status），
      另加两个 label 字段：中文措辞从 rag/parser.py 同一张表取，
      免得图谱和聊天里的证据块说成两套话
    * 边的 evidence 取"数据那一端"节点的出处（has_run/suitable_for 用雪道的，
      has_lift/serves 用缆车的）：边不是独立事实，是某条数据的派生关系
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml

from agent.evidence import SOURCE_TYPE_LABEL
from config.config import BASE_DIR, settings
from rag.parser import (DATA_STATUS_LABEL, DIFFICULTY_TO_LEVEL, LEVEL_LABEL,
                        ResortParser, ResortProvenance, list_resort_ids,
                        weakest_status)

SCHEMA_VERSION = 1

# 三档水平的中文名（契约里 level:advanced 的 label 是"高级（老手）"）。
# 只做显示用：YAML 里五花八门的难度叫法(中级道/蓝道/中高级道…)到这三档的映射
# 一律走 rag/parser.py 的 DIFFICULTY_TO_LEVEL，本文件不另立一套。
LEVEL_CN = {'beginner': '初级', 'intermediate': '中级', 'advanced': '高级'}
LEVEL_ORDER = ('beginner', 'intermediate', 'advanced')

# 缆车 serves 文本里出现这些词，按"新手"算（如"单板新手练习区"）
_BEGINNER_HINTS = ('新手', '教学', '练习')

# 节点 attrs 的字段白名单：源里写了的才搬，留空的不搬（前端就不会显示空行）
RESORT_ATTR_KEYS = ('address', 'phone', 'season', 'website', 'runs_total',
                    'vertical_drop_m', 'price_updated_at')
RUN_ATTR_KEYS = ('difficulty', 'length_m', 'top_altitude_m', 'avg_slope_deg',
                 'max_slope_deg', 'vertical_m', 'note')
LIFT_ATTR_KEYS = ('category', 'spec', 'length_m', 'capacity_per_hour', 'serves', 'note')


def _scalar_attrs(raw: dict, keys) -> dict:
    """按白名单搬运**源里真写了**的字段；空值不搬（留空本身就是一种诚实）"""
    return {key: raw[key] for key in keys
            if raw.get(key) not in (None, '', [], {})}


def _evidence(source_type: str, source: str, date: str, doc_type: str,
              data_status: str) -> dict:
    """构造节点/边的证据信封

    expire_at 直接复用 ResortParser._expire_at：保鲜期规则（价格 90 天 / 雪道 180 天…）
    只能有一处实现，导出脚本另算一遍就会和检索侧对不上。
    """
    return {
        'source': source,
        'source_type': source_type,
        'source_type_label': SOURCE_TYPE_LABEL.get(source_type, source_type),
        'date': date,
        'expire_at': ResortParser._expire_at(date, doc_type),
        'data_status': data_status,
        'status_label': DATA_STATUS_LABEL.get(data_status, ''),
    }


def _resolve_node_id(base_id: str, attrs: dict, index: dict[str, dict]) -> tuple[str, str]:
    """同名雪道/缆车的消歧：**不能让两个节点共用一个 id**（前端会拿到重复节点）

    YAML 里真有同名条目（yunding 的"空中技巧"就是重复了两行），所以：
      * 参数也完全相同 → 判为重复行，返回 ('', 'duplicate')，调用方跳过并记进 stats
      * 参数不同       → 加 #N 后缀区分，返回 (新 id, 'renamed')
    id 不重复，同时也不会因为"看着像重复"就把数据悄悄丢掉。
    """
    if base_id not in index:
        index[base_id] = attrs
        return base_id, ''
    if index[base_id] == attrs:
        return '', 'duplicate'
    suffix = 2
    while f'{base_id}#{suffix}' in index:
        suffix += 1
    new_id = f'{base_id}#{suffix}'
    index[new_id] = attrs
    return new_id, 'renamed'


def _levels_from_serves(text: str) -> list[str]:
    """从缆车 serves 的自由文本里认出它服务哪几档水平

    serves 是人写的自由文本（"高中初级道" / "初中级道与新手教学区" / "单板新手练习区"），
    所以只按字面特征取，不做语义猜测：含"初"→新手，含"中"→进阶，含"高"→老手，
    出现"新手/教学/练习"也算新手。认不出来就返回空列表（**不建边**）——
    宁可少画一条边，也不替雪场改口。
    """
    levels: list[str] = []
    if '初' in text or any(hint in text for hint in _BEGINNER_HINTS):
        levels.append('beginner')
    if '中' in text:
        levels.append('intermediate')
    if '高' in text:
        levels.append('advanced')
    return levels


def build_graph(resort_ids: list[str] | None = None) -> dict:
    """YAML 档案 → 图谱 dict（纯函数：不写文件，测试可直接调）"""
    ids = list(resort_ids or list_resort_ids())
    nodes: list[dict] = []
    edges: list[dict] = []
    nodes_by_type: Counter = Counter()
    edges_by_type: Counter = Counter()
    unmapped_runs: list[dict] = []
    lifts_without_serves: list[dict] = []
    duplicate_nodes: list[dict] = []
    run_index: dict[str, dict] = {}
    lift_index: dict[str, dict] = {}

    for resort_id in ids:
        path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
        if not path.exists():
            raise FileNotFoundError(f'雪场档案不存在：{path}')
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        name = str(data.get('name') or resort_id)
        prov = ResortProvenance.resolve(data)
        official = data.get('official') or {}

        def src(doc_type: str, item: dict | None = None) -> tuple[str, str, str]:
            return ResortProvenance.source_of(prov, doc_type, name, item)

        # --- 雪场节点 ---
        # attrs 里同时给出"逐类型生效档位"：一座雪场里常有"概览=官方直采、雪道=二手转引"
        # （万龙就是这个形状），一个档位说不清；再按项目既定规则附上"最弱档"，
        # 因为凡是要用一个值代表整座雪场时，一律取最保守的那档。
        overview_type, overview_src, overview_date = src('overview')
        resort_attrs = {
            'region': str(data.get('region') or ''),
            'aliases': [str(alias) for alias in data.get('aliases') or []],
            'data_status': prov['data_status'],
            'weakest_status': weakest_status(prov['status_map'].values()),
            'status_map': dict(prov['status_map']),
            'runs_recorded': len(data.get('runs') or []),
            'lifts_recorded': len(data.get('lifts') or []),
        }
        resort_attrs.update(_scalar_attrs(official, RESORT_ATTR_KEYS))
        resort_node_id = f'resort:{resort_id}'
        nodes.append({
            'id': resort_node_id,
            'type': 'resort',
            'label': name,
            'resort_id': resort_id,
            'attrs': resort_attrs,
            'evidence': _evidence(overview_type, overview_src, overview_date,
                                  'overview', ResortProvenance.status_of(prov, 'overview')),
        })
        nodes_by_type['resort'] += 1

        def add_edge(source: str, target: str, edge_type: str, evidence: dict) -> None:
            edges.append({'source': source, 'target': target, 'type': edge_type,
                          'resort_id': resort_id, 'evidence': evidence})
            edges_by_type[edge_type] += 1

        # --- 雪道节点 + has_run / suitable_for 边 ---
        run_status = ResortProvenance.status_of(prov, 'run')
        for run in data.get('runs') or []:
            run_name = str(run.get('name') or '未命名')
            run_type, run_source, run_date = src('run', run)
            run_evidence = _evidence(run_type, run_source, run_date, 'run', run_status)
            run_attrs = _scalar_attrs(run, RUN_ATTR_KEYS)
            run_node_id, how = _resolve_node_id(f'run:{resort_id}:{run_name}',
                                                run_attrs, run_index)
            if not run_node_id:
                duplicate_nodes.append({'resort_id': resort_id, 'name': run_name,
                                        'type': 'run', 'reason': '档案里重复了两行，已合并'})
                continue
            if how == 'renamed':
                duplicate_nodes.append({'resort_id': resort_id, 'name': run_name,
                                        'type': 'run', 'reason': f'同名但参数不同，id 记为 {run_node_id}'})
            nodes.append({
                'id': run_node_id,
                'type': 'run',
                'label': run_name,
                'resort_id': resort_id,
                'attrs': run_attrs,
                'evidence': run_evidence,
            })
            nodes_by_type['run'] += 1
            add_edge(resort_node_id, run_node_id, 'has_run', run_evidence)

            difficulty = str(run.get('difficulty') or '')
            level = DIFFICULTY_TO_LEVEL.get(difficulty, 'all')
            if level in LEVEL_ORDER:
                add_edge(run_node_id, f'level:{level}', 'suitable_for', run_evidence)
            else:
                # 连接道映射到 all（parser 的既定语义：联络道不参与分层），
                # 公园/地形公园则根本没在难度表里——两种都不猜，只记录
                unmapped_runs.append({'resort_id': resort_id, 'run': run_name,
                                      'difficulty': difficulty})

        # --- 缆车节点 + has_lift / serves 边 ---
        lift_status = ResortProvenance.status_of(prov, 'lift')
        for lift in data.get('lifts') or []:
            lift_name = str(lift.get('name') or '未命名')
            lift_type, lift_source, lift_date = src('lift', lift)
            lift_evidence = _evidence(lift_type, lift_source, lift_date, 'lift', lift_status)
            lift_attrs = _scalar_attrs(lift, LIFT_ATTR_KEYS)
            lift_node_id, how = _resolve_node_id(f'lift:{resort_id}:{lift_name}',
                                                 lift_attrs, lift_index)
            if not lift_node_id:
                duplicate_nodes.append({'resort_id': resort_id, 'name': lift_name,
                                        'type': 'lift', 'reason': '档案里重复了两行，已合并'})
                continue
            if how == 'renamed':
                duplicate_nodes.append({'resort_id': resort_id, 'name': lift_name,
                                        'type': 'lift', 'reason': f'同名但参数不同，id 记为 {lift_node_id}'})
            nodes.append({
                'id': lift_node_id,
                'type': 'lift',
                'label': lift_name,
                'resort_id': resort_id,
                'attrs': lift_attrs,
                'evidence': lift_evidence,
            })
            nodes_by_type['lift'] += 1
            add_edge(resort_node_id, lift_node_id, 'has_lift', lift_evidence)

            levels = _levels_from_serves(str(lift.get('serves') or ''))
            if levels:
                for level in levels:
                    add_edge(lift_node_id, f'level:{level}', 'serves', lift_evidence)
            else:
                lifts_without_serves.append({'resort_id': resort_id, 'lift': lift_name})

    # --- 水平节点（全局共用，放在最后，三档固定顺序） ---
    level_evidence = _evidence(
        'C', '本库分层口径（rag/parser.py 的 DIFFICULTY_TO_LEVEL）', '', '', '')
    for level in LEVEL_ORDER:
        nodes.append({
            'id': f'level:{level}',
            'type': 'level',
            'label': f'{LEVEL_CN[level]}（{LEVEL_LABEL[level]}）',
            'resort_id': '',
            'attrs': {'level': level},
            'evidence': level_evidence,
        })
        nodes_by_type['level'] += 1

    return {
        'schema_version': SCHEMA_VERSION,
        'stats': {
            'resorts': ids,
            'resort_count': len(ids),
            'node_count': len(nodes),
            'edge_count': len(edges),
            'nodes_by_type': dict(nodes_by_type.most_common()),
            'edges_by_type': dict(edges_by_type.most_common()),
            # 三份"没画出来的东西"的名单：不建边/不建节点，但要能被看见
            'unmapped_runs': unmapped_runs,
            'lifts_without_serves': lifts_without_serves,
            'duplicate_nodes': duplicate_nodes,
        },
        'nodes': nodes,
        'edges': edges,
    }


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()

    parser = argparse.ArgumentParser(
        description='把 data/resorts/*.yaml 导成知识图谱 graph.json（供前端关系图使用）')
    parser.add_argument('resort_ids', nargs='*', help='雪场 id，留空表示全部')
    parser.add_argument('--out', default=str(Path(settings.DATA_DIR) / 'graph' / 'graph.json'),
                        help='输出路径（默认 data/graph/graph.json）')
    parser.add_argument('--no-front', action='store_true',
                        help='不把同一份内容复制到 ski-front/static/graph/（默认会复制）')
    args = parser.parse_args()

    graph = build_graph(args.resort_ids or None)
    stats = graph['stats']
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(graph, ensure_ascii=False, indent=2) + '\n'
    out.write_text(text, encoding='utf-8')

    # 前端那份必须和这份**逐字节相同**：两份图不一样时，页面上的节点数量和
    # 聊天里的回答对不上，而那种不一致最难查（所以宁可在这里多写一次）
    front_dir = BASE_DIR / 'ski-front'
    front_out = front_dir / 'static' / 'graph' / 'graph.json'
    if not args.no_front and front_dir.is_dir():
        front_out.parent.mkdir(parents=True, exist_ok=True)
        front_out.write_text(text, encoding='utf-8')
        print(f'前端静态文件 → {front_out}（与上面这份内容一致）')

    # 逐雪场打印：节点/边数量对不上档案时，第一眼就能看出来
    by_resort: Counter = Counter(node['resort_id'] for node in graph['nodes'] if node['resort_id'])
    edges_by_resort: Counter = Counter(edge['resort_id'] for edge in graph['edges'])
    print(f'导出 {stats["resort_count"]} 座雪场 → {out}')
    for resort_id in stats['resorts']:
        print(f'  {resort_id}：节点 {by_resort[resort_id]}（含雪道/缆车）、'
              f'边 {edges_by_resort[resort_id]}')
    print(f'图规模：节点 {stats["node_count"]}（'
          + '、'.join(f'{key} {value}' for key, value in stats['nodes_by_type'].items())
          + f'），边 {stats["edge_count"]}（'
          + '、'.join(f'{key} {value}' for key, value in stats['edges_by_type'].items()) + '）')

    if stats['unmapped_runs']:
        print(f'⚠️  {len(stats["unmapped_runs"])} 条雪道的难度没映射到"初级/中级/高级"三档，'
              '按"不猜"原则不建 suitable_for 边：')
        for item in stats['unmapped_runs']:
            print(f'    {item["resort_id"]} / {item["run"]}：难度「{item["difficulty"] or "未填"}」')
    if stats['lifts_without_serves']:
        print(f'⚠️  {len(stats["lifts_without_serves"])} 条缆车没写 serves，不建 serves 边：')
        for item in stats['lifts_without_serves']:
            print(f'    {item["resort_id"]} / {item["lift"]}')
    if stats['duplicate_nodes']:
        print(f'⚠️  {len(stats["duplicate_nodes"])} 处同名条目需要处理（id 必须唯一）：')
        for item in stats['duplicate_nodes']:
            print(f'    {item["resort_id"]} / {item["type"]} {item["name"]}：{item["reason"]}')

    print(f'文件 {out.stat().st_size} 字节；同一份 YAML 导出的内容逐字节一致（可直接 diff）')
    print('单雪场视图：按节点的 resort_id 过滤（level 是全局节点，由边带出来）')


if __name__ == '__main__':
    main()
