"""仅使用临时 SQLite；不连接现有 MySQL，也不修改 .env。"""
import ast
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.util import CommandError

BACKEND = Path(__file__).resolve().parents[1]
INITIAL = "0001_initial"
HEAD = "0002_price_pricing_fields"
PRICING_COLUMNS = {"currency", "category", "unit_basis", "valid_from", "valid_to", "season",
                   "tax_status", "includes", "excludes", "conditions", "budget_usable", "source_url"}
TABLES = {"user", "user_profile", "resort", "run", "price", "photo"}


@pytest.fixture
def migration_config(tmp_path, monkeypatch):
    from config.config import settings

    url = "sqlite:///" + (tmp_path / "migration.sqlite").as_posix()
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "DEBUG", False)
    import models  # noqa: F401
    from config.database import Base

    config = Config(str(BACKEND / "alembic.ini"))
    return config, url, Base.metadata


def test_empty_upgrade_compare_and_roundtrip(migration_config):
    from migrations.check_schema import schema_differences

    config, url, metadata = migration_config
    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names()) == TABLES | {"alembic_version"}
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == HEAD
            assert schema_differences(connection, metadata) == []
            for table in TABLES:
                assert all(column["default"] is None for column in inspector.get_columns(table))
            assert inspector.get_indexes("resort")[0]["unique"]
            assert inspector.get_indexes("user_profile")[0]["unique"]
            for table in ("photo", "price", "run"):
                assert inspector.get_foreign_keys(table)[0]["options"]["ondelete"] == "CASCADE"
            assert inspector.get_foreign_keys("user_profile") == []
        command.upgrade(config, "head")  # 重复升级不重复建表
        command.downgrade(config, "base")  # 仅可丢弃的测试库
        with engine.connect() as connection:
            assert set(sa.inspect(connection).get_table_names()) == {"alembic_version"}
            assert connection.scalar(sa.text("SELECT count(*) FROM alembic_version")) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert schema_differences(connection, metadata) == []
    finally:
        engine.dispose()


def test_pricing_columns_are_additive_and_reversible(migration_config):
    config, url, _ = migration_config
    command.upgrade(config, INITIAL)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO resort (id, resort_key, name, region, data_status, create_time, update_time) "
                "VALUES (1, 'r', 'R', '', 'sample', '2026-01-01', '2026-01-01')")
            connection.exec_driver_sql(
                "INSERT INTO price (resort_id, item, price, unit, source, source_type, data_status) "
                "VALUES (1, '全天雪票', 560, '人/天', 's', 'A', 'verified')")
        command.upgrade(config, HEAD)
        with engine.connect() as connection:
            columns = {c["name"]: c for c in sa.inspect(connection).get_columns("price")}
            assert PRICING_COLUMNS <= set(columns)
            assert all(columns[name]["nullable"] and columns[name]["default"] is None
                       for name in PRICING_COLUMNS)
            row = connection.execute(sa.text("SELECT price, currency, budget_usable FROM price")).one()
            assert tuple(row) == (560, None, None)
        command.downgrade(config, INITIAL)
        with engine.connect() as connection:
            columns = {c["name"] for c in sa.inspect(connection).get_columns("price")}
            assert not PRICING_COLUMNS & columns
            assert connection.scalar(sa.text("SELECT price FROM price")) == 560
    finally:
        engine.dispose()


def test_comparison_detects_type_and_server_default(migration_config):
    from migrations.check_schema import schema_differences

    config, url, metadata = migration_config
    command.upgrade(config, "head")
    changed = sa.MetaData()
    for table in metadata.sorted_tables:
        table.to_metadata(changed)
    changed.tables["resort"].c.region.type = sa.String(12)
    changed.tables["resort"].c.region.server_default = sa.DefaultClause("test-default")
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            differences = schema_differences(connection, changed)
            kinds = {item[0] for diff in differences for item in (diff if isinstance(diff, list) else [diff])}
            assert "modify_type" in kinds
            assert "modify_default" in kinds
    finally:
        engine.dispose()


def test_initial_revision_is_frozen():
    source = (BACKEND / "migrations/versions/0001_initial.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    modules = {node.module if isinstance(node, ast.ImportFrom) else alias.name
               for node in imports for alias in node.names}
    assert modules <= {"alembic", "sqlalchemy"}
    assert "create_all" not in source
    assert "server_default" not in source


def test_mysql_offline_has_no_url_or_connection(migration_config, monkeypatch):
    from config.config import settings

    config, _, _ = migration_config
    secret = "offline-secret%25-never-print"
    url = sa.engine.URL.create(
        "mysql+pymysql", username="offline-user", password=secret,
        host="invalid.invalid", database="test_schema",
    ).render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    # ini URL 不应该成为连接配置入口。
    config.set_main_option("sqlalchemy.url", "ignored://wrong")
    output = io.StringIO()
    config.output_buffer = output
    def forbidden_connection(*args, **kwargs):
        pytest.fail("离线迁移不应创建 engine 或连接数据库")
    monkeypatch.setattr(sa, "create_engine", forbidden_connection)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE resort" in sql
    assert "AUTO_INCREMENT" in sql
    assert "ON DELETE CASCADE" in sql
    assert "0001_initial" in sql
    assert all(value not in sql for value in (secret, url, "offline-user", "invalid.invalid"))


def test_invalid_url_does_not_leak(migration_config, monkeypatch):
    from config.config import settings

    config, _, _ = migration_config
    secret = "bad-secret-never-print"
    monkeypatch.setattr(settings, "DATABASE_URL", secret)
    with pytest.raises(CommandError) as error:
        command.upgrade(config, "head", sql=True)
    assert secret not in str(error.value)


def test_cli_reads_settings_and_checker_is_read_only(tmp_path):
    url = "sqlite:///" + (tmp_path / "cli.sqlite").as_posix()
    env = {**os.environ, "DATABASE_URL": url, "DEBUG": "False", "PYTHONUTF8": "1"}
    def run(*args):
        return subprocess.run([sys.executable, "-m", *args], cwd=BACKEND, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
    result = run("alembic", "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert run("migrations.check_schema").returncode == 0
    assert run("alembic", "check").returncode == 0
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE unmanaged_test (id INTEGER)")
        result = run("migrations.check_schema")
        assert result.returncode == 1
        with engine.connect() as connection:
            assert "unmanaged_test" in sa.inspect(connection).get_table_names()
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == HEAD
    finally:
        engine.dispose()
