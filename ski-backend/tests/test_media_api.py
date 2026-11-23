from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from api import resort
from config.database import get_db
from config.dependencies import get_current_user
from config.exceptions import ServiceException
from service import media_service


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(resort.router, prefix='/api/v1')

    @app.exception_handler(ServiceException)
    def business_error(request, exc):
        return JSONResponse({'code': 500, 'msg': str(exc), 'data': None})

    @app.exception_handler(HTTPException)
    def http_error(request, exc):
        return JSONResponse({'code': exc.status_code, 'msg': str(exc.detail), 'data': None},
                            status_code=exc.status_code)

    class FakeResortService:
        def resolve(self, key):
            return {'resort_key': 'test'} if key in ('test', '测试雪场') else None

    def no_database():
        yield None

    app.dependency_overrides[get_db] = no_database
    app.dependency_overrides[resort.get_resort_service] = FakeResortService
    monkeypatch.setattr(media_service, 'RESORT_DIR', tmp_path)
    with TestClient(app) as test_client:
        yield test_client, app, tmp_path


def test_media_requires_authentication_without_database(client):
    test_client, app, _ = client
    response = test_client.get('/api/v1/resort/test/media')
    assert response.status_code in (401, 403)
    assert set(response.json()) == {'code', 'msg', 'data'}


def test_media_full_catalog_empty_and_unknown_key(client):
    import yaml
    test_client, app, directory = client
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    empty = test_client.get('/api/v1/resort/test/media').json()
    assert empty == {'code': 200, 'msg': 'success', 'data': []}
    items = [dict(title=f'媒体{i}', kind='photo', url=f'https://example.com/page/{i}',
                  source='官网', checked_at='2026-10-07', verification='content_checked',
                  rights='authorized', notice='非实时', category='terrain',
                  image_url=f'https://example.com/images/{i}.jpg', credit='已授权摄影师')
             for i in range(5)]
    (directory / 'test.yaml').write_text(yaml.safe_dump({'media': items}), encoding='utf-8')
    response = test_client.get('/api/v1/resort/测试雪场/media')
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {'code', 'msg', 'data'}
    assert payload['code'] == 200
    assert len(payload['data']) == 5
    assert payload['data'][0]['resort_id'] == 'test'
    assert payload['data'][0]['category'] == 'terrain'
    assert payload['data'][0]['image_url'] == 'https://example.com/images/0.jpg'
    unknown = test_client.get('/api/v1/resort/unknown/media').json()
    assert unknown == {'code': 500, 'msg': '未收录雪场：unknown', 'data': None}
