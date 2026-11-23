from datetime import datetime

from pydantic import BaseModel, Field

# 注册时，对前端注册信息的封装
class RegisterRequest(BaseModel):
    username: str = Field(description='用户名')
    password: str = Field(description='密码')
    confirm_password: str = Field(description='确认密码')

class LoginRequest(BaseModel):
    username: str = Field(description='用户名')
    password: str = Field(description='密码')

class UserResponse(BaseModel):
    id: int
    username: str
    create_time: datetime
