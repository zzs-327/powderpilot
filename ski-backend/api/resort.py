"""雪场接口：列表 / 雪道统计 / 价格 / 实拍图集。

前端图集与行程卡都靠这几个接口；数据全部来自 MySQL（由 YAML 同步而来）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from config.config import settings
from config.database import get_db
from config.dependencies import get_current_user
from schemas.guidance import MediaResource
from service.media_service import media_for_resort
from config.exceptions import ServiceException
from schemas.result import Result, success
from service.resort_service import ResortService

router = APIRouter(prefix='/resort')


def get_resort_service(session: Session = Depends(get_db)) -> ResortService:
    return ResortService(session)


def _photo_url(object_key: str) -> str:
    """实拍图访问地址：配了 MinIO 就拼公开 URL，否则留空由前端拼本地路径。"""
    if not object_key or not settings.MINIO_PUBLIC_BASE_URL:
        return ''
    return f'{settings.MINIO_PUBLIC_BASE_URL.rstrip("/")}/{object_key.lstrip("/")}'


@router.get('', response_model=Result[list], summary='雪场列表')
def list_resorts(service: ResortService = Depends(get_resort_service)):
    return success(data=service.list_resorts())


@router.get('/{resort}/runs', response_model=Result[dict], summary='雪道数量统计与清单')
def resort_runs(resort: str,
                difficulty: str = Query(default='', description='初级/中级/高级，留空为全部'),
                service: ResortService = Depends(get_resort_service)):
    stats = service.get_run_stats(resort, difficulty)
    if not stats:
        raise ServiceException(f'未收录雪场：{resort}')
    return success(data=stats)


@router.get('/{resort}/prices', response_model=Result[list], summary='价格表（带录入日期）')
def resort_prices(resort: str,
                  item: str = Query(default='', description='按价格项模糊过滤'),
                  service: ResortService = Depends(get_resort_service)):
    """价格列表

    ⚠️ 两种"没有"要分开（前端「雪场库」详情页会同时拉雪道与价格）：
        雪场本身没收录        -> 报错（500），因为这是调用方用错了
        收录了但这类数据没有  -> **返回空列表**，不是错误
      原来两种情况都抛异常，结果是"点开一座没有价格的雪场就弹一次错误提示"，
      而那恰恰是我们的诚实表达（宁缺勿造），不该表现成故障。
    """
    if not service.resolve(resort):
        raise ServiceException(f'未收录雪场：{resort}')
    return success(data=service.get_prices(resort, item))


@router.get('/{resort}/tips', response_model=Result[list], summary='已策展的避坑与已知缺口')
def resort_tips(resort: str, service: ResortService = Depends(get_resort_service)):
    """YAML 里的注意事项。未收录雪场报错；收录了但没有避坑则返回空列表。"""
    from rag.parser import ResortRegistry

    if not service.resolve(resort):
        raise ServiceException(f'未收录雪场：{resort}')
    return success(data=ResortRegistry.curated_tips(resort))


@router.get('/{key}/media', response_model=Result[list[MediaResource]],
            summary='已核验媒体资源（原页链接与授权图片）',
            description='需要 Bearer 登录；返回全部已核验资源。无素材返回空列表，未收录雪场沿用业务错误响应。')
def resort_media(key: str, user=Depends(get_current_user),
                 service: ResortService = Depends(get_resort_service)):
    resort = service.resolve(key)
    if not resort:
        raise ServiceException(f'未收录雪场：{key}')
    return success(data=media_for_resort(resort['resort_key']))


@router.get('/{resort}/photos', response_model=Result[list], summary='实拍图集（B 级证据）')
def resort_photos(resort: str, service: ResortService = Depends(get_resort_service)):
    photos = service.get_photos(resort)
    for photo in photos:
        photo['url'] = _photo_url(photo['object_key'])
    return success(data=photos)
