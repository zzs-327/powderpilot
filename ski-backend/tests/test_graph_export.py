"""graph.json 契约守卫：把"前端能不能画出来"变成可复现的断言。

为什么要单独盯着这件事：
    图谱是产品差异点，但它的数据是**导出**出来的——导出脚本一改、YAML 一加雪场，
    前端就可能拿到重复 id、悬空边或没有 evidence 的节点，而"点开看来源"正是
    这个页面存在的理由。所以契约（id 唯一 / 边两端存在 / 每节点带证据）必须被断言守住，
    而不是等 HBuilderX 里肉眼发现节点重叠。

跑法（在 ski-backend 目录下，不需要 MySQL、不需要 API key）：
    python -m pytest tests/test_graph_export.py -q
"""
import json
from pathlib import Path

import pytest
import yaml

from config.config import settings
from scripts.export_graph import build_graph
from rag.parser import list_resort_ids

EVIDENCE_FIELDS = {'source', 'source_type', 'date', 'expire_at', 'data_status'}
EDGE_TYPES = {'has_run', 'has_lift', 'suitable_for', 'serves'}


@pytest.fixture(scope='module')
def graph() -> dict:
    """全量图谱（纯解析 YAML，不碰数据库）"""
    return build_graph()


def _wanlong(data: dict) -> dict:
    """单雪场视图：按 resort_id 取节点，再由边走出的 level 节点补齐"""
    nodes = [node for node in data['nodes'] if node['resort_id'] == 'wanlong']
    edges = [edge for edge in data['edges'] if edge['resort_id'] == 'wanlong']
    by_id = {node['id']: node for node in data['nodes']}
    touched = {eid for edge in edges for eid in (edge['source'], edge['target'])}
    return {'nodes': nodes, 'edges': edges, 'by_id': by_id, 'touched': touched}


def test_node_ids_are_unique(graph):
    ids = [node['id'] for node in graph['nodes']]
    duplicated = sorted({nid for nid in ids if ids.count(nid) > 1})
    assert not duplicated, f'节点 id 重复，前端会拿到重叠节点：{duplicated}'


def test_edges_reference_existing_nodes(graph):
    ids = {node['id'] for node in graph['nodes']}
    dangling = [edge for edge in graph['edges']
                if edge['source'] not in ids or edge['target'] not in ids]
    assert not dangling, f'悬空边（两端必须都在 nodes 里）：{dangling[:3]}'


def test_every_node_carries_evidence(graph):
    """每个节点都要能回答"这条数据来自哪、几手"——这是本页面的立身之本。"""
    missing = [node['id'] for node in graph['nodes']
               if not EVIDENCE_FIELDS <= set(node['evidence'])
               or not node['evidence']['source']]
    assert not missing, f'这些节点没有可展示的证据：{missing[:5]}'


def test_edge_types_stay_within_contract(graph):
    """边类型先只做 4 种：类型一多，单雪场视图马上变成毛线球。"""
    assert {edge['type'] for edge in graph['edges']} <= EDGE_TYPES


def test_level_nodes_are_global(graph):
    levels = [node for node in graph['nodes'] if node['type'] == 'level']
    assert levels, 'level 节点必须有：雪道/缆车靠它分层'
    assert all(node['resort_id'] == '' for node in levels), 'level 是跨雪场共用的全局节点'


@pytest.mark.local_data('resorts/wanlong.yaml')
def test_single_resort_view_covers_every_run_in_the_yaml(graph):
    """单雪场视图必须覆盖档案里的每一条雪道：导出漏数据是最难发现的那种 bug。"""
    data = yaml.safe_load((Path(settings.RESORT_DIR) / 'wanlong.yaml').read_text(encoding='utf-8'))
    view = _wanlong(graph)
    labels = {node['label'] for node in view['nodes'] if node['type'] == 'run'}
    for run in data['runs']:
        assert run['name'] in labels, f'档案里有雪道「{run["name"]}」，图里没有'


@pytest.mark.local_data('resorts/wanlong.yaml')
def test_single_resort_view_edges_and_levels(graph):
    view = _wanlong(graph)
    types = {edge['type'] for edge in view['edges']}
    assert types == EDGE_TYPES, f'万龙应当四种边齐全（含缆车 serves），实际 {types}'
    # level 节点不带 resort_id，必须由边带进单雪场视图
    level_keys = {view['by_id'][eid]['attrs']['level'] for eid in view['touched']
                  if eid.startswith('level:')}
    assert level_keys == {'beginner', 'intermediate', 'advanced'}


def test_export_is_reproducible(graph):
    """幂等：同一份 YAML 导出的内容必须一致，否则每次导出都是一次"新版本"。"""
    again = json.dumps(build_graph(), ensure_ascii=False, sort_keys=True)
    assert again == json.dumps(graph, ensure_ascii=False, sort_keys=True)


def test_stats_match_the_lists(graph):
    stats = graph['stats']
    assert stats['node_count'] == len(graph['nodes'])
    assert stats['edge_count'] == len(graph['edges'])
    assert stats['resort_count'] == len(stats['resorts']) == len(list_resort_ids())
