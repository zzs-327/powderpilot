"""用户画像接口：读 / 改（"我晋级了"也可以走这里，不只是对话）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from config.database import get_db
from config.dependencies import get_current_user
from models.user import User
from schemas.profile import ProfileResponse, ProfileUpdateRequest
from schemas.result import Result, success
from service.profile_service import PROFILE_FIELDS, ProfileService

router = APIRouter(prefix='/profile')


def get_profile_service(session: Session = Depends(get_db)) -> ProfileService:
    return ProfileService(session)


@router.get('', response_model=Result[ProfileResponse], summary='读取用户画像')
def get_profile(user: User = Depends(get_current_user),
                service: ProfileService = Depends(get_profile_service)):
    return success(data=ProfileResponse(**service.get_profile(user.id)))


@router.put('', response_model=Result[ProfileResponse], summary='更新用户画像')
def update_profile(request: ProfileUpdateRequest,
                   user: User = Depends(get_current_user),
                   service: ProfileService = Depends(get_profile_service)):
    fields = {key: value for key, value in request.model_dump().items()
              if value and key in PROFILE_FIELDS}
    profile = service.upsert(user.id, fields) if fields else service.get_profile(user.id)
    return success(data=ProfileResponse(**profile))
