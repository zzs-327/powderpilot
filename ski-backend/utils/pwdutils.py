
import bcrypt

# bcrypt 算法内部本质上是sha256算法
# hash签名算法，根据明文生成密文，无法根据密文得到明文
class PwdUtil:
    # 静态方法
    @staticmethod
    def hash_password(password: str) -> str:
        """生成密码，返回标准bcrypt密文(与passlib生成的$2b$格式兼容)"""
        # bcrypt只使用前72字节，直接传给新版本bcrypt会抛异常，故先截断
        return bcrypt.hashpw(password.encode('utf-8')[:72], bcrypt.gensalt()).decode('utf-8')

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        验证密码
        :param plain_password: 原始密码
        :param hashed_password: hash处理后的密码
        :return:
        """
        return bcrypt.checkpw(plain_password.encode('utf-8')[:72], hashed_password.encode('utf-8'))