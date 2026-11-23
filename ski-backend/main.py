"""PowderPilot 滑雪情报官 —— FastAPI 入口。

基于 his-backend 脚手架改造：
- 保留：分层结构 / 统一响应 Result / 全局异常 / JWT 鉴权 / CORS
- 新增：chat（对话）/ profile（画像）/ resort（雪场）路由 —— Day3 接入
"""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.user import router as user_router
from config.config import settings
from config.exceptions import ServiceException
from schemas.result import error
from utils.log import LogUtil

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
# 服务端日志同时写 logs/backend.log：引用校验的打回原因、降级原因都在这
# （原来只有 print 到控制台，服务起在哪个终端里就等于没留下证据）
logger = LogUtil.get_logger('ski')

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时检查/创建数据表（MySQL 没起也不阻塞启动，只记日志）。"""
    try:
        from config.database import create_all

        create_all()
        logger.info('数据表检查完成')
    except Exception:
        logger.exception('建表失败（MySQL 未启动？）——接口仍可启动，但数据库相关接口会报错')
    yield


app = FastAPI(
    title='PowderPilot 滑雪情报官 API',
    version='0.1.0',
    description='反照骗、可溯源的滑雪情报 Agent：选雪场 / 排行程 / 分层推荐 / 教学与避坑',
    lifespan=lifespan,
)


def _allow_origins() -> list[str]:
    """CORS 白名单：解析 settings.ALLOW_ORIGINS（逗号分隔），为空则回退 '*'"""
    origins = [item.strip() for item in settings.ALLOW_ORIGINS.split(',') if item.strip()]
    return origins or ['*']


def _cors_origin() -> str:
    """异常响应该回显哪个 Origin。

    配了白名单就回显第一个域名，没配（本地开发）才回退 '*'。
    部署用 '*' 的问题：中间件是 allow_credentials=True，而浏览器不接受
    带凭证请求里的通配符来源——表现为"CORS 配了但带 cookie 的请求就是失败"。
    """
    origins = _allow_origins()
    return origins[0] if origins != ['*'] else '*'


def _add_cors_headers(response: JSONResponse) -> JSONResponse:
    """异常响应也要带 CORS 头，否则前端只能看到"网络错误"。"""
    response.headers['Access-Control-Allow-Origin'] = _cors_origin()
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Allow-Methods'] = '*'
    response.headers['Access-Control-Allow-Headers'] = '*'
    return response


@app.exception_handler(ServiceException)
def service_exception(request: Request, e: ServiceException):
    """业务异常：统一转成 Result 格式，HTTP 状态码固定 200（前端按 code 判断）。"""
    msg = e.args[0] if e.args else '业务异常'
    result = error(msg=msg)
    return _add_cors_headers(JSONResponse(status_code=200, content=result.model_dump()))


@app.exception_handler(StarletteHTTPException)
def http_exception(request: Request, e: StarletteHTTPException):
    """401/403/404 等也统一成 Result 格式，否则前端拿到的是 {'detail': ...}。

    这里保留原始 HTTP 状态码（语义正确），前端按 code 判断即可。
    """
    result = error(msg=str(e.detail or '请求失败'), code=e.status_code)
    return _add_cors_headers(JSONResponse(status_code=e.status_code, content=result.model_dump()))


@app.exception_handler(Exception)
def unknown_exception(request: Request, e: Exception):
    """兜底异常：记录堆栈，只给前端返回统一格式，避免暴露内部细节。"""
    logger.exception('未捕获异常: %s %s', request.method, request.url.path)
    result = error(msg='服务器内部错误')
    return _add_cors_headers(JSONResponse(status_code=200, content=result.model_dump()))


# ---------------- 路由注册 ----------------
from api.chat import router as chat_router          # POST /chat、/chat/stream
from api.profile import router as profile_router    # GET/PUT /profile
from api.resort import router as resort_router      # GET /resort...

app.include_router(router=user_router, prefix='/api/v1')
app.include_router(router=chat_router, prefix='/api/v1')
app.include_router(router=profile_router, prefix='/api/v1')
app.include_router(router=resort_router, prefix='/api/v1')

# ---------------- 跨域 ----------------
# 白名单来自 settings.ALLOW_ORIGINS（.env 里配）：默认 '*' 只供本地开发，
# 部署时必须列出来源——allow_credentials 与通配符互斥（见 config.py 说明）
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins(),
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/', tags=['健康检查'])
def health():
    """探活接口，前端联调前先访问它确认服务起来了。"""
    return {'code': 200, 'msg': 'PowderPilot API is running', 'data': None}


if __name__ == '__main__':
    uvicorn.run('main:app', host=settings.APP_HOST, port=settings.APP_PORT, reload=settings.DEBUG)
