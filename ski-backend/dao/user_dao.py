from sqlalchemy import select
from sqlalchemy.orm import Session

from models.user import User


class UserDao:
    def __init__(self, session: Session):
        self.session = session

    def select_by_name(self, username: str):
        """根据用户名查询用户对象"""
        statement = select(User).where(User.username == username)
        return self.session.execute(statement).scalar()

    def insert(self, user: User):
        """添加用户信息"""
        self.session.add(user)

    def select_by_id(self, user_id):
        return self.session.get(User, user_id)