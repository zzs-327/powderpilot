"""只读检查数据库与当前模型；不写版本表，不执行 stamp/DDL。"""
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, pool


def schema_differences(connection, metadata):
    context = MigrationContext.configure(connection, opts={
        "compare_type": True, "compare_server_default": True,
    })
    return compare_metadata(context, metadata)


def main():
    engine = None
    try:
        from config.config import settings
        import models  # noqa: F401
        from config.database import Base

        engine = create_engine(settings.DATABASE_URL, poolclass=pool.NullPool,
                               echo=False, hide_parameters=True)
        with engine.connect() as connection:
            differences = schema_differences(connection, Base.metadata)
        if differences:
            # 不打印 diff 的 repr：服务器默认表达式也可能包含秘密。
            print(f"结构不一致：发现 {len(differences)} 组差异；禁止 stamp。请在受控环境审查。")
            return 1
        print("当前模型结构检查通过（类型/服务器默认已检查）；仍需备份与人工基线核对才能 stamp。")
        return 0
    except Exception:
        print("结构检查失败；请私下检查配置、权限和连接，不公开原始异常。", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    from utils.console import enable_utf8_console
    enable_utf8_console()
    sys.exit(main())
