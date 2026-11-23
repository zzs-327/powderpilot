"""从策展 YAML 提供已核验媒体，不代理下载或宣称实时雪况。"""
from pathlib import Path
import re

import yaml
from pydantic import ValidationError
from schemas.guidance import MediaResource

RESORT_DIR = Path(__file__).resolve().parent.parent / 'data' / 'resorts'


def media_for_resort(resort_id: str) -> list[MediaResource]:
    """详情使用全部已核验资源；坏档案或坏条目视为无可展示素材。"""
    if not isinstance(resort_id, str) or not re.fullmatch(r'[a-z0-9_-]+', resort_id):
        return []
    path = RESORT_DIR / f'{resort_id}.yaml'
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get('media'), list):
        return []
    resources = []
    seen = set()
    for item in data['media']:
        if not isinstance(item, dict):
            continue
        try:
            resource = MediaResource.model_validate({**item, 'resort_id': resort_id})
        except (ValidationError, TypeError, ValueError):
            continue
        if str(resource.url) in seen:
            continue
        seen.add(str(resource.url))
        resources.append(resource)
    return resources


# (问题里的意图, 卡片标题/来源里要出现的词)；只调整同一雪场内的先后，不新增或改写卡片
_TOPICS = [
    (r'雾凇|打卡|拍照|出片|风景|观景|云海', r'雾凇|云海|观景|打卡|出片'),
    (r'新手|第一次|初学|入门|零基础', r'星光大道|新手|初级'),
    (r'夜场|夜滑|晚上', r'夜场|夜滑'),
    (r'雪道图|地图|全山|路线图', r'雪道图|trail'),
    (r'住宿|酒店|住哪', r'住宿|酒店'),
    (r'树林|野雪|粉雪', r'树林|粉雪'),
    (r'交通|怎么去|怎么走|乘车|班车', r'乘车|交通'),
    (r'换乘|索道|缆车', r'换乘|索道|缆车'),
]
_BEGINNER = r'新手|第一次|初学|入门|零基础'


def _run_names(resort_id: str) -> set[str]:
    """雪道名的几种叫法：原名、去掉编号的名字、编号本身（H6岗梁道 → 岗梁道 / H6）"""
    try:
        data = yaml.safe_load((RESORT_DIR / f'{resort_id}.yaml').read_text(encoding='utf-8'))
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        return set()
    runs = data.get('runs') if isinstance(data, dict) else None
    names = set()
    for run in runs if isinstance(runs, list) else []:
        name = str(run.get('name') or '') if isinstance(run, dict) else ''
        code = re.match(r'[A-Z]+\d+', name)
        rest = name[code.end():] if code else name
        names.update(n for n in (name, rest, code.group() if code else '') if len(n) >= 2)
    return names


def _rank(resources: list[MediaResource], query: str, beginner: bool,
          run_names: set[str]) -> list[MediaResource]:
    """按本轮问题给卡片排序：点名的雪道 > 话题相符 > 官方来源；新手问题把「新手不要」的卡压后"""
    novice = beginner or bool(re.search(_BEGINNER, query))
    named = {n for n in run_names if n in query}

    def score(item: MediaResource) -> int:
        text = f'{item.title} {item.source}' + (' 雪道图' if item.kind == 'map' else '')
        value = 0
        if any(n in text for n in named):
            value += 5
        for asked, offered in _TOPICS:
            if re.search(asked, query) and re.search(offered, text, re.IGNORECASE):
                value += 3
        if re.search(r'视频|一镜到底', query) and item.kind == 'video':
            value += 2
        if re.search(r'(?<!非)官方', item.source):
            value += 1
        if novice and re.search(r'新手(?:不要|别)', item.notice):
            value -= 4
        return value

    return sorted(resources, key=score, reverse=True)


def media_for_query(resort_id: str, query: str, limit: int = 5) -> list[MediaResource]:
    """问视频、照片或雪道图时，挑出可引用的链接；其他问题不附带。"""
    if not query or not re.search(r'视频|短视频|vlog|照片|图片|实拍|雪道图|地图', query):
        return []
    ranked = _rank(media_for_resort(resort_id), query, False, _run_names(resort_id))
    if re.search(r'视频|短视频|vlog', query):
        ranked = [item for item in ranked if item.kind == 'video']
    elif re.search(r'雪道图|地图', query):
        ranked = [item for item in ranked if item.kind == 'map']
    elif re.search(r'照片|图片|实拍', query):
        ranked = [item for item in ranked if item.kind == 'photo']
    return ranked[:limit]


def media_for_resorts(resort_ids: list[str], query: str = '',
                      beginner: bool = False) -> list[MediaResource]:
    """聊天出口保持最多三个雪场、总计三条及去重的兼容契约；有问题文本时按问题挑卡。"""
    resources = []
    seen = set()
    for resort_id in resort_ids[:3]:
        candidates = media_for_resort(resort_id)
        if query:
            candidates = _rank(candidates, query, beginner, _run_names(resort_id))
        for resource in candidates:
            if str(resource.url) in seen:
                continue
            seen.add(str(resource.url))
            resources.append(resource)
            if len(resources) == 3:
                return resources
    return resources
