from sqlalchemy.orm import Session

from config.exceptions import ServiceException
from dao.user_dao import UserDao
from models.user import User
from schemas.user import RegisterRequest, LoginRequest
from utils.jwtutils import JwtUtil
from utils.pwdutils import PwdUtil


class UserService:
    def __init__(self, session: Session):
        self.user_dao = UserDao(session)

    def register(self, register_request: RegisterRequest):
        # 先根据用户名查询数据，如果存在，说明注册过
        user = self.user_dao.select_by_name(register_request.username)
        if user:
            raise ServiceException('用户名已存在')

        # 判断两次数据密码是否一致
        if register_request.password != register_request.confirm_password:
            raise ServiceException('两次输入密码不一致')
        # 对密码加密
        pwd = PwdUtil.hash_password(register_request.password)
        user = User(username=register_request.username, password=pwd)
        self.user_dao.insert(user)

    def login(self, login_request: LoginRequest) -> str:

        user = self.user_dao.select_by_name(login_request.username)
        if not user:
            raise ServiceException('用户名不存在')
        # 判断密码是否正确
        if not PwdUtil.verify_password(login_request.password, user.password):
            raise ServiceException('密码错误')
        # 本例，暂时使用uuid做token
        # return str(uuid.uuid4())
        return JwtUtil.create_access_token(user.id)


