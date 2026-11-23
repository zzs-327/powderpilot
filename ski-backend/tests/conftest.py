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
