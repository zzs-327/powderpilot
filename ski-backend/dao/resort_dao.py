"""雪场相关数据访问层。

只负责"查数据库"，不写业务逻辑（业务在 service 层）。
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models.photo import Photo
from models.resort import Resort, Run


class ResortDao:
    def __init__(self, session: Session):
        self.session = session

    # ---------------- 雪场 ----------------
    def select_by_key(self, resort_key: str) -> Resort | None:
        """按 YAML 的 id 查（如 wanlong）。"""
        return self.session.execute(
            select(Resort).where(Resort.resort_key == resort_key)
        ).scalar_one_or_none()

    def select_by_key_or_name(self, term: str) -> Resort | None:
        """支持用户直接说"万龙"或"万龙滑雪场"：先按 key，再按名称模糊匹配。"""
        if not term:
            return None
        resort = self.select_by_key(term)
        if resort:
            return resort
        return self.session.execute(
            select(Resort).where(or_(Resort.name.contains(term),
                                     Resort.resort_key.contains(term)))
        ).scalars().first()

    def select_all(self) -> list[Resort]:
        return list(self.session.execute(select(Resort).order_by(Resort.id)).scalars())

    # ---------------- 雪道 ----------------
    def select_runs(self, resort_id: int, difficulty: str = '') -> list[Run]:
        statement = select(Run).where(Run.resort_id == resort_id)
        if difficulty:
            statement = statement.where(Run.difficulty == difficulty)
        return list(self.session.execute(statement.order_by(Run.level, Run.name)).scalars())

    def count_runs_by_level(self, resort_id: int) -> dict[str, int]:
        """按水平统计雪道数量——"有几条高级道"就是这条 SQL 在回答。"""
        rows = self.session.execute(
            select(Run.level, func.count(Run.id)).where(Run.resort_id == resort_id).group_by(Run.level)
        ).all()
        return {level: count for level, count in rows}

    def count_runs_by_difficulty(self, resort_id: int) -> dict[str, int]:
        """按**来源原始级别**统计雪道数量（如"中高级道""连接道"）

        为什么还需要这个：有些雪场官网用的是复合分级（北大湖就是
        初级道/初中级道/中级道/中高级道/高级道/连接道 六类）。
        我们为了分层过滤会把它们归并成初/中/高三桶，但**归并动作必须让用户看得见**——
        否则用户拿官网一对（官网写"高级道 5 条"，我们答"高级道 10 条"）就会以为我们在编。
        """
        rows = self.session.execute(
            select(Run.difficulty, func.count(Run.id))
            .where(Run.resort_id == resort_id).group_by(Run.difficulty)
        ).all()
        return {difficulty: count for difficulty, count in rows if difficulty}

    # ---------------- 实拍图 ----------------
    def select_photos(self, resort_id: int) -> list[Photo]:
        return list(self.session.execute(
            select(Photo).where(Photo.resort_id == resort_id).order_by(Photo.shot_at.desc())
        ).scalars())
