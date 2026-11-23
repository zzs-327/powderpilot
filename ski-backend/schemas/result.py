
from pydantic import BaseModel
from typing import Generic, TypeVar, Optional

# 定义泛型，类型参数化
T = TypeVar('T')

class Result(BaseModel, Generic[T]):
    """
        控制层统一响应的数据格式（原 his-backend 文件名拼写为 reuslt.py，这里修正为 result.py）
        code 状态值，借用http协议的响应状态，200成功，500表示业务出问题了
        msg 成功或者失败的提示信息
        data 后端向前端返回业务数据
        """
    code: int
    msg: str | None = None
    data: Optional[T] = None

# 工具函数
def success(data: Optional[T] = None, msg: str = "success", code: int = 200) -> Result[T]:
    """成功响应"""
    return Result(code=code, msg=msg, data=data)

def error(msg: str = "error", code: int = 500, data: Optional[T] = None) -> Result[T]:
    """错误响应"""
    return Result(code=code, msg=msg, data=data)

def page_success(items: list, total: int, page: int, page_size: int, msg: str = "success") -> Result:
    """分页成功响应"""
    return Result(
        code=200,
        msg=msg,
        data={
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size
        }
    )