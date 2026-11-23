"""从已有状态与用户消息派生交互，不从 AI 正文猜测雪场。"""
import json
import re
from pathlib import Path

from agent.conversation_context import known_location
from schemas.guidance import Guidance, Learning, LearningResource, Scene, Suggestion

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


def _resorts() -> list[dict]:
    graph = json.loads((DATA_DIR / 'graph' / 'graph.json').read_text(encoding='utf-8'))
    return [node for node in graph['nodes'] if node.get('type') == 'resort']


def _denied(text: str, node: dict) -> bool:
    return any(re.search(r'(?:不去|不选|不要|不考虑)\s*' + re.escape(name), text)
               for name in [node['label'], node['resort_id'],
                            *(node.get('attrs', {}).get('aliases') or [])] if name)


def _matches(text: str, resorts: list[dict]) -> list[dict]:
    return [node for node in resorts if not _denied(text, node) and any(
        name and name in text for name in
        [node['label'], node['resort_id'], *(node.get('attrs', {}).get('aliases') or [])])]


def _reset_target(text: str) -> bool:
    from rag.parser import ResortRegistry

    return bool(ResortRegistry.detect_unknown(text) or re.search(
        r'(?:其他|别的|另外|换个|换一家|重新推荐).*雪场|(?:换雪场|换个雪场)|推荐.*雪场', text))


def _learning(board: str, beginner: bool) -> Learning:
    catalog = json.loads((DATA_DIR / 'learning_resources.json').read_text(encoding='utf-8'))
    resources = [LearningResource.model_validate(item) for item in catalog.get('resources', [])]
    # 单双板/水平未知时不把特定训练视频当成适用建议。
    resources = [item for item in resources if board and item.board in (board, '通用')
                 and (not beginner or item.level in ('新手', '入门', '通用'))]
    return Learning(notice=catalog['notice'] if not resources else '请在安全场地并由教练指导练习。',
                    resources=resources)


def _user_context(state: dict, profile: dict, human: list[str]) -> tuple[str, bool, str]:
    board = str(state.get('board') or profile.get('board_type') or '')
    for text in reversed(human):
        denied = [value for value in ('单板', '双板') if re.search(
            r'(?:不滑|不玩|不学|不要|不选)\s*' + value, text)]
        candidates = [value for value in ('单板', '双板') if value not in denied and re.search(
            r'(?:我(?:是|滑|玩|学|选择|选|用)?|(?:我)?(?:想|打算|准备)(?:滑|玩|学|选择|选|用)|改(?:玩|滑|学)|还是(?:玩|滑)?)' + value
            + r'|(?:^|[，。\s])' + value + r'(?=新手|中级|高级|[，。\s]|$)',
            text)]
        if len(candidates) == 1:
            board = candidates[0]
            break
        if denied:
            if board in denied:
                board = ''
            break
    level = str(profile.get('level') or '')
    beginner = level in ('新手', '入门', 'beginner')
    for text in reversed(human):
        if re.search(r'我(?:已经|现在)?(?:是|属于|算)?(?:中级|高级|进阶|老手)|我不是新手', text):
            beginner = False
            break
        if re.search(r'我(?:是|属于|算)?(?:(?:单板|双板))?(?:新手|零基础|初学)|(?:^|[，。\s])(?:新手|零基础)|第一次滑', text):
            beginner = True
            break
    location = known_location(state.get('messages', []), str(profile.get('home_region') or ''))
    return board, beginner, location


def _archive(resort_id: str) -> dict:
    import yaml
    path = DATA_DIR / 'resorts' / f'{resort_id}.yaml'
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _text_parts(data: dict) -> list[str]:
    parts = []
    for tip in data.get('tips') or []:
        if isinstance(tip, dict):
            parts.append(str(tip.get('text') or ''))
    for info in (data.get('by_level') or {}).values():
        if isinstance(info, dict):
            parts.append(str(info.get('summary') or ''))
            parts.extend(str(tip) for tip in info.get('tips') or [])
    for item in data.get('media') or []:
        if isinstance(item, dict):
            parts.append(str(item.get('title') or ''))
    return parts


def _mentions_run(intent: str, data: dict) -> bool:
    names = []
    for run in data.get('runs') or []:
        if not isinstance(run, dict):
            continue
        name = str(run.get('name') or '')
        code = re.match(r'[A-Z]+\d+', name)
        rest = name[code.end():] if code else ''
        names.extend(n for n in (name, rest, code.group(0) if code else '') if len(n) >= 2)
    return any(name in intent for name in sorted(set(names), key=len, reverse=True))


def _topic_key(intent: str, data: dict) -> str:
    """本轮已经在问的话题。空串表示没有可接的雪场话题。"""
    if re.search(r'教学|怎么学|课程|练习|先学', intent):
        return ''
    if re.search(r'视频|短视频|vlog', intent):
        return 'video'
    if re.search(r'雾凇|打卡|拍照|出片', intent):
        return 'rime'
    if re.search(r'夜场|夜滑', intent):
        return 'night'
    if re.search(r'Ikon|通行证', intent, re.I):
        return 'pass'
    if re.search(r'住宿|酒店|住哪', intent):
        return 'stay'
    if re.search(r'交通|怎么走|怎么去|乘车|班车', intent):
        return 'way'
    if re.search(r'价格|多少钱|雪票|票价|预算|费用|租赁|租具', intent):
        return 'price'
    if re.search(r'新手|第一次|初学|入门|零基础', intent) and re.search(r'雪道|滑哪', intent):
        return 'beginner-run'
    if _mentions_run(intent, data):
        return 'one-run'
    if re.search(r'雪道|坡度|难度|缆车|索道', intent):
        return 'runs'
    return ''


# 换掉整组追问的话题。价格、交通、雪道清单仍沿用原有第一条，只把空着的位置补上。
_REPLACE = {'video', 'rime', 'night', 'pass', 'stay', 'beginner-run', 'one-run'}
_NEXT_ORDER = {
    'rime': ['next-runs', 'next-night', 'next-way', 'next-price', 'next-video', 'next-stay', 'next-pass'],
    'night': ['next-stay', 'next-rime', 'next-price', 'next-way', 'next-video', 'next-runs', 'next-pass'],
    'pass': ['next-price', 'next-runs', 'next-way', 'next-rime', 'next-night', 'next-video', 'next-stay'],
    'stay': ['next-way', 'next-runs', 'next-price', 'next-rime', 'next-night', 'next-video'],
    'video': ['next-rime', 'next-runs', 'next-night', 'next-stay', 'next-way', 'next-price', 'next-pass'],
    'beginner-run': ['next-rime', 'next-night', 'next-price', 'next-video', 'next-way', 'next-stay', 'next-pass'],
    'one-run': ['next-rime', 'next-night', 'next-video', 'next-price', 'next-runs', 'next-way', 'next-stay', 'next-pass'],
    'price': ['next-pass', 'next-runs', 'next-rime', 'next-night', 'next-way', 'next-video', 'next-stay'],
    'runs': ['next-rime', 'next-night', 'next-pass', 'next-video', 'next-way', 'next-stay', 'next-price'],
    'way': ['next-price', 'next-runs', 'next-rime', 'next-night', 'next-stay', 'next-video', 'next-pass'],
    '': ['next-runs', 'next-rime', 'next-night', 'next-price', 'next-pass', 'next-video', 'next-way', 'next-stay'],
}
_SUBJECT = {
    'next-runs': r'雪道',
    'next-rime': r'雾凇|打卡',
    'next-night': r'夜场|夜滑',
    'next-pass': r'Ikon|通行证',
    'next-price': r'雪票|预算|价格|多少钱|购票|租具',
    'next-video': r'视频',
    'next-stay': r'住宿|酒店',
    'next-way': r'交通|怎么去|怎么走|乘车',
}


def _offers(name: str, data: dict, board: str, beginner: bool, topic: str) -> list[tuple[str, str, str]]:
    """下一条只推荐这座雪场档案里真有的内容，并且避开刚刚问过的话题。"""
    parts = _text_parts(data)
    blob = '\n'.join(parts)
    media = [item for item in data.get('media') or [] if isinstance(item, dict)]
    if board and beginner:
        who = f'{board}新手'
    elif board:
        who = f'{board}当前水平'
    elif beginner:
        who = '新手'
    else:
        who = '当前水平'
    catalog = []
    if data.get('runs') or data.get('by_level'):
        catalog.append(('next-runs', '看看适合的雪道',
                        f'{name}有哪些适合{who}的雪道？请优先采用分层建议里点名的雪道和走法，并注明来源。'))
    if '雾凇' in blob:
        catalog.append(('next-rime', '哪里看雾凇',
                        f'{name}哪里看雾凇？请只根据已收录攻略回答，并说明是不是每天都有。'))
    if any(re.search(r'夜滑|夜场', part) and '雪道' in part for part in parts):
        catalog.append(('next-night', '夜场能滑哪些',
                        f'{name}夜场能滑哪些雪道？请说明资料来源，以及雪季不同会不会变。'))
    if 'Ikon' in blob or '通行证' in blob:
        catalog.append(('next-pass', '通行证能不能用',
                        f'{name}能用 Ikon Pass 吗？请说明不可用日期，并说明通行证售价不是雪票价格。'))
    catalog.append(('next-price', '雪票多少钱',
                    f'{name}的雪票和租具多少钱？没有收录的金额请直接说没有，不要估算。'))
    if any(item.get('kind') == 'video' for item in media):
        catalog.append(('next-video', '有哪些滑雪视频',
                        f'{name}有哪些已收录的滑雪视频链接？只转述标题和链接，未观看的不要说成推荐。'))
    if any(item.get('category') == 'stay' or '住宿' in str(item.get('title') or '') for item in media) or '住宿' in blob:
        catalog.append(('next-stay', '住哪里方便',
                        f'{name}住宿有什么已收录信息？没有房价或开放日期就直接说明。'))
    if any(word in blob for word in ('乘车', '班车', '交通')):
        catalog.append(('next-way', '怎么去雪场',
                        f'{name}怎么去？请区分已有资料和需要向官方确认的班次、票价。'))
    by_id = {item[0]: item for item in catalog}
    return [by_id[key] for key in _NEXT_ORDER.get(topic, _NEXT_ORDER['']) if key in by_id]


def _suggestions(human: list[str], scene: list[Scene], board: str,
                 beginner: bool, location: str) -> tuple[list[Suggestion], bool]:
    # 只用用户本轮意图决定追问；场景入口仍独立保留。
    intent = human[-1] if human else ''
    if re.fullmatch(r'\s*(?:继续|还有什么建议|还有呢|好的|好|嗯)[。！？]?\s*', intent):
        intent = next((text for text in reversed(human[:-1]) if not re.fullmatch(
            r'\s*(?:继续|还有什么建议|还有呢|好的|好|嗯)[。！？]?\s*', text)), intent)
    pause = bool(re.search(r'先不(?:去|选|考虑|安排)|暂时不(?:去|选|考虑|安排)|暂不(?:安排|规划)|不急着(?:去|选)|先别(?:推荐|安排)|暂停|暂缓', intent))
    preparation = bool(re.search(r'准备什么|怎么准备|如何准备|行前|装备|穿什么|带什么|注意什么|注意事项|入门准备|先了解|先学', intent))
    # 「推荐滑雪视频」是雪场链接，不是教学。只有没绑雪场、或明确在问教学时才进教学追问。
    learning_requested = bool(re.search(r'教学|怎么学|课程|练习|先学', intent) or (
        '视频' in intent and not scene))
    price = bool(re.search(r'价格|多少钱|雪票|票价|预算|费用|租赁|租具', intent))
    transport = bool(re.search(r'交通|怎么走|怎么去|路线|接驳|停车', intent))
    runs = bool(re.search(r'雪道|缆车|坡度|难度|适合|开放|营业', intent))
    fact = bool(re.search(r'海拔|位置|在哪|哪里|地址|雪季|面积|长度|多少|几条|介绍', intent))
    suggestions = []
    archive = _archive(scene[0].resort_id) if len(scene) == 1 else {}
    topic = _topic_key(intent, archive) if len(scene) == 1 else ''

    def add(key, label, question):
        suggestions.append(Suggestion(id=key, label=label, question=question))

    def add_offers():
        if len(scene) != 1:
            return
        for key, label, question in _offers(scene[0].name, archive, board, beginner, topic):
            if len(suggestions) >= 3:
                return
            if any(item.id == key for item in suggestions):
                continue
            asked = ' '.join(item.question for item in suggestions)
            if re.search(_SUBJECT[key], asked):
                continue
            add(key, label, question)

    focused = False
    if pause or preparation or learning_requested:
        if learning_requested:
            add('learning-safety', '练习前的安全边界', f'{board or "滑雪"}{"新手" if beginner else "当前水平"}练习时怎样选择安全场地，哪些动作需要教练指导？')
            add('learning-preparation', '上课前准备什么', f'学习{board or "滑雪"}前需要准备哪些装备，如何检查护具和雪具是否合适？')
        else:
            add('beginner-preparation', '第一次滑雪装备清单', f'{board or "滑雪"}{"新手" if beginner else "入门"}第一次滑雪需要准备哪些衣物和护具，哪些适合租赁？')
            add('beginner-safety', '先了解安全与课程', f'{board or "滑雪"}入门前应了解哪些安全规则，第一次上课怎样选择教练和练习场地？')
        if not board:
            add('choose-board', '单双板怎么选', '单板和双板怎么选？请结合我的水平、运动经验与学习目标说明。')
        else:
            add('preparation-fitness', '出发前如何做准备', f'第一次学习{board}前可以做哪些体能和热身准备？请说明安全边界，不用安排雪场或行程。')
    elif topic in _REPLACE:
        add_offers()
        focused = len(suggestions) >= 2
        if not focused:
            suggestions.clear()
    if not (pause or preparation or learning_requested) and not focused:
        if price or transport or runs or fact:
            names = '、'.join(item.name for item in scene)
            target = names or '我关注的雪场'
            if price:
                add('cost-inclusions', '购票前核对包含项目', f'{target}购票前应核对哪些包含项目、租具押金和退改规则？未核验的条款请明确说明。')
                add('cost-transport', '交通方式怎么选', f'{target}有哪些交通方式{f"，从{location}出发" if location else ""}？请区分已有资料与需实时确认的信息。')
            elif transport:
                add('arrival-preparation', '到场前核对什么', f'前往{target}之前，需要核对哪些开放时间、装备与安全事项？请标明资料日期。')
                add('resort-cost', '雪票与租具预算', f'{target}的雪票和{board or "雪具"}租赁预算是多少？请区分资料日期与实时价格。')
            elif runs:
                add('runs-safety', '练习安全边界', f'在{target}滑{board or "雪"}时，如何判断雪道是否符合我的水平，哪些情况应请教练指导？')
                add('resort-cost', '雪票与租具预算', f'{target}的雪票和{board or "雪具"}租赁预算是多少？请区分资料日期与实时价格。')
            else:
                add('fact-conditions', '出行前确认开放条件', f'前往{target}之前应核对哪些开放条件和天气风险？请区分档案信息与实时信息。')
                add('resort-runs', '查看适合我的雪道', f'{target}有哪些适合{board or "我的单双板类型"}{"新手" if beginner else "当前水平"}的雪道？请说明来源和安全边界。')
            if len(scene) > 1:
                add('compare-planning', '分别核对出行条件', f'请分别列出{names}出行前仍需确认的信息，不要混用不同雪场的资料。')
        elif len(scene) > 1:
            names = '、'.join(item.name for item in scene)
            add('compare-runs', '比较适合我的雪道', f'请比较{names}哪些雪道适合{board or "我的单双板类型"}{"新手" if beginner else "当前水平"}，保留每个雪场的独立来源。')
            add('compare-cost', '比较交通与预算', f'请比较{names}的交通与雪票预算，不要混用不同雪场的资料。')
        elif scene:
            name = scene[0].name
            add('resort-runs', '查看适合我的雪道', f'{name}有哪些适合{board or "我的单双板类型"}{"新手" if beginner else "当前水平"}的雪道？请说明来源和安全边界。')
            add('resort-cost', '雪票与租具预算', f'{name}的雪票和{board or "雪具"}租赁预算是多少？请区分资料日期与实时价格。')
        else:
            add('choose-resort', '按我的条件选雪场', f'请推荐适合{"新手" if beginner else "我的水平"}{board}的雪场{f"，从{location}出发" if location else "，先问我出发位置"}。')
    if not (pause or preparation or learning_requested):
        add_offers()
    if len(suggestions) < 3 and not (pause or preparation or learning_requested):
        if not board:
            add('choose-board', '单双板怎么选', '我是新手，单板和双板怎么选？有哪些安全入门差异？' if beginner else '单板和双板怎么选？请先确认我的水平与目标。')
        elif beginner:
            add('beginner-learning', '安全入门与课程', f'{board}新手第一次滑雪应先学什么？请只推荐已核验的教学资源，缺资料请说明。')
        else:
            add('learning', '下一步练习什么', f'针对{board}推荐下一步练习主题，请先确认我的水平，只提供已核验的教学资料。')
    return suggestions[:3], learning_requested


def build_guidance(state: dict, profile: dict | None = None) -> dict:
    profile = profile or {}
    human = [str(message.content) for message in state.get('messages', [])
             if getattr(message, 'type', '') == 'human']
    resorts = _resorts()
    current = _matches(human[-1], resorts) if human else []
    # 本轮明确提到多个雪场时分别给入口；换场/未知目标后旧状态不复活。
    latest_target = []
    reset_target = False
    for text in human:
        matched = _matches(text, resorts)
        if _reset_target(text):
            latest_target = matched
            reset_target = not bool(matched)
        elif matched:
            latest_target = matched
            reset_target = False
    bound = current
    if not bound and not reset_target:
        bound = _matches(str(state.get('resort') or ''), resorts) or latest_target
    excluded = set()
    for text in human:
        for node in resorts:
            if _denied(text, node):
                excluded.add(node['resort_id'])
            elif node in _matches(text, [node]):
                excluded.discard(node['resort_id'])
    bound = [node for node in bound if node['resort_id'] not in excluded]
    scene = [Scene(resort_id=node['resort_id'], name=node['label']) for node in bound[:3]]
    board, beginner, location = _user_context(state, profile, human)
    suggestions, learning_requested = _suggestions(human, scene, board, beginner, location)
    learning = _learning(board, beginner) if learning_requested or (beginner and board) else Learning()
    from service.media_service import media_for_resorts
    from service.run_brief import build_run_brief
    brief = build_run_brief(bound[0]['resort_id'], bound[0]['label'], human[-1]) if (
        len(bound) == 1 and human) else None
    return Guidance(suggestions=suggestions[:3], scenes=scene,
                    learning=learning, run_brief=brief, media=media_for_resorts(
                        [node['resort_id'] for node in bound], query=human[-1] if human else '',
                        beginner=beginner)).model_dump(mode='json', exclude_none=True)
