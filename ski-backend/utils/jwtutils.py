from datetime import datetime, timedelta
from jose import jwt, JWTError
from config.config import settings

# from config.config import settings

class JwtUtil:

    @staticmethod
    def create_access_token(user_id: int) -> str:
        """
        生成jwt,本例使用用户id做载荷
        :param user_id:
        :return:
        """
        # 设置过期时间
        expire = datetime.now() + timedelta(hours=24)
        # 设置载荷，本例包括用户id和过期时间
        payload = {"uid": user_id, "exp": expire}
        # 第二个参数，签名的秘钥，第三个参数，签名算法
        return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

    @staticmethod
    def parse_access_token(token: str):
        """
        解析jwt, 发你载荷信息
        :param token: jwt字符串
        :return:
        """
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            return payload
        except JWTError:
            raise

# print(JwtUtil.create_access_token(101))