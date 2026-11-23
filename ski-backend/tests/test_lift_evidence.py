"""索道服务级别只能来自档案证据，未知关系不得按布局补边。"""
from scripts.export_graph import build_graph


def test_duolemeidi_levels_preserve_transcribed_evidence():
    graph = build_graph()
    expected = {
        '1号缆车': {'intermediate', 'advanced'},
        '2号缆车': {'intermediate'},
        '牵拖': {'beginner'},
        '魔毯': {'beginner'},
    }
    nodes = {node['id']: node for node in graph['nodes']}
    for name, levels in expected.items():
        node_id = f'lift:duolemeidi:{name}'
        edges = [edge for edge in graph['edges']
                 if edge['source'] == node_id and edge['type'] == 'serves']
        assert {edge['target'].removeprefix('level:') for edge in edges} == levels
        assert nodes[node_id]['attrs']['note'].startswith('通往：')
        assert nodes[node_id]['evidence']['data_status'] == 'transcribed'
        assert all(edge['evidence']['data_status'] == 'transcribed' for edge in edges)


def test_unknown_lift_services_stay_unknown():
    graph = build_graph()
    for resort_id in ('beidahu', 'yunding'):
        assert not any(edge['type'] == 'serves' and edge['resort_id'] == resort_id
                       for edge in graph['edges'])
    assert not any(node['type'] == 'lift' and node['resort_id'] == 'taiwu'
                   for node in graph['nodes'])
