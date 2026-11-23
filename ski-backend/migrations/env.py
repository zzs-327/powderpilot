"""迁移连接只走 Settings，不使用 ini URL 或应用的 DEBUG SQL 日志。"""
from alembic import context
from alembic.util import CommandError
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url


def run_migrations():
    from config.config import settings

    if not settings.DATABASE_URL:
        raise CommandError("请通过 Settings 配置 DATABASE_URL（不在日志中输出连接配置）。")
    url = make_url(settings.DATABASE_URL)
    import models  # noqa: F401
    from config.database import Base

    options = dict(target_metadata=Base.metadata, compare_type=True,
                   compare_server_default=True)
    if context.is_offline_mode():
        # 只传方言，不把包含密码的 URL 写入配置或输出。
        context.configure(dialect_name=url.get_backend_name(), literal_binds=True,
                          dialect_opts={"paramstyle": "named"}, **options)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = create_engine(url, poolclass=pool.NullPool, echo=False, hide_parameters=True)
        try:
            with engine.connect() as connection:
                context.configure(connection=connection, **options)
                with context.begin_transaction():
                    context.run_migrations()
        finally:
            engine.dispose()


try:
    run_migrations()
except CommandError:
    raise
except Exception:
    # 驱动异常可能包含用户名、主机或原始 URL；CLI 只报告安全摘要。
    raise CommandError("迁移失败；请私下检查连接配置、权限和表结构，禁止公开原始异常。") from None
