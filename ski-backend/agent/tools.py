"""Agent 工具层（本地资料查询、行程状态与外部实时数据）。

对应计划书 v0.4 第 5.2 节。docstring 就是给模型看的"何时调用"说明——
写得越清楚，Agentic RAG 的误判越少，这也是面试里"你怎么控制 Agent 判断"的答案。

分工：
    search_resort_kb  非结构化检索（RAG）—— 雪道体验、避坑、教学
    get_resort_runs   结构化统计（SQL）—— "有几条高级道"这类 count/聚合问题
    get_resort_price  结构化查询（SQL）—— 价格，带录入日期与保鲜期
    get_resort_lifts  缆车档案（策展 YAML）—— 同场条目与资料缺失
    set_trip_info     写行程状态（本次行程）
    update_profile    写用户画像（长期记忆）
    （外部 3 个：get_weather / get_route / search_nearby，走百度地图 MCP，
      接入层在 agent/mcp_tools.py，参数与证据都在那里做过适配）

为什么结构化问题不用 RAG：
    RAG 擅长语义相似，不擅长 count/聚合/精确数值；
    "有几条高级道"交给向量检索，模型很可能猜一个数字。

⚠️ 本文件是**模型唯一的事实来源**：这里写"官方公布"，模型就会对用户说"官方公布"。
   所以凡是涉及"数据是什么来路"的措辞，一律由 data_status 决定，不许写死。
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from agent.evidence import Evidence, format_block, from_document
from agent.mcp_tools import (BaiduMapMcp, McpEvidence, call_mcp,
                             resolve_adcode)
from agent.state import (FIELD_LABELS, TRIP_FIELDS, TripState, UserContext,
                         missing_fields, parse_trip_value)
from config.config import settings
from config.database import SessionLocal
from rag.parser import detect_unknown_resort
from rag.pricing import narrow_prices, price_evidence_text, reference_price_notice
from rag.retriever import retrieve
from service.profile_service import PROFILE_FIELDS, ProfileService
from service.resort_service import ResortService


def _num(value) -> str:
    """数字转文本：18.0 -> "18"，18.5 -> "18.5"

    坡度在库里是 Float，直接插值会出现"平均坡度约 16.0 度"这种别扭写法。
    """
    if value is None:
        return ''
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else str(number)


# 雪道总数那句话的句式：**按数据可信度分档**，不许一律写成"官方公布"
_TOTAL_PHRASE = {
    'verified': '{name}官方公布雪道共 {total} 条（{counts}）。',
    'corroborated': '{name}的雪道资料（非官方直采，但有多个来源相互印证）显示共 {total} 条（{counts}）。',
    'transcribed': '{name}的雪道资料（二手转引，非官网直采）显示共 {total} 条（{counts}）。',
    'sample': '{name}的雪道数据仍为示例占位（未核实）：共 {total} 条（{counts}）。',
}
_DEFAULT_TOTAL_PHRASE = '{name}收录的雪道资料显示共 {total} 条（{counts}）。'


def _total_sentence(stats: dict) -> str:
    """拼"雪道总数"那句话，措辞跟着 data_status 走

    这里原来写死的是"{name}官方公布雪道共 N 条"。问题在于：
        万龙的数据本身是**二手转引**（官网已失效被抢注），工具输出却替它自称
        "官方公布"，模型照抄，回答就成了"万龙官方公布的雪道共 32 条"。
    工具输出是模型唯一的事实来源——**在这一层抬高数据身份，等于把上游做过的
    可信度分级（provenance / data_status）白做**，而且正好打在"反照骗"的招牌上。

    另一个容易忽略的点：模型对工具输出是"照抄不疑"的。所以这种句子必须
    由数据决定，不能由写代码的人顺手决定。
    """
    template = _TOTAL_PHRASE.get(stats.get('data_status') or '', _DEFAULT_TOTAL_PHRASE)
    return template.format(
        name=stats['resort_name'], total=stats['summary_total'],
        counts='、'.join(f'{level} {num} 条'
                        for level, num in stats['summary_counts'].items()))


def _resort_id(resort: str, query: str) -> str:
    """工具参数可能是档案 id，也可能是中文名；对不上就从问题里认"""
    from rag.parser import ResortRegistry
    key = (resort or '').strip()
    if key in set(ResortRegistry.name_map().values()):
        return key
    return ResortRegistry.name_map().get(key) or ResortRegistry.detect(query) or ResortRegistry.detect(key)


def _media_evidences(query: str, resort: str) -> list[Evidence]:
    """把策展链接写成可引用证据。只在问视频、照片、雪道图时附上，且不经过向量库。"""
    from service.media_service import media_for_query
    items = media_for_query(_resort_id(resort, query), query)
    evidences = []
    for item in items:
        checked = item.verification == 'content_checked'
        scope = '已核对内容' if checked else '未观看内容，只核对了页面或标题，不能写成已验证的推荐'
        kind = {'video': '视频', 'photo': '图文', 'map': '雪道图'}.get(item.kind, item.kind)
        evidences.append(Evidence(
            content=(f'{kind}链接《{item.title}》：{item.url}。来源：{item.source}。'
                     f'核对日期：{item.checked_at}。{scope}。说明：{item.notice}'),
            source_type='C', source=item.source, date=item.checked_at,
            doc_type='video' if item.kind == 'video' else 'photo',
            resort_id=item.resort_id))
    return evidences


_LEVEL_LABEL = {'beginner': '新手', 'intermediate': '进阶', 'advanced': '老手'}


def level_notes(resort_id: str) -> list[tuple[str, str]]:
    """分层建议原文（YAML）。问「新手先滑哪条」时，明细表里的另一条初级道不能顶替这里点名的走法。"""
    import yaml
    from pathlib import Path
    path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        return []
    if not isinstance(data, dict):
        return []
    name = str(data.get('name') or resort_id)
    reviewed = str((data.get('meta') or {}).get('last_reviewed_at') or '')
    notes = []
    by_level = data.get('by_level') or {}
    if not isinstance(by_level, dict):
        return []
    for level, info in by_level.items():
        if not isinstance(info, dict) or not str(info.get('summary') or '').strip():
            continue
        text = f'{name}对{_LEVEL_LABEL.get(str(level), level)}的建议：{info["summary"]}'
        tips = [str(tip) for tip in info.get('tips') or [] if tip]
        if tips:
            text += ' 注意：' + '；'.join(tips) + '。'
        if str(level) == 'beginner':
            text += ' 问「新手先滑哪条」时优先采用本段点名的雪道和走法，不要改推明细里另一条初级道。'
        notes.append((text, reviewed))
    return notes


@tool
def search_resort_kb(query: str, resort: str = '', level: str = '', board: str = '') -> str:
    """检索雪场知识库：地理位置与地址、雪道体验与难度、缆车设施、开放时间、避坑经验、教学视频。

    何时调用：用户问雪场"在哪""在哪里""哪个城市""地址"，或询问雪道/难度/设施/注意事项，或"新手适合吗""怎么练换刃"这类
    需要依据雪场资料回答的问题。
    何时不调用：纯寒暄、与雪场无关的闲聊、已经在本轮对话中检索过的同一问题；
    问"有几条高级道""雪票多少钱"这类精确统计/价格问题，用 get_resort_runs / get_resort_price。

    Args:
        query: 检索问题，尽量保留用户原话中的雪场名与专有名词（如"金龙道""面条雪"）
        resort: 雪场 id，如 wanlong / taiwu；不确定时留空
        level: 用户水平 beginner / intermediate / advanced；留空表示不限制
        board: 单双板 snowboard / ski；留空表示不限制
    返回：带来源编号的检索证据（含 A官方/B实拍/C经验、日期、保鲜期），回答时请引用编号。
    问视频、照片或雪道图时，返回里会多出「链接」条目：只转述这些标题和链接，
    写了「未观看」的不要说成已验证推荐。回答每项都须引用 [E编号]，最多选 3 项，原帖 URL 由下方卡片展示。
    """
    docs = retrieve(query, resort=resort, level=level, board=board,
                    top_n=settings.RERANK_TOP_N)
    media = _media_evidences(query, resort)
    if not docs and not media:
        unknown = detect_unknown_resort(query)
        if unknown:
            from rag.parser import list_resort_ids, resort_name_map

            covered = '、'.join(sorted({name for name in resort_name_map()}))
            return (f'本库暂未收录「{unknown}」，目前只收录了：{covered}。'
                    '请如实告知用户该雪场暂无资料，不要用其他雪场的数据代替。')
        return ('未检索到相关资料。请如实告知用户"暂无可靠来源"，'
                '并建议其查看官方渠道或等待实拍更新，不要编造。')
    # 链接放在前面：便宜模型问「有什么视频」时，若链接埋在检索结果后面，就会空答然后被降级
    return format_block(media + [from_document(doc) for doc in docs])


def _raw_difficulty_lines(stats: dict) -> list[str]:
    """来源用了复合分级时，把"归并动作"说出来

    为什么必须有这一段：北大湖官网的级别是 初级道/初中级道/中级道/中高级道/高级道/连接道 六类，
    本库为了分层过滤归并成 初/中/高三桶。若不说明，
    用户拿官网一对（官网写"高级道 5 条"、我们答"高级道 10 条"）就会以为我们在编数字。
    **归并可以做，但必须让用户看得见。**
    """
    raw_counts = stats.get('raw_counts') or {}
    if not any(label not in _STANDARD_DIFFICULTIES for label in raw_counts):
        return []
    breakdown = '、'.join(f'{label} {num} 条' for label, num in raw_counts.items())
    return [
        f'本库按**来源原始级别**收录：{breakdown}。'
        '分层过滤时做过归并（初中级道→新手、中高级道→老手、连接道不计入分级），'
        '因此"按分层口径的条数"与"来源原始级别的条数"可能不同；'
        '回答时请把两个口径都讲清楚，不要让用户以为数字对不上。'
    ]


# 标准三级（来源直接用这套分级时，不需要额外解释归并动作）
_STANDARD_DIFFICULTIES = {'初级', '中级', '高级', '初级道', '中级道', '高级道'}


def _run_total_lines(stats: dict) -> list[str]:
    """拼"雪道条数"那几句：**不同来源说法不一致时必须把分歧说出来**

    为什么不能挑一个报：挑一个就等于替数据做取舍，而用户会把那个数当成权威结论。

    三个数必须分清（踩过坑：把"来源声明的分难度合计"说成"本库已收录明细"，
    而本库其实一条明细都没有——将军山就是这种形状）：
        official_total  某个来源声明的雪道总数（可能是官网，也可能是百科/聚合页）
        summary_total   另一个来源声明的**分难度合计**（来自 YAML 的 runs_summary）
        detail_total    本库**真正收录的逐条明细**条数
    所以措辞一律用"某来源称/分难度合计/本库收录明细"，不写"官网称"——
    除非确实来自官网（那由证据里的来源串说明）。
    """
    declared_total = stats.get('official_total')
    summary_total = stats.get('summary_total')
    detail_total = stats.get('detail_total') or 0

    if declared_total and summary_total and declared_total != summary_total:
        counts = '、'.join(f'{level} {num} 条'
                          for level, num in stats['summary_counts'].items())
        # 差额的方向不同，原因也不一样，别用一句话糊过去：
        #   声明总数 < 分难度合计（万龙 22 < 32）：那个口径更窄，如只计国际标准/压雪雪道
        #   声明总数 > 分难度合计（北大湖 74 > 34 / 将军山 61 > 48）：那个口径更宽，
        #   如含规划中/未开放雪道、野雪地形，也可能分难度那份统计本身不全
        if declared_total < summary_total:
            guess = '前者口径可能更窄（例如只计国际标准/压雪雪道）'
        else:
            guess = ('前者口径可能更宽（例如含规划中或未开放的雪道、野雪地形），'
                     '也可能分难度那份统计本身不全')
        return [
            f"{stats['resort_name']}的雪道条数存在**两个口径**："
            f"一个来源称 {declared_total} 条（未给各难度条数）；"
            f"另一个来源的分难度合计为 {summary_total} 条（{counts}）。"
            f"两者相差 {abs(declared_total - summary_total)} 条，{guess}。"
            f"本库目前**收录明细 {detail_total} 条**。"
            f"回答时把两个口径都告诉用户，不要只报一个数，也不要断言哪个是「总数」。"
        ]
    if summary_total:
        # 只有一个口径时，措辞按 data_status 分档（二手转引不许说"官方公布"）
        return [_total_sentence(stats)]
    if declared_total:
        # 只有总数、没有分难度条数（松花湖就是这种：运营方只给了总数与总长度）
        # 这时候**总数不能丢**——丢了就变成"什么都没查到"，用户白问一次
        return [
            f"{stats['resort_name']}的雪道总数：有来源称 {declared_total} 条，"
            f"但**未公布各难度条数**；本库目前收录明细 {detail_total} 条。"
            '回答"高级道有几条"这类问题时只能说总数，不能给分级数字，也不要自己拆分。'
        ]
    return []


@tool
def get_resort_runs(resort: str, difficulty: str = '') -> str:
    """查询雪场的雪道数量与清单（结构化统计，来自 MySQL）。

    何时调用：用户问"有几条高级道""雪道多不多""初级道够不够新手练""有哪些雪道"，
    或需要按难度统计雪道时。
    何时不调用：用户问缆车/索道数量或长度时，不得用雪道统计替代，应使用 get_resort_lifts 查询缆车档案；没有缆车表就说明本库缺失，不表示现实没有缆车。
    用户问"金龙道难不难""雪道体验怎么样"也属于检索(search_resort_kb)。
    问新手先滑哪条时，本工具会附上分层建议；回答请优先引用「对新手的建议」，不要只挑清单里坡度最低的一条。

    Args:
        resort: 雪场 id 或中文名，如 wanlong / 万龙 / 万龙滑雪场
        difficulty: 可选，只列某一难度：初级 / 中级 / 高级；留空返回全部
    返回：各难度雪道数量 + 雪道清单，带来源与日期。数量与来源分歧都必须附返回的 [E编号]，不能只解释而漏引用。
    """
    with SessionLocal() as session:
        stats = ResortService(session).get_run_stats(resort, difficulty)
    if not stats:
        return (f'未收录「{resort}」的雪道数据。请如实告知用户该雪场暂未收录，'
                '不要编造雪道数量。')

    lines = ['以下仅为雪道统计，不能据此回答缆车或索道的数量、长度。']
    notes = level_notes(stats.get('resort_key') or '')
    if notes:
        lines.append('若用户在问新手或第一次该滑哪条：优先引用后面「对新手的建议」里点名的雪道和走法，'
                     '不要改挑明细里另一条初级道。')
    status = stats.get('data_status') or ''
    lines.extend(_run_total_lines(stats))

    lines.extend(_raw_difficulty_lines(stats))
    detail = '、'.join(f'{level} {num} 条' for level, num in stats['detail_counts'].items())
    if stats['summary_total'] and stats['summary_total'] == stats['detail_total']:
        # 来源声明的总数与已收录明细刚好一致（整份雪道表都录进来了），不要重复说两遍
        lines.append(f"本库已收录全部 {stats['detail_total']} 条明细，"
                     '字段含长度/最高海拔/平均坡度/最大坡度。')
    elif stats['summary_total'] and stats['detail_total'] > stats['summary_total']:
        # 明细比分级总数还多：多出来的通常是不属于初/中/高三级的地形（连接道、野雪区），
        # 把它们说成"未收录的雪道"就错了——北大湖就是这种形状
        lines.append(f"本库已收录明细 {stats['detail_total']} 条（{detail}）；"
                     f"其中 {stats['detail_total'] - stats['summary_total']} 条不属于"
                     '初/中/高三级（如连接道、野雪地形），未计入上面的分级统计。')
    else:
        lines.append(f"本库已收录明细 {stats['detail_total']} 条（{detail}），"
                     '未收录的雪道暂无可引用细节。')
    if stats.get('data_status_note'):
        lines.append(f"数据来源说明：{stats['data_status_note']}。")
    evidences = [Evidence(
        # 置信级别跟数据走（service 从雪道行里带出来），不在工具层写死 'A'
        content=''.join(lines), source_type=stats.get('source_type') or 'A',
        source=stats['source'], date=stats['date'], doc_type='run',
        data_status=status)]
    for run in stats['runs']:
        parts = [f"{stats['resort_name']}的雪道「{run['name']}」：难度{run['difficulty']}"]
        if run['length_m']:
            parts.append(f"长度约 {run['length_m']} 米")
        if run['vertical_m']:
            parts.append(f"落差约 {run['vertical_m']} 米")
        if run.get('top_altitude_m'):
            parts.append(f"最高海拔约 {_num(run['top_altitude_m'])} 米")
        if run.get('avg_slope_deg'):
            parts.append(f"平均坡度约 {_num(run['avg_slope_deg'])} 度")
        if run.get('max_slope_deg'):
            parts.append(f"最大坡度约 {_num(run['max_slope_deg'])} 度")
        if run['note']:
            parts.append(str(run['note']))
        evidences.append(Evidence(
            content='，'.join(parts) + '。', source_type=run['source_type'],
            source=run['source'], date=run['date'], doc_type='run',
            level=run['level'], data_status=status))
    for text, reviewed in notes:
        evidences.append(Evidence(
            content=text, source_type='C',
            source=f"{stats['resort_name']}分层建议（雪道表与公开攻略整理，走法属经验）",
            date=reviewed, doc_type='tip'))
    return format_block(evidences)


@tool
def get_resort_lifts(resort: str) -> str:
    """查询单座雪场策展档案的索道/缆车/拖牵/魔毯及长度、运力、服务范围。

    缆车数量、名称、长度问题优先调用本工具，不调用雪道统计。
    空表只表示本库暂无档案，不表示现实没有缆车；必须引用返回的编号。
    本工具不提供实时开放状态，未知雪场不得借用其他雪场资料。

    Args:
        resort: 单座雪场 id 或完整名称/别名，如 taiwu / 太舞
    """
    from pathlib import Path
    import yaml
    from rag.parser import ResortParser, ResortRegistry

    term = resort.strip()
    ids = ResortRegistry.list_resort_ids()
    resort_id = term if term in ids else ResortRegistry.name_map().get(term, '')
    if not resort_id:
        return '本库未识别该雪场，请提供单座雪场的准确名称；不得借用其他雪场缆车资料。'
    path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    name = data.get('name') or resort_id
    if not data.get('lifts'):
        return '资料缺失说明也是基于档案的结论，回答必须引用下方来源编号；不得省略引用。\n' + format_block([Evidence(
            content=f'{name}本库档案暂未收录索道/缆车/魔毯表，无法提供各条名称与长度。'
                    '这是本库资料缺失，不代表现实没有缆车；不能用雪道数量或长度替代。',
            source_type='C', source=f'本库策展档案 {resort_id}.yaml 的 lifts 字段检查（非官方运营资料）',
            date=str((data.get('meta') or {}).get('last_reviewed_at') or ''),
            doc_type='lift', resort_id=resort_id)])
    docs = [doc for doc in ResortParser.parse_resort(resort_id)
            if doc.metadata.get('doc_type') == 'lift']
    if not docs:
        return '缆车档案未能解析，暂无可用证据，不得推测。'
    return '以下仅是本库已收录条目，不保证全量或实时开放；缺失字段不可推测。\n' + format_block(
        [from_document(doc) for doc in docs])


def _price_gap_reply(resort: str, item: str, mode: str) -> str:
    """没有对应价格时的工具回复，并附上 YAML 里已经写明的相关避坑"""
    from rag.parser import ResortRegistry

    if mode == 'board_gap':
        lead = (f'本库已收录「{resort}」的价格，但没有与「{item}」对应的板型价格行。'
                '不得用另一种板型的价格代替，不得把缺失费用当成 0。')
    else:
        lead = (f'本库已收录「{resort}」，但没有与「{item}」对应的价格行。'
                '不得用其他类别的价格补上，不得把缺失费用当成 0。')
    marks = []
    for word, extras in (
        ('双板', ('双板',)),
        ('单板', ('单板',)),
        ('住宿', ('住宿', '房价', '酒店')),
        ('酒店', ('酒店', '房价')),
        ('房价', ('房价',)),
        ('接驳', ('接驳',)),
        ('教练', ('教练',)),
        ('雪票', ('雪票',)),
    ):
        if word in item:
            marks.extend(extras)
    related = [row['text'] for row in ResortRegistry.curated_tips(resort)
               if any(word in row['text'] for word in marks)]
    if related:
        lead += '\n已核验的相关说明：' + '\n'.join(related[:2])
    return lead


@tool
def get_resort_price(resort: str, item: str = '') -> str:
    """查询雪场价格：雪票、装备租赁、教练课（数据来自 MySQL，带录入日期与保鲜期）。

    何时调用：用户问"多少钱""贵不贵""预算够吗"，或生成行程卡预算表时需要。
    何时不调用：用户只是泛泛问"性价比高吗"——先检索避坑与雪道信息再判断。

    Args:
        resort: 雪场 id 或中文名，如 wanlong / 万龙
        item: 可选，指定查询项（如"全天雪票""单板套装租赁"）；留空返回该雪场全部价格
    返回：价格 + 录入日期；超过 90 天会标注"可能已过期"，回答时必须提醒用户。
    """
    broadened = False
    narrowed = False
    with SessionLocal() as session:
        service = ResortService(session)
        prices = service.get_prices(resort, item)
        if not prices and item.strip():
            catalog = service.get_prices(resort)
            narrowed_rows, mode = narrow_prices(catalog, item)
            if mode in ('board_gap', 'category_gap'):
                return _price_gap_reply(resort, item, mode)
            if mode == 'category':
                prices = narrowed_rows
                narrowed = True
            else:
                prices = catalog
                broadened = bool(prices)
    if not prices:
        from rag.parser import ResortRegistry
        if resort.strip() in ResortRegistry.list_resort_ids() or ResortRegistry.name_map().get(resort.strip()):
            return (f'本库已收录「{resort}」雪场档案，但暂无价格数据或对应价格项。'
                    '不得说整座雪场未收录，不得用其他雪场或其他项价格补答。')
        return (f'未查询到「{resort}」的价格数据（可能该雪场未收录，或价格项名称不匹配）。'
                '请如实告知用户"暂无可靠来源"，不要编造价格。')

    evidences = [
        Evidence(
            content=price_evidence_text(p['resort_name'], p, p['updated_at']),
            source_type=p['source_type'], source=p['source'],
            date=p['updated_at'], expire_at=p['expire_at'], doc_type='price',
            data_status=p.get('data_status') or '')
        for p in prices
    ]
    # 来源说明随价格一起给：像"来源页已改版成新雪季、本库价格可能滞后"这种话，
    # 不挂在这里就永远传不到回答里（价格是本项目最需要提示时效的一类数据）
    note = prices[0].get('data_status_note') or ''
    if note:
        evidences.append(Evidence(
            content=f"{prices[0]['resort_name']}价格来源说明：{note}",
            source_type='C', source='本库来源登记', date=prices[0]['updated_at'],
            doc_type='price', data_status=prices[0].get('data_status') or ''))
    block = format_block(evidences)
    notice = reference_price_notice(prices)
    if notice:
        block = notice + '\n' + block
    if narrowed:
        block = ('价格项名称未精确匹配。下面只保留同一雪场里类别相符的价目，不代表请求项已命中。'
                 '请按条目原名回答；没有对应板型或类别就说暂无，不要用其他板型或其他项代替。\n' + block)
    elif broadened:
        block = ('价格项名称未精确匹配；下面是同一雪场的候选价目，不代表请求项已命中。'
                 '请按原价目名称核对全天/时段、平日/周末、自带板/含雪具；没有对应项就说暂无，勿拿其他项替代。\n' + block)
    return block


@tool
def set_trip_info(date: str = '', days: str = '', people: str = '',
                  budget: str = '', resort: str = '', board: str = '',
                  runtime: ToolRuntime[TripState, UserContext] = None) -> Command:
    """记录行程要素到状态机（不是长期画像，只对本次行程有效）。

    何时调用：用户给出或修改行程信息时——"下周末去""玩 3 天""两个人""预算 3000"
    "想去万龙""我玩单板"。只要用户说出行程要素，就先调用它记录，再回答。
    何时不调用：用户说"我晋级了/我以后都玩单板了"——那是长期画像，用 update_profile。

    ⚠️ **一次把用户已经说出的所有要素都填上**（2026-10-05 改）：
        原来一次只收一个字段，于是"2 人周末去万龙玩两天、预算 2000、单板"这种一句话
        要调 **5 次**工具 = 5 轮"模型 + 工具"，而每轮都要把**不断变长的上下文重发一遍**。
        实测"排行程"输入 token 达 **81,973**（是简单问价的 6.5 倍）、耗时 44 秒、约 ¥0.12/次。
        改成一次提交多项后：输入 token 降到 **23,314（-72%）**、耗时 21 秒。

    为什么是**一堆可选字符串参数**而不是一个 dict：
        实测做成 `updates: dict[str, str]` 时，GLM 仍然一次只填一个键（还多调了几次）。
        嵌套对象的 schema 对模型最不友好；扁平字符串参数最稳，且模型能一次填全。

    Args:
        date: 出行日期（如"周末""2026-12-20"）
        days: 滑几天（如"2"）
        people: 几个人（如"2"）
        budget: 人均预算（如"2000"，可带"元"）
        resort: 雪场（如"万龙"）
        board: 单板 / 双板
    返回：记录结果与生成行程时所缺信息。仅记录条件不等于开始规划；用户未要求方案时不要追问缺失行程项。
    """
    updates = {'date': date, 'days': days, 'people': people,
               'budget': budget, 'resort': resort, 'board': board}
    updates = {key: value for key, value in updates.items()
               if str(value or '').strip()}

    if not updates:
        return Command(update={'messages': [ToolMessage(
            '没有收到任何行程要素。请把用户已说出的项填进对应参数（date/days/people/'
            'budget/resort/board），可一次填多项。',
            tool_call_id=runtime.tool_call_id)]})

    # 用当前 state 合并出"记录后的状态"，据此判断信息是否齐全。
    # 逐项解析：某一项格式不对不影响其余项入库（原来整条失败，用户得重说一遍）。
    merged = dict(runtime.state or {})
    ok_fields: list[str] = []
    recorded: list[str] = []
    failed: list[str] = []
    for field, value in updates.items():
        label, _ = TRIP_FIELDS[field]
        try:
            parsed = parse_trip_value(field, str(value))
        except ValueError as exc:
            failed.append(f'{label}（{exc}）')
            continue
        merged[field] = parsed
        ok_fields.append(field)
        recorded.append(f'{label}：{parsed}')

    if not recorded:
        return Command(update={'messages': [ToolMessage(
            '记录失败：' + '；'.join(failed) + '。请把数字换算成阿拉伯数字再试。',
            tool_call_id=runtime.tool_call_id)]})

    missing = missing_fields(merged)
    hint = '行程字段已齐全；仅在用户要求方案时继续生成。' if not missing else \
        '生成行程时还缺：' + '、'.join(FIELD_LABELS[m] for m in missing) + '。仅在用户要求行程方案时追问 1-2 项；否则回应当前问题，不启动行程采集。'
    message = '已记录 ' + '；'.join(recorded) + '。' + hint
    if failed:
        message += f'（未记录：{"；".join(failed)}）'

    update: dict = {field: merged[field] for field in ok_fields}
    update['messages'] = [ToolMessage(message, tool_call_id=runtime.tool_call_id)]
    return Command(update=update)


@tool
async def get_weather(city: str, days: int = 3) -> str:
    """查询雪场所在地区的天气（实况 + 未来预报 + 气象预警），数据来自百度地图 MCP。

    何时调用：用户问"这周去冷不冷""最近天气怎么样""会不会下雪""缆车会不会停运"，
    或生成行程卡时判断体感与雪质。
    何时不调用：用户问的是往年同期雪况或历史雪况——那属于知识库内容。

    Args:
        city: 城市/地区名，如"崇礼""张家口""乌鲁木齐"；也可以用雪场所在区县名
        days: 预报天数，默认 3（接口最多返回 5 天）
    返回：实况 + 逐日预报 + 气象预警，带数据更新时间。
        必须说明"这是天气预报，不是实时雪况"；有预警时要主动提醒。
    """
    # 1.把人类说法翻译成接口要的行政区划码（模型不可能记住"崇礼=130709"）
    adcode = await resolve_adcode(city)
    if not adcode:
        return (f'查不到「{city}」这个地区，无法获取天气。'
                '请如实告知用户，并建议其提供更具体的城市名。')

    # 2.调天气接口
    raw = await call_mcp('map_weather', {'district_id': adcode})

    # 3.摘成可读文本并套证据信封（带数据更新时间，天气是最讲究时效的东西）
    body = BaiduMapMcp.format_weather(raw, days=days)
    return McpEvidence.wrap(body, source='百度地图 MCP·天气（底层为气象部门数据）')


@tool
async def get_route(origin: str, destination: str, mode: str = 'driving') -> str:
    """查询两地之间的出行路线（距离 / 预计耗时 / 过路费），数据来自百度地图 MCP。

    何时调用：用户问"怎么去""要开多久""多远""过路费多少"，
    或生成行程卡时估算交通时间与成本。
    何时不调用：用户只是问雪场在哪个城市——那用 search_resort_kb。

    Args:
        origin: 出发地，如"郑州""北京西站"
        destination: 目的地，如"崇礼万龙滑雪场""丝绸之路国际滑雪场"
        mode: 出行方式 driving（驾车，默认）/ riding（骑行）/ walking（步行）
    返回：里程、预计耗时、过路费与主要途经路段，带来源。
        必须说明这是规划路线，实际以导航实时路况为准。
    """
    raw = await call_mcp('map_directions', {
        'origin': origin, 'destination': destination, 'model': mode or 'driving'})
    body = BaiduMapMcp.format_route(raw)
    if '驾车路线' not in body:
        return (f'没查到从「{origin}」到「{destination}」的路线。'
                '请如实告知用户，并建议其确认起终点写法。')
    return McpEvidence.wrap(body, source='百度地图 MCP·路线规划')


@tool
async def search_nearby(keyword: str, region: str) -> str:
    """检索某地周边的地点（雪具店 / 租赁 / 住宿 / 餐饮），数据来自百度地图 MCP。

    何时调用：用户问"雪场附近有没有租装备的""住哪儿方便""附近有什么吃的"。
    何时不调用：用户问雪场本身的设施——那属于知识库内容，用 search_resort_kb。

    Args:
        keyword: 检索关键词，如"雪具租赁""酒店""餐厅"
        region: 检索范围，填城市或区县名，如"崇礼"
    返回：地点列表（名称/地址/评分），带来源。评分类信息须说明来自地图平台用户评价。
    """
    raw = await call_mcp('map_search_places', {'query': keyword, 'region': region})
    body = BaiduMapMcp.format_places(raw)
    return McpEvidence.wrap(body, source='百度地图 MCP·地点检索')


@tool
def update_profile(field: str, value: str,
                   runtime: ToolRuntime[TripState, UserContext]) -> Command:
    """更新用户画像（长期记忆，跨会话生效）。

    何时调用：用户说"我晋级了""我现在玩单板了""我预算改了"等，明确要更新自己的信息时。
    何时不调用：用户只是随口提到某次行程的临时信息——那属于 TripState，不要写进画像。

    Args:
        field: 字段名，可取 level（新手/进阶/老手）、board_type（单板/双板）、
               home_region（常活动区域）、budget_band（预算带）
        value: 新的取值
    返回：确认信息（由 Command 写回状态与消息）
    """
    if field not in PROFILE_FIELDS:
        return Command(update={'messages': [ToolMessage(
            f'不支持的画像字段：{field}（可用：{"、".join(PROFILE_FIELDS)}）',
            tool_call_id=runtime.tool_call_id)]})

    uid = getattr(runtime.context, 'uid', None) or 1   # 命令行调试时没有登录用户，落到 1
    with SessionLocal() as session:
        profile = ProfileService(session).upsert(uid, {field: value})
        session.commit()
    return Command(update={'messages': [ToolMessage(
        f'已更新画像：{PROFILE_FIELDS[field]} → {value}。当前画像：{profile}',
        tool_call_id=runtime.tool_call_id)]})


# 注册给 create_agent 的工具清单
# 本地资料查询 + 外部实时数据（百度地图 MCP）+ 状态写入
TOOLS = [search_resort_kb, get_resort_runs, get_resort_price, get_resort_lifts,
         get_weather, get_route, search_nearby,
         set_trip_info, update_profile]
