"""部署状态快照：MySQL 计数 / 向量库条数 / 会话记忆行数

用途：重启（stop/start、down/up、整机重启）**前后**各跑一次，对比数字有没有丢。
只读，不改任何数据。在 backend 容器里跑：

    docker compose --env-file .env.docker exec -T backend python - < deploy/ops_snapshot.py
"""
import os
import sqlite3

from sqlalchemy import text

from config.config import settings
from config.database import SessionLocal


def mysql_counts():
    session = SessionLocal()
    try:
        for table in ('resort', 'run', 'price', 'user', 'user_profile'):
            n = session.execute(text(f'select count(*) from {table}')).scalar()
            print(f'mysql {table} = {n}')
    finally:
        session.close()


def chroma_count():
    import chromadb

    client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
    collection = client.get_collection(settings.CHROMA_COLLECTION)
    print(f'chroma {settings.CHROMA_COLLECTION} = {collection.count()}')


def checkpoint_counts():
    path = settings.CHECKPOINT_DB
    print(f'checkpoints db = {path} exists = {os.path.exists(path)}')
    if not os.path.exists(path):
        return
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]
        print(f'checkpoints 表 = {tables}')
        for table in tables:
            n = con.execute(f'select count(*) from {table}').fetchone()[0]
            print(f'  {table} = {n}')
    finally:
        con.close()


if __name__ == '__main__':
    mysql_counts()
    try:
        chroma_count()
    except Exception as exc:
        print(f'chroma 读取失败: {type(exc).__name__}: {exc}')
    checkpoint_counts()
