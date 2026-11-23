"""雪场业务层：把 DAO 查出来的 ORM 对象转成"带溯源信息的字典"。

Agent 工具只调用这里，不直接碰 DAO——保持分层，也方便以后换数据源。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from dao.price_dao import PriceDao
from dao.resort_dao import ResortDao
from models.price import PRICE_TTL_DAYS
from rag.pricing import DEFAULT_CURRENCY

LEVEL_LABEL = {'beginner': '初级', 'intermediate': '中级', 'advanced': '高级', 'all': '通用'}


def _expire_at(updated_at: dt.date | None, ttl_days: int) -> dt.date | None:
    if not updated_at:
        return None
    return updated_at + dt.timedelta(days=ttl_days)


class ResortService:
    def __init__(self, session: Session):
        self.session = session
        self.resort_dao = ResortDao(session)
        self.price_dao = PriceDao(session)

    # ---------------- 雪场 ----------------
    def list_resorts(self) -> list[dict]:
        """雪场列表（前端「雪场库」用）

        除了名称/地区，还带上**数据可信度档位**与收录量——
        这是本项目对外最该露的东西：用户一眼能看到"这家是官方直采、那家只是二手转引"。
        一次查完，避免前端为每座雪场再各发一次请求。
        """
        out = []
        for resort in self.resort_dao.select_all():
            detail = self.resort_dao.count_runs_by_level(resort.id)
            out.append({
                'resort_key': resort.resort_key,
                'name': resort.name,
                'region': resort.region,
                'data_status': resort.data_status,
                'data_status_note': resort.data_status_note,
                'runs_total': sum(detail.values()),
                'summary_total': sum(v for v in (resort.runs_beginner,
                                                 resort.runs_intermediate,
                                                 resort.runs_advanced) if v) or None,
                'official_total': resort.official_runs_total,
                'price_count': len(self.price_dao.select_prices(resort.id)),
                'price_updated_at': resort.price_updated_at.isoformat()
                                    if resort.price_updated_at else '',
            })
        return out

    def resolve(self, term: str) -> dict | None:
        """把用户输入的雪场名/简称解析成雪场信息。"""
        resort = self.resort_dao.select_by_key_or_name(term or '')
        if not resort:
            return None
        return {'id': resort.id, 'resort_key': resort.resort_key, 'name': resort.name,
                'region': resort.region, 'data_status': resort.data_status,
                'data_status_note': resort.data_status_note,
                'price_updated_at': resort.price_updated_at}

    # ---------------- 价格 ----------------
    def get_prices(self, term: str, item: str = '') -> list[dict]:
        """返回价格列表（含录入日期与保鲜期）。term 可以是 id 或中文名。"""
        resort = self.resort_dao.select_by_key_or_name(term or '')
        if not resort:
            return []
        rows = self.price_dao.select_prices(resort.id, item)
        return [
            {
                'resort_key': resort.resort_key,
                'resort_name': resort.name,
                'item': p.item,
                'price': p.price,
                'unit': p.unit,
                'currency': p.currency or DEFAULT_CURRENCY,
                'category': p.category,
                'unit_basis': p.unit_basis,
                'valid_from': p.valid_from.isoformat() if p.valid_from else None,
                'valid_to': p.valid_to.isoformat() if p.valid_to else None,
                'season': p.season,
                'tax_status': p.tax_status,
                'includes': p.includes,
                'excludes': p.excludes,
                'conditions': p.conditions,
                'budget_usable': p.budget_usable is not False,
                'source_url': p.source_url,
                'updated_at': (p.updated_at or dt.date.min).isoformat() if p.updated_at else '',
                'expire_at': (p.expire_at or _expire_at(p.updated_at, PRICE_TTL_DAYS) or dt.date.min).isoformat()
                             if (p.expire_at or p.updated_at) else '',
                'source': p.source or f'{resort.name}资料（未标注具体出处）',
                'source_type': p.source_type,
                # 行级可信度优先：同一座雪场，雪道可能有多源印证而价格只有单一转引，
                # 拿雪场级档位渲染每一条必然有一类说错
                'data_status': p.data_status or resort.data_status,
                # 来源说明随价格一起给模型：来源页改版、口径滞后这类事必须能传到回答里
                'data_status_note': resort.data_status_note or '',
            }
            for p in rows
        ]

    # ---------------- 雪道统计 ----------------
    def get_run_stats(self, term: str, difficulty: str = '') -> dict | None:
        """返回雪道统计 + 雪道清单——"有几条高级道"靠这里回答。

        注意区分两个数字（否则会自相矛盾）：
            summary_counts：**来源声明的**雪道总数（来自 YAML 的 runs_summary）
            detail_counts ：本库已人工收录明细的雪道数量

        为什么这两个键原来叫 official_*、现在改名：
            叫 official 会诱导调用方写出"官方公布共 32 条"这种话——而这批数据
            其实是二手转引（万龙官网已失效被抢注）。**字段名会变成谎话的源头**，
            所以这里只描述事实：这是"来源声明的总数"，是不是官方由 data_status 说。
        """
        resort = self.resort_dao.select_by_key_or_name(term or '')
        if not resort:
            return None
        detail = self.resort_dao.count_runs_by_level(resort.id)
        summary = {
            'beginner': resort.runs_beginner,
            'intermediate': resort.runs_intermediate,
            'advanced': resort.runs_advanced,
        }
        summary = {k: v for k, v in summary.items() if v}
        runs = self.resort_dao.select_runs(resort.id, difficulty)
        return {
            'resort_key': resort.resort_key,
            'resort_name': resort.name,
            # 雪道这一步的可信度按行取（雪道可能有多源印证，而价格没有）；
            # 取不到才退回雪场级口径——那个值是各类里最保守的一档
            'data_status': (runs[0].data_status or resort.data_status) if runs
                           else resort.data_status,
            'data_status_note': resort.data_status_note,
            'summary_counts': {LEVEL_LABEL.get(k, k): v for k, v in summary.items()},
            'summary_total': sum(summary.values()) or None,
            # 官网口径的总数（可能为空，也可能与 summary_total 不一致）
            'official_total': resort.official_runs_total,
            'detail_counts': {LEVEL_LABEL.get(k, k): v for k, v in detail.items()},
            'detail_total': sum(detail.values()),
            # 来源**原始级别**的条数（如"中高级道 5 条"）：分层归并过的数字必须对得上官网
            'raw_counts': self.resort_dao.count_runs_by_difficulty(resort.id),
            'runs': [
                {'name': r.name, 'difficulty': r.difficulty, 'level': r.level,
                 'length_m': r.length_m, 'vertical_m': r.vertical_m,
                 'top_altitude_m': r.top_altitude_m, 'avg_slope_deg': r.avg_slope_deg,
                 'max_slope_deg': r.max_slope_deg, 'note': r.note,
                 'source': r.source, 'source_type': r.source_type,
                 'date': r.date.isoformat() if r.date else ''}
                for r in runs
            ],
            'source': runs[0].source if runs else f'{resort.name}资料（未标注具体出处）',
            # 置信级别跟着数据走，不在工具里写死 'A'：
            # 整份雪道表来自同一档案，取第一条的 source_type 即可
            'source_type': runs[0].source_type if runs else 'A',
            'date': runs[0].date.isoformat() if runs and runs[0].date else '',
        }

    # ---------------- 实拍图 ----------------
    def get_photos(self, term: str) -> list[dict]:
        resort = self.resort_dao.select_by_key_or_name(term or '')
        if not resort:
            return []
        return [
            {'object_key': p.object_key, 'shot_at': p.shot_at.isoformat() if p.shot_at else '',
             'location': p.location, 'credit': p.credit, 'license': p.license,
             'source_type': p.source_type}
            for p in self.resort_dao.select_photos(resort.id)
        ]
