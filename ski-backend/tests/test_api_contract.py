"""接口契约测试：统一响应结构、鉴权、雪场接口。

为什么值得单独测这几条（都是真踩过坑的）：
    1. 前端按 `code / msg / data` 判断，不是按 HTTP 状态码——异常路径也必须是这个结构，
       否则前端只能看到"网络错误"（main.py 的 _add_cors_headers 就是为它加的）。
    2. 未收录雪场是**业务错误**（code 500 + "未收录雪场"），不是 404：
       前端要能区分"这个雪场我们没有"和"接口挂了"。
    3. `/user/info` 没带 token 必须失败，且失败体也是统一结构（不能漏出 {'detail': ...}）。
"""
import pytest


def test_health_ok(client):
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.json() == {'code': 200, 'msg': 'PowderPilot API is running', 'data': None}


def test_unknown_route_returns_unified_result(client):
    """404 也要走统一结构：前端只认 code/msg/data。"""
    resp = client.get('/api/v1/definitely-not-a-route')
    assert resp.status_code == 404
    body = resp.json()
    assert 'detail' not in body
    assert set(body) == {'code', 'msg', 'data'}
    assert body['code'] == 404


def test_user_info_without_token_is_rejected(client):
    """没带 token：HTTP 状态码保留语义，响应体仍是统一结构。"""
    resp = client.get('/api/v1/user/info')
    assert resp.status_code in (401, 403), resp.text
    body = resp.json()
    assert 'detail' not in body
    assert body['code'] == resp.status_code


def test_login_with_wrong_password_is_business_error(client, requires_db):
    """密码错 = 业务错误（code != 200、HTTP 200），前端弹提示而不是"网络错误"。"""
    resp = client.post('/api/v1/user/login',
                       json={'username': 'ski_demo', 'password': 'definitely-wrong'})
    body = resp.json()
    assert body['code'] != 200
    assert body['msg']


def test_resort_list_carries_provenance(client, requires_db):
    """雪场列表必须带可信度档位——这是本项目对外最该露的信息。"""
    resp = client.get('/api/v1/resort')
    assert resp.status_code == 200
    body = resp.json()
    assert body['code'] == 200
    resorts = body['data']
    assert resorts, '雪场列表为空：先跑 python -m scripts.sync_to_mysql'
    keys = {item['resort_key'] for item in resorts}
    assert 'wanlong' in keys
    assert all(item.get('data_status') for item in resorts)


def test_resort_tips_quote_known_gaps(client, requires_db):
    resp = client.get('/api/v1/resort/rusutsu/tips')
    body = resp.json()
    assert body['code'] == 200
    texts = '\n'.join(item['text'] for item in body['data'])
    assert '双板' in texts and '房价' in texts
    assert all(item.get('source') for item in body['data'])


def test_resort_runs_stats(client, requires_db):
    resp = client.get('/api/v1/resort/wanlong/runs')
    body = resp.json()
    assert body['code'] == 200
    assert body['data']


@pytest.mark.parametrize('path', ['runs', 'prices', 'tips'])
def test_uncovered_resort_is_business_error(client, requires_db, path):
    """未收录雪场：业务错误 + 话术里点名"未收录"，不能被当成 404 或空数据糊过去。"""
    resp = client.get(f'/api/v1/resort/never-heard-of-it/{path}')
    assert resp.status_code == 200
    body = resp.json()
    assert body['code'] != 200
    assert '未收录' in body['msg']
