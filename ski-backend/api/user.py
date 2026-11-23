from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from config.database import get_db
from config.dependencies import get_current_user
from models.user import User
from schemas.result import success, Result
from schemas.user import RegisterRequest, LoginRequest, UserResponse
from service.user_service import UserService

# 路由对象
router = APIRouter(prefix='/user')


# 依赖函数
def get_user_service(session: Session = Depends(get_db)) -> UserService:
    return UserService(session)


@router.post('/register', response_model=Result)
def register(register_request: RegisterRequest,
             user_service: UserService = Depends(get_user_service)):
    """注册：用户名 + 密码 + 确认密码"""
    user_service.register(register_request)
    return success(msg='注册成功')


@router.post('/login', response_model=Result[str])
def login(login_request: LoginRequest,
          user_service: UserService = Depends(get_user_service)):
    """登录：返回 JWT，前端存到 storage，后续请求放 Authorization 头"""
    token = user_service.login(login_request)
    return success(data=token)


@router.get('/info', response_model=Result[UserResponse])
def my_info(user: User = Depends(get_current_user)):
    """当前登录用户信息（依赖注入解析 JWT）"""
    user_response = UserResponse(**user.__dict__)
    return success(data=user_response)
