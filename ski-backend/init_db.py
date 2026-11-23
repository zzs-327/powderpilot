"""数据库初始化：建表 + 种子账号。

用法（在 ski-backend 目录下）：
    python init_db.py

做什么：
    1. 建表（已存在则不重建，只补缺失的列）
    2. 仅当设置了 DEMO_PASSWORD 时创建演示账号（默认用户名 ski_demo，口令不进仓库）
    3. 提示下一步：同步雪场档案（python -m scripts.sync_to_mysql）
"""
import os

from sqlalchemy import inspect, select, text

from config.database import Base, SessionLocal, engine
from dao.user_dao import UserDao
from models.profile import UserProfile  # noqa: F401 确保建表
from models.resort import Resort  # noqa: F401 确保建表
from models.user import User
from utils.pwdutils import PwdUtil

# 演示账号：仓库不内置口令。需要本地演示时再设置环境变量。
DEMO_USERNAME = os.environ.get('DEMO_USERNAME', 'ski_demo')
DEMO_PASSWORD = os.environ.get('DEMO_PASSWORD')

# 已存在表需要补的列：{表名: {列名: DDL}}
DEFENSIVE_COLUMNS = {
    'resort': {
        'runs_beginner': 'INT NULL',
        'runs_intermediate': 'INT NULL',
        'runs_advanced': 'INT NULL',
        # 官网口径的雪道总数（与 runs_* 的第三方口径可能不一致，两个都要留）
        'official_runs_total': 'INT NULL COMMENT "官网口径的雪道总数"',
        # 采集方式说明：引用来源、检索日期、为什么够不到 verified
        'data_status_note': 'TEXT NULL',
    },
    'run': {
        # 官方雪道参数表的常见口径，比落差更能回答"陡不陡"
        'top_altitude_m': 'INT NULL COMMENT "最高海拔（米）"',
        'avg_slope_deg': 'FLOAT NULL COMMENT "平均坡度（度）"',
        'max_slope_deg': 'FLOAT NULL COMMENT "最大坡度（度）"',
        # 行级可信度：雪道可能有多源印证，而同一座雪场的价格只有单一转引
        'data_status': 'VARCHAR(16) NULL COMMENT "该行数据的可信度档位"',
    },
    'price': {
        'data_status': 'VARCHAR(16) NULL COMMENT "该行数据的可信度档位"',
    },
}

# 防御性：把已存在但**太窄**的列加宽（create_all 不会改列类型）
#
# 为什么需要：price.unit 原来是 String(32)。录入"分时段优惠价"时，
# 单位里要写清时段口径（如"人/天（季末 2026-03-23~04-06 平日时段）"），
# 超出 32 字符 → MySQL 直接 `DataError: Data too long` → **那 12 条价格静默丢失**，
# 而同步脚本报的还是 YAML 条数（已一并修成报真实入库条数）。
DEFENSIVE_WIDENINGS = {
    'price': {'unit': 'VARCHAR(64) NOT NULL DEFAULT ""'},
}


def create_tables():
    """建表（已存在则不重建，只补缺失的列）"""
    Base.metadata.create_all(bind=engine)

    # 防御性：给已存在的表补充后加的列（create_all 不会改已存在的表）
    inspector = inspect(engine)
    for table_name, columns in DEFENSIVE_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        existing = [col['name'] for col in inspector.get_columns(table_name)]
        for column, ddl in columns.items():
            if column not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {column} {ddl}'))
                print(f'已为 {table_name} 表补充 {column} 列')

    # 防御性：把太窄的列加宽（见 DEFENSIVE_WIDENINGS 的说明）
    for table_name, columns in DEFENSIVE_WIDENINGS.items():
        if not inspector.has_table(table_name):
            continue
        info = {col['name']: col for col in inspector.get_columns(table_name)}
        for column, ddl in columns.items():
            current = info.get(column)
            if current is None:
                continue
            length = getattr(current.get('type'), 'length', None)
            wanted = 64
            if length is not None and length < wanted:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table_name} MODIFY COLUMN {column} {ddl}'))
                print(f'已把 {table_name}.{column} 从 {length} 加宽到 {wanted}')


def seed_demo_user():
    """按环境变量创建演示账号；未设置 DEMO_PASSWORD 则跳过。"""
    if not DEMO_PASSWORD:
        print('未设置 DEMO_PASSWORD，已跳过演示账号。如需本地登录请自行设置 DEMO_USERNAME / DEMO_PASSWORD。')
        return
    with SessionLocal() as session:
        user = UserDao(session).select_by_name(DEMO_USERNAME)
        if user is not None:
            print(f'演示账号已存在：{DEMO_USERNAME}')
            return
        session.add(User(username=DEMO_USERNAME,
                         password=PwdUtil.hash_password(DEMO_PASSWORD)))
        session.commit()
        print(f'已创建演示账号：{DEMO_USERNAME}（口令来自环境变量，不会打印）')


def seed_demo_profile():
    """给演示账号建一条空画像（Agent 会渐进式补全）"""
    with SessionLocal() as session:
        user = UserDao(session).select_by_name(DEMO_USERNAME)
        if user is None:
            return
        profile = session.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)).scalar_one_or_none()
        if profile is None:
            session.add(UserProfile(user_id=user.id))
            session.commit()
            print(f'已为 {DEMO_USERNAME} 创建空画像')


def report_resort_count():
    """提示雪场数据是否已同步"""
    with SessionLocal() as session:
        count = len(session.execute(select(Resort)).scalars().all())
    if count:
        print(f'雪场数据已同步：{count} 个雪场')
    else:
        print('提示：还没有雪场数据，请执行 python -m scripts.sync_to_mysql')


if __name__ == '__main__':
    create_tables()
    seed_demo_user()
    seed_demo_profile()
    report_resort_count()
    print('初始化完成，可启动服务：python main.py')
