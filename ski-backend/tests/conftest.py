"""pytest 公共夹具：路径准备、接口客户端、数据库可用性跳过。

跑法（在 ski-backend 目录下）：
    python -m pytest              # 全部
    python -m pytest tests/test_api_contract.py -q
"""
import sys
from pathlib import Path

import pytest

# 让 tests/ 也能像项目内其他模块一样 `from rag... import ...`
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope='session')
def client():
    """FastAPI 测试客户端。

    故意不用 `with TestClient(app)`：那会触发 lifespan 里的建表逻辑，
    接口测试不该顺手改数据库结构；起服务与建表由 init_db.py / main.py 负责。
    """
    from fastapi.testclient import TestClient

    from main import app

    return TestClient(app)


def _db_available() -> bool:
    try:
        from sqlalchemy import create_engine, text

        from config.config import settings
        if not settings.DATABASE_URL:
            return False
        engine = create_engine(settings.DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return True
    except Exception:                                        # noqa: BLE001
        return False


@pytest.fixture(scope='session')
def requires_db():
    """依赖 MySQL 的用例：连不上就跳过，而不是报一堆红（换机器的第一体验）。"""
    if not _db_available():
        pytest.skip('MySQL 不可用，跳过依赖数据库的接口测试')


@pytest.fixture
def test_registry(monkeypatch, request):
    """测试问题使用固定名称名册，避免单元测试依赖本机策展档案。"""
    from rag import parser

    names = {
        'wanlong': ('万龙', '万龙滑雪场'),
        'beidahu': ('北大湖', '北大湖滑雪场', '北大壶'),
        'taiwu': ('太舞', '太舞滑雪小镇'),
        'rusutsu': ('留寿都', '留寿都滑雪场'),
        'sichouzhilu': ('丝绸之路', '丝绸之路滑雪场'),
    }
    mapping = {name: key for key, aliases in names.items() for name in (key, *aliases)}
    monkeypatch.setattr(parser, '_name_map', mapping)
    monkeypatch.setattr(parser, '_uncovered_map', {
        '二世谷': '二世谷', '南山滑雪场': '南山滑雪场',
    })
    if request.node.get_closest_marker('local_data') is None:
        monkeypatch.setattr(parser.ResortRegistry, 'curated_tips', lambda term: [])
    return names


@pytest.fixture
def guidance_catalog(monkeypatch, test_registry):
    """交互状态单元测试仅需要名称与别名，不读取生成的图谱文件。"""
    from service import guidance_service

    nodes = [
        {'type': 'resort', 'resort_id': key, 'label': aliases[0],
         'attrs': {'aliases': list(aliases)}}
        for key, aliases in test_registry.items()
    ]
    monkeypatch.setattr(guidance_service, '_resorts', lambda: nodes)


def pytest_collection_modifyitems(items):
    """真实档案检查仅在所需文件存在时执行，公开版显示具体缺失路径。"""
    from config.config import settings

    for item in items:
        marker = item.get_closest_marker('local_data')
        if marker is None:
            continue
        missing = [name for name in marker.args
                   if not (Path(settings.DATA_DIR) / name).is_file()]
        if missing:
            item.add_marker(pytest.mark.skip(
                reason='需要未随公开快照提供的本地档案：' + ', '.join(missing)))
