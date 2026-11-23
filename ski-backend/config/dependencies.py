"""FastAPI 依赖：从 JWT 里解析当前登录用户。

沿用 his-backend 的写法，只做了两点清理：
1. 去掉调试用的 print（会污染日志、泄露 payload）；
2. 清理未使用的 import。
"""
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from config.config import settings
from config.database import get_db
from config.exceptions import ServiceException
from dao.user_dao import UserDao
from models.user import User

# 通过 Authorization 请求头获取 token
security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """解析 Bearer token → 用户对象；任何异常统一抛 ServiceException('请重新登录')。"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        user_id: int | None = payload.get('uid')
        if user_id is None:
            raise ServiceException('请重新登录')
    except JWTError:
        raise ServiceException('请重新登录')

    user = UserDao(db).select_by_id(user_id)
    if user is None:
        raise ServiceException('请重新登录')
    return user
