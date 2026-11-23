from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,   # DEBUG=True 时打印 SQL，方便看 DAO 到底发了什么
)
# session 工厂对象
SessionLocal = sessionmaker(bind=engine)


def get_db():
    """FastAPI 依赖注入用：每个请求一个 session，异常回滚。"""
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def create_all() -> None:
    """建表（开发期用；正式项目应换 Alembic 迁移）。

    必须先 import models：Base.metadata 只有见过模型类，才知道要建哪些表。
    """
    import models  # noqa: F401

    Base.metadata.create_all(engine)
