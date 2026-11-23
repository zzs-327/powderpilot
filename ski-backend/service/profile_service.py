"""用户画像业务层。

画像字段只有 4 个（对应计划书 v0.4 第二节）：
    level（新手/进阶/老手）、board_type（单板/双板/都玩）、
    home_region（常活动区域）、budget_band（预算带）
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from dao.profile_dao import ProfileDao
from models.profile import UserProfile

# 允许更新的字段白名单（防止模型乱写字段）
PROFILE_FIELDS = {
    'level': '水平',
    'board_type': '单双板',
    'home_region': '常活动区域',
    'budget_band': '预算带',
}


class ProfileService:
    def __init__(self, session: Session):
        self.session = session
        self.dao = ProfileDao(session)

    def get_profile(self, user_id: int) -> dict:
        """读画像；没有记录返回空 dict（表示还没采集）。"""
        profile = self.dao.select_by_user_id(user_id)
        if not profile:
            return {}
        return {key: getattr(profile, key) for key in PROFILE_FIELDS if getattr(profile, key)}

    def upsert(self, user_id: int, fields: dict) -> dict:
        """按字段更新画像（不存在则创建）。返回更新后的完整画像。"""
        profile = self.dao.select_by_user_id(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            self.dao.insert(profile)
        for key, value in fields.items():
            if key in PROFILE_FIELDS and value:
                setattr(profile, key, str(value))
        self.session.flush()
        return self.get_profile(user_id)
