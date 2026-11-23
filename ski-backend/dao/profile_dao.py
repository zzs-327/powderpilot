"""用户画像数据访问层。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.profile import UserProfile


class ProfileDao:
    def __init__(self, session: Session):
        self.session = session

    def select_by_user_id(self, user_id: int) -> UserProfile | None:
        return self.session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        ).scalar_one_or_none()

    def insert(self, profile: UserProfile) -> None:
        self.session.add(profile)
