"""YAML 真相源 → MySQL 同步脚本（幂等）。

用法（在 ski-backend 目录下）：
    python -m scripts.sync_to_mysql              # 建库 + 建表 + 同步全部雪场
    python -m scripts.sync_to_mysql wanlong      # 只同步指定雪场

为什么要这一步：
    YAML 是"人写、可 diff、可版本控制"的真相源；MySQL 是"Agent 能查、能做统计"的运行时库。
    两者由这个脚本单向同步——改数据只改 YAML，不要在数据库里手改。
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import yaml
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.engine import URL, make_url

from config.config import settings
from config.database import Base, SessionLocal, create_all, engine
from models.photo import Photo
from models.price import PRICE_TTL_DAYS, Price
from models.resort import Resort, Run
from rag.parser import (DATA_STATUS_LABEL, DIFFICULTY_TO_LEVEL, DOC_TYPE_LABEL,
                        ResortProvenance, list_resort_ids, weakest_status)
from rag.pricing import to_date, validate_prices


def _date(value) -> dt.date | None:
    """把 '2026-09-01' 之类的字符串转成 date；转不了就返回 None。"""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def ensure_database() -> None:
    """数据库不存在就创建（只对 MySQL 生效）。"""
    url = make_url(settings.DATABASE_URL)
    if not url.drivername.startswith('mysql'):
        return
    # 注意：URL.set(database=None) 会忽略 None（SQLAlchemy 2.0 的行为），
    # 必须用 URL.create 重建一个"不指定库"的连接串。
    server_url = URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
    )
    engine = create_engine(server_url)
    with engine.connect() as conn:
        conn.execute(text(
            f'CREATE DATABASE IF NOT EXISTS {url.database} DEFAULT CHARACTER SET utf8mb4'))
        conn.commit()
    engine.dispose()
    print(f'数据库 {url.database} 已就绪')


def sync_resort(session, data: dict) -> dict[str, int]:
    """同步单个雪场：upsert 雪场本身，重建它的雪道/价格/实拍图。"""
    key = str(data['id'])
    official = data.get('official') or {}
    meta = data.get('meta') or {}
    # 先校验再动库：价格行不合规就整场失败，不会出现"旧价格已删、新价格没写进去"
    validate_prices(key, data.get('prices') or [])

    # 来源解析与 rag/parser.py 共用同一处，避免两边对"出处"各写一套说法
    prov = ResortProvenance.resolve(data)
    prov_yaml = data.get('provenance') or {}
    prov_note = str(prov_yaml.get('method') or '')
    if prov_yaml.get('primary_source'):
        prov_note += f'｜来源：{prov_yaml["primary_source"]}'
    if prov_yaml.get('stated_source'):
        prov_note += f'｜该页面自述原始出处：{prov_yaml["stated_source"]}'
    if prov_yaml.get('caveat'):
        prov_note += f'｜说明：{prov_yaml["caveat"]}'
    # 各类数据分别是几手：可信度现在是**按类型**给的，
    # 一行 note 写不下这个结构，但至少要让人一眼看到"哪个类型是哪档"
    if prov['status_map']:
        breakdown = '、'.join(
            f'{DOC_TYPE_LABEL.get(doc_type, doc_type)}={DATA_STATUS_LABEL[item_status]}'
            for doc_type, item_status in prov['status_map'].items())
        prov_note += f'｜各类数据可信度：{breakdown}'
    if prov['corroborations']:
        prov_note += '｜交叉印证：' + ' '.join(
            ResortProvenance.corroboration_caveats(prov['corroborations']))

    resort = session.execute(
        select(Resort).where(Resort.resort_key == key)).scalar_one_or_none()
    if resort is None:
        resort = Resort(resort_key=key)
        session.add(resort)

    resort.name = str(data.get('name') or key)
    resort.region = str(data.get('region') or '')
    resort.transport_hint = data.get('transport_hint')
    resort.handbook_url = official.get('handbook')
    resort.price_updated_at = _date(official.get('price_updated_at'))
    summary = data.get('runs_summary') or {}
    resort.runs_beginner = summary.get('beginner')
    resort.runs_intermediate = summary.get('intermediate')
    resort.runs_advanced = summary.get('advanced')
    # 官网口径（可能与上面的第三方口径不一致——不一致也要各存各的，见 models/resort.py）
    resort.official_runs_total = official.get('runs_total')
    # 雪场级的 data_status 取**最保守**的那一档：
    # 它只是"整座雪场"的兜底口径，行级真相在 run.data_status / price.data_status。
    # 取最弱而不是取默认档，是为了让任何误用雪场级字段的地方都只会少说、不会多说。
    resort.data_status = weakest_status(prov['status_map'].values()) \
        if prov['status_map'] else prov['data_status']
    resort.data_status_note = prov_note or None
    session.flush()   # 拿到 resort.id

    # 子表整体重建：YAML 是真相源，DB 里多余的旧记录要清掉
    session.execute(delete(Run).where(Run.resort_id == resort.id))
    session.execute(delete(Price).where(Price.resort_id == resort.id))
    session.execute(delete(Photo).where(Photo.resort_id == resort.id))

    for run in data.get('runs') or []:
        difficulty = str(run.get('difficulty') or '')
        run_type, run_src, run_date = ResortProvenance.source_of(
            prov, 'run', resort.name, run)
        session.add(Run(
            resort_id=resort.id,
            name=str(run.get('name') or ''),
            difficulty=difficulty,
            level=DIFFICULTY_TO_LEVEL.get(difficulty, 'all'),
            length_m=run.get('length_m'),
            vertical_m=run.get('vertical_m'),
            top_altitude_m=run.get('top_altitude_m'),
            avg_slope_deg=run.get('avg_slope_deg'),
            max_slope_deg=run.get('max_slope_deg'),
            note=run.get('note'),
            source=run_src,
            source_type=run_type,
            # 行级可信度：这类数据可能有多源印证（见 YAML 的 source_defaults.run）
            data_status=ResortProvenance.status_of(prov, 'run'),
            date=_date(run_date),
        ))

    for price in data.get('prices') or []:
        price_type, price_src, price_date = ResortProvenance.source_of(
            prov, 'price', resort.name, price)
        updated = _date(price.get('updated_at') or price_date
                        or official.get('price_updated_at'))
        session.add(Price(
            resort_id=resort.id,
            item=str(price.get('item') or ''),
            price=price['price'],
            unit=str(price.get('unit') or '次'),
            updated_at=updated,
            expire_at=(updated + dt.timedelta(days=PRICE_TTL_DAYS)) if updated else None,
            source=price_src,
            source_type=price_type,
            # 单条可信度优先：同一雪场里已核验与待核验的价格要能分档
            data_status=price.get('data_status') or ResortProvenance.status_of(prov, 'price'),
            currency=price.get('currency'),
            category=price.get('category'),
            unit_basis=price.get('unit_basis'),
            valid_from=to_date(price.get('valid_from')),
            valid_to=to_date(price.get('valid_to')),
            season=price.get('season'),
            tax_status=price.get('tax_status'),
            includes=price.get('includes'),
            excludes=price.get('excludes'),
            conditions=price.get('conditions'),
            budget_usable=price.get('budget_usable'),
            source_url=price.get('source_url'),
        ))

    for photo in data.get('photos') or []:
        session.add(Photo(
            resort_id=resort.id,
            object_key=str(photo.get('key') or ''),
            shot_at=_date(photo.get('shot_at')),
            location=str(photo.get('location') or ''),
            credit=str(photo.get('credit') or ''),
            license=str(photo.get('license') or ''),
            source_type='B',
        ))

    # 报**入库后的真实条数**，不是 YAML 里的条数。
    # 为什么（踩过）：price.unit 原来是 String(32)，录入分时段优惠价时单位太长，
    # MySQL 抛 DataError → 12 条价格**静默丢失**，而这里照样打印 YAML 的 29 条，
    # 看日志完全看不出问题。计数从库里查，就不会再骗人。
    counts = {
        'runs': session.query(func.count(Run.id)).filter(Run.resort_id == resort.id).scalar(),
        'prices': session.query(func.count(Price.id)).filter(Price.resort_id == resort.id).scalar(),
        'photos': session.query(func.count(Photo.id)).filter(Photo.resort_id == resort.id).scalar(),
    }
    expected = {'runs': len(data.get('runs') or []),
                'prices': len(data.get('prices') or []),
                'photos': len(data.get('photos') or [])}
    for name, want in expected.items():
        if counts[name] != want:
            print(f'⚠️  {resort.name} 的 {name} 入库条数 {counts[name]} != 档案里的 {want}，请检查')
    return counts


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()
    parser = argparse.ArgumentParser(description='把 data/resorts/*.yaml 同步到 MySQL')
    parser.add_argument('resort_ids', nargs='*', help='雪场 id，留空表示全部')
    parser.add_argument('--recreate', action='store_true',
                        help='先删表再重建（改了模型字段时用，数据从 YAML 重新同步）')
    args = parser.parse_args()

    ensure_database()
    if args.recreate:
        import models  # noqa: F401
        Base.metadata.drop_all(engine)
        print('已删除旧表')
    create_all()
    # create_all 不会给已有表补列：现有库没跑 0002 迁移时，这里先拦下，别等插入时才报错
    price_columns = {column['name'] for column in inspect(engine).get_columns('price')}
    if 'currency' not in price_columns:
        raise SystemExit('price 表缺少计价口径列：请先按 docs/DATABASE_MIGRATIONS.md '
                         '完成迁移到 0002_price_pricing_fields，再同步')

    ids = args.resort_ids or list_resort_ids()
    with SessionLocal() as session:
        for resort_id in ids:
            path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
            if not path.exists():
                print(f'跳过 {resort_id}：找不到 {path}')
                continue
            data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            counts = sync_resort(session, data)
            print(f'同步 {data.get("name", resort_id)}：'
                  f'{counts["runs"]} 条雪道 / {counts["prices"]} 条价格 / {counts["photos"]} 张实拍')
        session.commit()

        total_runs = session.execute(select(Run)).scalars().all()
        total_prices = session.execute(select(Price)).scalars().all()
        print(f'数据库现状：雪道 {len(total_runs)} 条，价格 {len(total_prices)} 条')


if __name__ == '__main__':
    main()
