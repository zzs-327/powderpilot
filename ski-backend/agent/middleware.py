"""中间件：把"可信"从提示词变成机制。

对应计划书 v0.4 第 5.5 节。三个钩子：
    1. evidence_envelope (wrap_tool_call)  工具返回值重编号，保证全轮编号唯一
    2. citation_check    (after_model)     回答里的引用与数字必须在证据范围内
    3. trim_history      (before_model)    长对话裁剪，控制上下文长度

为什么是中间件而不是提示词：
    提示词里写"请引用来源"是"请求"，模型可以不听；
    中间件是"机制"，工具输出结构 + 后置校验，绕不过去。

------------------------------------------------------------
引用校验做到哪一层（这是本文件最需要看懂的设计）

只校验"[E3] 在不在白名单里"是**不够的**：
    模型编一条"雪票 380 元"，照样可以给它标上 [E1]，编号校验完全放行。
    所以这里做了两层：
        第一层 编号白名单 —— 抓"引用了不存在的来源"
        第二层 事实级校验 —— 抓"数字根本不是证据里的"，这才是真正防幻觉的那层

编号为什么要重修：
    原来每个工具各自从 [E1] 开始编号，一轮里同时调 search_resort_kb 和
    get_resort_price，两批证据都叫 E1/E2，"白名单"就退化成取并集、形同虚设。
    现在按工具分配"编号段"（搜索 E1xx / 统计 E2xx / 价格 E3xx），
    全轮唯一，而且**不需要共享计数器**——并行工具调用不会争抢同一个 state key
    （这一点是照着重踩过的坑设计的：state 里的派生字段在并行写时会触发
    InvalidUpdateError，见 agent/state.py 的注释）。
------------------------------------------------------------
"""
from __future__ import annotations

import re
from typing import Any, Callable
from uuid import uuid4

from langchain.agents.middleware import after_model, before_model, wrap_tool_call
from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage

from config.config import settings
from utils.log import LogUtil

# 引用校验的留痕 logger：同时进控制台与 logs/backend.log
# （见 utils/log.py 的注释：诊断只 print 到控制台，等于没留证据）
logger = LogUtil.get_logger('citation')

# 每个工具独占一个证据编号段：search -> E101 起，runs -> E201 起，依此类推。
# 为什么用"段"而不是全轮递增计数器：并行工具调用时拿不到确定的全局序号，
# 而按工具分段是纯函数式的，谁先谁后都不冲突。
EVIDENCE_BLOCK = {
    # 本地资料库（MySQL + Chroma）
    'search_resort_kb': 100,
    'get_resort_runs': 200,
    'get_resort_price': 300,
    'get_resort_lifts': 600,
    # 外部实时数据（百度地图 MCP，见 agent/mcp_tools.py）
    'get_weather': 400,
    'get_route': 410,
    'search_nearby': 420,
}
DEFAULT_BLOCK = 500

# 证据头形如：[E101] (A 官方｜来源｜日期｜可信度)
_EVIDENCE_HEAD = re.compile(r'\[E(\d+)\]')
# 回答里的引用，形如 [E101] 或 [E101][E103]
_CITE = re.compile(r'\[E(\d+)\]')
# 回答里"带单位的事实数字"——只查这几种单位：
#   元 价格 / 条 雪道数量 / 度 坡度 / 米 长度
# 不查"天/人/年"是因为那些多来自用户原话与行程推算，误报率高。
# 日元必须写在「元」前面，否则「5000日元」会被看成 5000 元
_FACT_NUM = re.compile(r'(\d+(?:\.\d+)?)\s*(日元|元|条|度|米)')

# 书名号里的作品名：模型"推荐一部滑雪电影"时给的就是这类内容，
# 一个价格数字都没有，只查数字会整段漏掉
_TITLE = re.compile(r'《[^》]{1,40}》')

# 中文数字：用户说"三个人""玩两天"时一个阿拉伯数字都没有，
# 不认它就无法还原"单价 × 人数"这种正当推演
_CN_NUMERAL = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5,
               '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

# 重生成标记：靠它判断"本轮是否已经重试过"，避免再加一个 state 字段
RETRY_MARK = '【引用校验未通过】'

# 降级话术：两次都没过，就宁可说"暂无可靠来源"
DEGRADE_REPLY = ('抱歉，这条回答没能通过来源校验，我不能确认它的准确性。'
                 '你可以换个问法，或告诉我具体雪场，我再查一次。')

# 允许参与"同单位加减乘"推算的单位。
# 刻意不含"条"：雪道条数在证据里写全了，推算只会放大巧合（19-4=15 会放过编造的"15 条"）
# 日元只核对原文，不参与加减乘。否则 5000+5000 会被放行成「10000 日元」总价。
_ARITHMETIC_UNITS = ('元', '度', '米')

# 事实级校验的规模上限（防止组合爆炸）
_MAX_DERIVE_INPUT = 12
_MAX_DERIVE_SIZE = 6000
#: "两项乘积之和"参与推导的乘积个数上限（两两相加 → 最多 n²/2 个数）
_MAX_DERIVE_SUM_INPUT = 40


# ---------------- 工具函数 ----------------

def _text_of(message) -> str:
    """取消息纯文本（兼容 content 是字符串或内容块列表两种形态）"""
    content = getattr(message, 'content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get('text'), str):
                parts.append(block['text'])
        return ''.join(parts)
    return ''


def _numbers_in(text: str) -> set[float]:
    """抽出文本里所有数字（含小数）"""
    values: set[float] = set()
    for raw in re.findall(r'\d+(?:\.\d+)?', text or ''):
        try:
            values.add(float(raw))
        except ValueError:
            continue
    return values


def _fact_pairs(text: str) -> set[tuple[float, str]]:
    """抽出文本里的 (数值, 单位) 配对

    校验必须带单位，否则"证据里有 15 度"会被拿去给"15 条"背书。
    """
    pairs: set[tuple[float, str]] = set()
    for raw, unit in _FACT_NUM.findall(text or ''):
        try:
            pairs.add((float(raw), unit))
        except ValueError:
            continue
    return pairs


def _strip_dates(text: str) -> str:
    """去掉 YYYY-MM-DD 日期

    为什么要去：证据里到处是"录入日期 2026-04-06"，其中的 04/06 会被当成
    普通数字参与组合，凑出 9+6=15 这种巧合值，把校验放得过松。
    """
    return re.sub(r'\d{4}-\d{2}-\d{2}', ' ', text or '')


def _normalize_units(text: str) -> str:
    """把同义单位收成校验用的写法

    天气证据是「22℃」，回答常写成「22 度」。价格证据是「5000 JPY」，
    回答常写成「5000日元」。不归一会被当成证据里没有这个数。
    日元必须整词替换，不能把「日元」里的「元」当成人民币。
    """
    text = re.sub(r'℃|°C|°', '度', text or '')
    return re.sub(r'JPY', '日元', text, flags=re.IGNORECASE)


def _counts_in(text: str) -> set[int]:
    """抽小整数（1~20），包含中文数字

    为什么必须认中文数字：用户说的是"三个人""玩两天"，一个阿拉伯数字都没有。
    不认它，模型算出的"1680 = 560×3"就会被误判成编造。
    """
    counts: set[int] = set()
    for raw in re.findall(r'\d+', text or ''):
        try:
            value = int(raw)
        except ValueError:
            continue
        if 1 <= value <= 20:
            counts.add(value)
    for char in re.findall(r'[一两二三四五六七八九十]', text or ''):
        value = _CN_NUMERAL.get(char)
        if value:
            counts.add(value)
    return counts


def _derive(evidence_pairs: set[tuple[float, str]],
            counts: set[int]) -> dict[str, set[float]]:
    """算出"可以由证据合法推出来"的数值，**按单位分开**

    核心规则：**同单位内允许加减乘，跨单位绝不合并**。
    这条规则是被两个真实误报/漏报逼出来的：

        漏报（该拦没拦）：证据里有 16 度、19 条，早期版本允许任意两数相加，
            16+19=35，于是编造的"35 条雪道"被放行了。
            → 跨单位相加没有意义，禁止。
        误报（该放没放）：模型算昼夜温差 19℃ − 4℃ = 15℃，
            这是正当推算，却因为没有"度"的推算规则被打回、最后降级成拒答。
            → 同单位的温差/合计是正常表述，必须放行。

    为什么"条"不参与推算：
        雪道条数在证据里本来就写全了（"共 32 条（初级 4 条、中级 9 条、高级 19 条）"），
        不需要推算；而允许推算只会放大巧合——19-4=15 会让编造的"高级道 15 条"过关。
    """
    by_unit: dict[str, list[float]] = {}
    for value, unit in evidence_pairs:
        by_unit.setdefault(unit, []).append(value)

    derived: dict[str, set[float]] = {}
    for unit in _ARITHMETIC_UNITS:
        values = sorted(set(by_unit.get(unit, [])))[:_MAX_DERIVE_INPUT]
        out: set[float] = set()
        for index, a in enumerate(values):
            for b in values[index:]:
                out.add(a + b)
                out.add(a * b)
                out.add(abs(a - b))       # 温差、价差这类差值
                if len(out) >= _MAX_DERIVE_SIZE:
                    break
        # 单价 × 人数 / 天数，以及**单价 × 人数 × 天数**：只对"元"开放
        # （预算合计是唯一真正常见的乘法场景）。
        #
        # ⚠️ 两连乘是 2026-10-05 补的，因为实测踩到一次**误伤**：
        #   回答写「2 人 × 2 天 × 650 元 = 2600 元」——算法完全正确，
        #   但旧规则只支持"单价 × 单个数量"（650×2=1300），推导不出 2600，
        #   于是整条回答被判"数字没有依据" → 重写 → 仍不合格 → 降级成拒答。
        #   用户看到的是"明明算对了却说没有可靠来源"，这比漏拦一次幻觉更伤体验。
        #   注意 counts 是**集合**，"2 人 2 天"里两个 2 只留一个，
        #   所以必须允许同一个数量用两次（下面的 c1/c2 取自同一个列表，含自身）。
        if unit == '元':
            count_list = sorted(counts)
            products: set[float] = set()
            for price in values:
                for count in count_list:
                    products.add(price * count)
                for index, first in enumerate(count_list):
                    for second in count_list[index:]:
                        products.add(price * first * second)
            out |= products
            # 合计 = 两项乘积之和（雪票 + 租赁 这种最典型的预算表述）。
            # 只做**两两**相加并设上限：更深层的组合会放大巧合，
            # 让"编造的数字恰好等于某个组合"过关——那是把误伤换成了漏拦。
            items = sorted(products)[:_MAX_DERIVE_SUM_INPUT]
            for index, a in enumerate(items):
                for b in items[index:]:
                    out.add(a + b)
                    out.add(abs(a - b))
        derived[unit] = out
    return derived


def _shift_indices(text: str, block: int) -> str:
    """把 [E1] 这种段内编号平移成 [E101] 这种全轮唯一编号"""
    return _EVIDENCE_HEAD.sub(lambda m: f'[E{block + int(m.group(1))}]', text)


def _renumber_result(result: Any, block: int) -> Any:
    """改写工具返回结果里的证据编号（ToolMessage / Command / 字符串都兼容）"""
    from langgraph.types import Command

    if isinstance(result, str):
        return _shift_indices(result, block)

    if isinstance(result, Command):
        update = getattr(result, 'update', None)
        if isinstance(update, dict):
            messages = update.get('messages')
            if isinstance(messages, list):
                for message in messages:
                    if isinstance(getattr(message, 'content', None), str):
                        message.content = _shift_indices(message.content, block)
        return result

    if isinstance(getattr(result, 'content', None), str):
        result.content = _shift_indices(result.content, block)
    return result


def _turn_messages(state) -> list:
    """取本轮消息：**含**最后那条用户消息，到当前为止

    为什么必须把用户消息也带上（这里踩过坑）：
        事实校验要放行"复述用户自己给的数字"——用户说"预算 3000"，
        模型答"预算 3000 元"不算编造。而这条放行靠的是遍历本轮里的
        human 消息。早先这里写的是 messages[last_human + 1:]，
        把用户那句话本身排除在外，于是放行逻辑在生产环境**从未生效**：
        用户报的预算、中文人数（"三个人"）全都取不到，
        正常的预算合计会被判成编造、甚至降级成拒答。
    """
    messages = list((state or {}).get('messages') or [])
    last_human = 0
    for index, message in enumerate(messages):
        if getattr(message, 'type', '') == 'human':
            last_human = index
    return messages[last_human:]


def _final_ai_message(turn: list):
    """取本轮最后一条"没有工具调用"的 AI 消息——那才是给用户看的答案"""
    for message in reversed(turn):
        if getattr(message, 'type', '') != 'ai':
            continue
        if getattr(message, 'tool_calls', None):
            return None
        return message
    return None


def _evidence_ids(messages: list) -> set[int]:
    """从工具消息里抽出证据编号（E101 / E201 / E401 …）"""
    return {int(x) for x in _EVIDENCE_HEAD.findall(
        '\n'.join(_text_of(m) for m in messages))}


def _lookback_tool_messages(state, turns: int) -> list:
    """取"此前 N 轮"的工具消息——跨轮引用不该被当成编造

    为什么需要（2026-09-12 实测）：
        引用校验的白名单原来只认本轮证据，而**模型的引用行为是跨轮连续的**：
        追问时它会把上一轮的编号一起带进表格。于是「先问有几条高级道 →
        再问那价格呢」这种最普通的对话，第二轮被判"引用了不存在的编号"，
        重写仍带着它 → 降级成拒答。

    为什么只回看最近 N 轮（settings.CITATION_LOOKBACK_TURNS），而不是整段会话：
        1. 证据基数越大，数字"巧合匹配"的机会越多，事实校验就越松；
        2. 数据更新后，几十轮前的旧证据不该继续给新回答背书——
           记忆是有保质期的（这条是踩过坑的教训）。

    参数 turns=0 表示关闭回看，退回到"只认本轮"的严格模式。
    """
    if turns <= 0:
        return []
    messages = list((state or {}).get('messages') or [])
    human_indices = [index for index, message in enumerate(messages)
                     if getattr(message, 'type', '') == 'human']
    if len(human_indices) < 2:
        return []                      # 第一轮，没有"上一轮"可言
    start = human_indices[max(0, len(human_indices) - 1 - turns)]
    end = human_indices[-1]            # 当前轮的起点，不含
    return [m for m in messages[start:end]
            if getattr(m, 'type', '') == 'tool']


def _quantity_objects(text: str) -> set[tuple[float, str]]:
    """只识别明确的条数对象，不做实体、否定或全文语义推断。"""
    # 长的写在前面：「中高级道」不能先被「中级」截走；证据里的「初级 7 条」也要认成雪道
    objects = (r'初级雪道|中级雪道|高级雪道|中高级道|初中级道|初级道|中级道|高级道|连接道'
               r'|缆车|索道|魔毯|雪道|初级|中级|高级')
    number = r'(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])'
    pairs: set[tuple[float, str]] = set()
    for match in re.finditer(number + r'\s*条\s*(' + objects + r')', text):
        if re.search(r'(?:不是|并非|不代表)\s*$', text[max(0, match.start() - 8):match.start()]):
            continue
        raw, obj = match.groups()
        pairs.add((float(raw), 'lift' if obj in {'缆车', '索道', '魔毯'} else 'run'))
    # 兼容“雪道共22条”，不越过分句，也不把“雪道资料：22条缆车”计作雪道。
    reverse = (r'(' + objects + r')[^\d，,。；;：:\n]{0,12}' + number
               + r'\s*条(?!\s*(?:' + objects + r'))')
    for match in re.finditer(reverse, text):
        obj, raw = match.groups()
        if re.search(r'(?:不是|并非|不代表)\s*$', text[max(0, match.start() - 8):match.start()]):
            continue
        if re.search(r'不是|并非|不代表', match.group(0)):
            continue
        pairs.add((float(raw), 'lift' if obj in {'缆车', '索道', '魔毯'} else 'run'))
    return pairs


def _check_quantity_objects(answer: str, evidence_text: str) -> list[str]:
    """仅拦同数字明确出现在相反对象、但目标对象无证据的情况。

    不检查跨雪场归属、金额对象或复杂否定，只针对雪道与缆车类条数。
    """
    evidence = _quantity_objects(evidence_text)
    problems = []
    for value, obj in sorted(_quantity_objects(answer)):
        opposite = 'run' if obj == 'lift' else 'lift'
        if (value, obj) not in evidence and (value, opposite) in evidence:
            label = '缆车/索道/魔毯' if obj == 'lift' else '雪道'
            problems.append(f'{value:g}条{label}（证据同数字仅支持另一数量对象）')
    return problems


def _check_facts(answer: str, evidence_text: str, turn: list) -> list[str]:
    """事实级校验：回答里带单位的数字，必须能在证据或用户本轮原话里站得住

    这是防幻觉的真正落点——编号可以随便标，数字编不出来。

    比对粒度是 **(数值, 单位) 配对**，不是光比数值。为什么：
        证据里银龙道写着"最大坡度约 15 度"，如果只比数值，
        模型编的"高级道 15 条"就会被误判成有据可依——
        数字确实出现过，但它是"度"不是"条"。带上单位才拦得住。

    放行条件（三者之一）：
        1. 证据原文里字面出现同样的"数值+单位"
        2. 用户本轮原话里出现过这个数值（模型复述用户给的预算/人数）
        3. 单位是"元"且能由证据里的单价推出来（合计、单价×人数）
    """
    evidence_clean = _normalize_units(_strip_dates(evidence_text))
    evidence_pairs = _fact_pairs(evidence_clean)
    counts = _counts_in(evidence_clean)

    human_numbers: set[float] = set()
    for message in turn:
        if getattr(message, 'type', '') == 'human':
            human = _strip_dates(_text_of(message))
            human_numbers |= _numbers_in(human)
            counts |= _counts_in(human)     # "三个人"里的 3 在这里被认出来

    derived = _derive(evidence_pairs, counts)

    problems: list[str] = _check_quantity_objects(answer, evidence_text)
    seen: set[str] = set()
    for raw, unit in _FACT_NUM.findall(_normalize_units(answer)):
        try:
            value = float(raw)
        except ValueError:
            continue
        decimals = len(raw.split('.')[1]) if '.' in raw else 0
        if _pair_ok(value, decimals, unit, evidence_pairs):   # 1.证据原文（含四舍五入）
            continue
        if value in human_numbers:               # 2.用户原话
            continue
        if value in derived.get(unit, set()):     # 3.同单位内可推算（温差/合计/单价×人数）
            continue
        key = f'{raw}{unit}'
        if key in seen:
            continue
        seen.add(key)
        problems.append(key)
    return problems


def _user_board(turn: list) -> tuple[str, str]:
    """本轮用户只点名一种板型时返回 (用户板型, 另一种)；两种都提或都没提返回 ('', '')"""
    human = next((m for m in reversed(turn) if getattr(m, 'type', '') == 'human'), None)
    text = _text_of(human) if human is not None else ''
    alpine, snowboard = '双板' in text, '单板' in text
    if alpine == snowboard:
        return '', ''
    return ('双板', '单板') if alpine else ('单板', '双板')


def _check_board_amounts(answer: str, evidence_text: str, turn: list) -> list[str]:
    """用户只问一种板型时，回答不得写出「只属于另一种板型价格行」的金额

    背景（2026-10-11 实测）：问「留寿都双板租赁一天多少钱」，回答给的是单板价。
    查价参数已由 _query_board_guard 纠正；这里是回答侧的第二道闸——
    模型拿到正确的工具结果后，仍可能把别的板型金额写进回答。

    判定（宁可漏报，不可误报）：
        1. 把证据按 [E编号] 切段，只含另一种板型词（不含用户板型词）的段，是「另一板型行」；
        2. 这些行里的 (金额, 单位)，扣掉在其他任何证据段、用户原话里也出现过的，才算「另一板型独有金额」；
        3. 回答里某个分句写出这个金额，且该分句没有出现另一种板型词（没有明说它是单板/双板价），才拦。
           明说「单板 22300 日元，不能代替双板」属于如实说明，放行。
    """
    mine, other = _user_board(turn)
    if not mine:
        return []
    evidence = _normalize_units(_strip_dates(evidence_text))
    heads = list(_EVIDENCE_HEAD.finditer(evidence))
    if not heads:
        return []
    segments = [evidence[m.start():(heads[i + 1].start() if i + 1 < len(heads) else len(evidence))]
                for i, m in enumerate(heads)]
    other_pairs: set[tuple[float, str]] = set()
    shared_pairs: set[tuple[float, str]] = set()
    for segment in segments:
        pairs = _fact_pairs(segment)
        if other in segment and mine not in segment:
            other_pairs |= pairs
        else:
            shared_pairs |= pairs
    human_numbers: set[float] = set()
    for message in turn:
        if getattr(message, 'type', '') == 'human':
            human_numbers |= _numbers_in(_strip_dates(_text_of(message)))
    exclusive = {p for p in other_pairs - shared_pairs if p[0] not in human_numbers}
    if not exclusive:
        return []
    problems: list[str] = []
    for clause in re.split(r'[。；;！？!?\n]', _normalize_units(answer)):
        if other in clause:
            continue
        for raw, unit in _FACT_NUM.findall(clause):
            try:
                pair = (float(raw), unit)
            except ValueError:
                continue
            key = f'{raw}{unit}（只见于{other}价格行）'
            if pair in exclusive and key not in problems:
                problems.append(key)
    return problems


def _pair_ok(value: float, decimals: int, unit: str,
             pairs: set[tuple[float, str]]) -> bool:
    """回答里的这个"数值+单位"，能不能在证据里站住

    除了字面相等，还要放过**四舍五入**：
        证据写"人均 166.8 元"，模型写"人均 167 元"——这是正常表述，不是编造。
        实测就是这条把 POI 回答连续两次打成"无依据"，最后降级成拒答。

    容差不是拍一个固定值，而是**按回答自身的小数位数去 round 证据值**，
    这样既放过 166.8→167，又拦得住凭空多出来的 168：
        round(166.8, 0) = 167  ✅ 放行
        round(166.8, 0) = 167 ≠ 168  ❌ 拦下
    """
    if (value, unit) in pairs:
        return True
    for evidence_value, evidence_unit in pairs:
        if evidence_unit == unit and round(evidence_value, decimals) == value:
            return True
    return False


def _factual_claims(answer: str, turn: list) -> list[str]:
    """找出回答里"具体事实"的痕迹——没有任何资料依据时，这些都不该出现

    什么算具体事实：
        1. 带单位的数字（元/条/度/米）——价格、雪道数、坡度、长度
        2. 书名号里的作品名——《The Art of FLIGHT》这种

    为什么单靠数字不够（这是被真模型打脸打出来的）：
        问"推荐一部滑雪电影"时，模型给的是《The Art of FLIGHT》（2011）、
        《McConkey》（2013）——一个价格数字都没有，全是片名和年份，
        但同样是在没有依据的情况下凭空输出。只查数字会整段漏掉。

    精度取向：**宁可漏报，不可误报**。
        这里刻意不把裸年份算作事实（"2026-2027 雪季"这类表述太常见，
        会把正常回答打成幻觉）。误报的代价是让用户看到"暂无可靠来源"，
        比漏掉一条更难受。更宽的政策交给系统提示词去讲，
        中间件只守住最高危、最容易误伤为 0 的那几类。
    """
    human_numbers: set[float] = set()
    for message in turn:
        if getattr(message, 'type', '') == 'human':
            human_numbers |= _numbers_in(_text_of(message))

    claims: list[str] = []
    seen: set[str] = set()

    for raw, unit in _FACT_NUM.findall(_normalize_units(answer)):
        try:
            value = float(raw)
        except ValueError:
            continue
        if value in human_numbers:      # 复述用户自己给的预算/人数，不算编造
            continue
        key = f'{raw}{unit}'
        if key not in seen:
            seen.add(key)
            claims.append(key)

    for title in _TITLE.findall(answer or ''):
        if title not in seen:
            seen.add(title)
            claims.append(title)

    return claims


# ---------------- 三个钩子 ----------------

def _query_resort_guard(call: dict, state) -> tuple[dict, str]:
    """仅依据本轮用户明确的唯一雪场纠正未知工具参数；不做模糊猜名。"""
    if call.get('name') not in {'get_resort_runs', 'get_resort_price', 'get_resort_lifts', 'search_resort_kb'}:
        return call, ''
    from rag.parser import ResortRegistry
    human = next((m for m in reversed(_turn_messages(state)) if getattr(m, 'type', '') == 'human'), None)
    text = _text_of(human) if human is not None else ''
    # 只排除明确禁止替代的整段从句，不把普通比较或模糊否定当作单场。
    text = re.sub(r'(?:不要|别)(?:拿|用|使用)[^，,。；;！？!?\n]{0,60}(?:替代|补答|补)', '', text)
    ids = ResortRegistry.list_resort_ids()
    mapping = ResortRegistry.name_map()
    def targets(value):
        known = {id_ for name, id_ in mapping.items() if name and name in value}
        known.update(id_ for id_ in ids if re.search(r'(?<![a-zA-Z0-9_])' + re.escape(id_) + r'(?![a-zA-Z0-9_])', value))
        return known, ResortRegistry.detect_unknown(value)
    mentioned, unknown = targets(text)
    args = dict(call.get('args') or {})
    supplied = str(args.get('resort') or '').strip()
    actual = supplied if supplied in ids else mapping.get(supplied, '')
    rejection = '查询对象有歧义或参数不在用户请求范围，未查询；请明确单座雪场，不能拿其他雪场补答。'
    if not mentioned and not unknown and re.search(r'它|该雪场|那里的|那.*(?:票价|雪票|雪道|缆车)|(?:票价|雪票|雪道|缆车).*[呢？?]', text):
        humans = [m for m in (state or {}).get('messages', []) if getattr(m, 'type', '') == 'human']
        # 只看紧邻上一用户轮，不能越过闲聊或多场问题找更旧对象。
        if len(humans) < 2:
            return call, rejection
        mentioned, unknown = targets(_text_of(humans[-2]))
        if len(mentioned) != 1 or unknown:
            return call, rejection
    if len(mentioned) > 1 or (mentioned and unknown):
        if actual in mentioned or (unknown and supplied in ResortRegistry.uncovered_map()):
            return call, ''
        return call, rejection
    if unknown and not mentioned:
        if actual:
            return call, rejection
        return call, ''
    if not mentioned:
        return call, ''
    expected = next(iter(mentioned))
    if actual and actual != expected or ResortRegistry.detect_unknown(supplied):
        return call, '工具雪场参数与本轮用户明确雪场不一致，未查询；请按用户原话重新选参，不得用其他雪场资料补答。'
    if actual == expected:
        return call, ''
    # 用户明确对象是依据；不是把未知字符串模糊匹配成相似雪场。
    args['resort'] = expected
    return {**call, 'args': args}, ''


_RENTAL_WORDS = ('租赁', '租具', '租板', '租金', '雪具')


def _query_board_guard(call: dict, state) -> dict:
    """用户只点名一种板型时，查价与检索参数里的板型跟用户原话走

    实测（2026-10-11，glm-4-flash）：用户问「留寿都双板租赁一天多少钱」，
    模型传的是 item=「单板租赁」，工具照参数返回了单板价，回答整段答非所问。
    这里只改板型词；用户两种都提或都没提时不动参数。
    """
    if call.get('name') not in {'get_resort_price', 'search_resort_kb'}:
        return call
    human = next((m for m in reversed(_turn_messages(state)) if getattr(m, 'type', '') == 'human'), None)
    text = _text_of(human) if human is not None else ''
    alpine, snowboard = '双板' in text, '单板' in text
    if alpine == snowboard:
        return call
    board, other = ('双板', '单板') if alpine else ('单板', '双板')
    args = dict(call.get('args') or {})
    changed = False
    code = 'ski' if board == '双板' else 'snowboard'
    supplied_code = str(args.get('board') or '').strip().lower()
    if supplied_code and supplied_code != code:
        args['board'] = code
        changed = True
    for key in ('item', 'query'):
        value = args.get(key)
        if not isinstance(value, str) or not value:
            continue
        fixed = value.replace(other, board) if board not in value else value
        if (key == 'item' and board not in fixed and other not in fixed
                and any(word in fixed for word in _RENTAL_WORDS)):
            fixed = board + fixed
        if fixed != value:
            args[key] = fixed
            changed = True
    return {**call, 'args': args} if changed else call


def _query_evidence_block(call: dict, state) -> int:
    """本轮明确多场查询才启用确定性雪场子段，未知参数保留旧段。"""
    name = str(call.get('name') or '')
    base = EVIDENCE_BLOCK.get(name, DEFAULT_BLOCK)
    if name not in {'get_resort_runs', 'get_resort_price', 'get_resort_lifts', 'search_resort_kb'}:
        return base
    from rag.parser import ResortRegistry
    human = next((m for m in reversed(_turn_messages(state)) if getattr(m, 'type', '') == 'human'), None)
    text = _text_of(human) if human is not None else ''
    text = re.sub(r'(?:不要|别)(?:拿|用|使用)[^，,。；;！？!?\n]{0,60}(?:替代|补答|补)', '', text)
    ids = sorted(ResortRegistry.list_resort_ids())
    mapping = ResortRegistry.name_map()
    known = {id_ for alias, id_ in mapping.items() if alias and alias in text}
    known.update(id_ for id_ in ids if re.search(r'(?<![a-zA-Z0-9_])' + re.escape(id_) + r'(?![a-zA-Z0-9_])', text))
    if len(known) < 2 and not (known and ResortRegistry.detect_unknown(text)):
        return base
    supplied = str((call.get('args') or {}).get('resort') or '').strip()
    canonical = supplied if supplied in ids else mapping.get(supplied, '')
    if canonical not in known:
        return base
    return base + 10000 + (ids.index(canonical) + 1) * 1000


@wrap_tool_call
async def evidence_envelope(request, handler: Callable):
    """证据信封：把工具返回值里的 [E1] 平移成全轮唯一的编号

    为什么在中间件里做、而不是让每个工具自己编：
        工具之间互相不知道对方返回了几条证据，并行调用时更拿不到全局序号。
        按工具分段；本轮明确多场时增加确定性雪场子段，单场编号保持兼容。
    """
    original = getattr(request, 'tool_call', None) or {}
    call, rejection = _query_resort_guard(original, getattr(request, 'state', None))
    if rejection:
        from langchain_core.messages import ToolMessage
        return ToolMessage(content=rejection, tool_call_id=original['id'])
    if call is not original:
        logger.info('查询参数按本轮唯一用户雪场规范化｜工具=%s｜雪场=%s', call.get('name'), call['args']['resort'])
    board_call = _query_board_guard(call, getattr(request, 'state', None))
    if board_call is not call:
        logger.info('查询参数按用户原话板型规范化｜工具=%s｜参数=%s', call.get('name'), board_call['args'])
        call = board_call
    if call is not original:
        request = request.override(tool_call=call)
    result = await handler(request)
    name = str(call.get('name') or '')
    block = _query_evidence_block(call, getattr(request, 'state', None))
    if name not in EVIDENCE_BLOCK:
        return result          # 不产证据的工具（set_trip_info / update_profile）不动它
    return _renumber_result(result, block)


@after_model(can_jump_to=['model', 'tools'])
async def citation_check(state, runtime):
    """引用校验：编号要在白名单内，数字要在证据里站得住

    漏查唯一明确雪场时由图补查一次；其他错误按配置重答或降级。
    这是"反照骗"最硬的技术表达：溯源不是提示词工程，是后置校验。

    三条防线，覆盖三种"没有依据却开口"的情形：
        1. 有证据，但引用了不存在的编号 / 数字对不上   -> 见下方白名单校验
        2. 调了工具却一无所获，仍输出具体事实          -> 见 not allowed 分支
        3. 一个工具都没调，却输出具体事实              -> 见 not tool_messages 分支
    第 3 条是最容易被忽略的：模型完全可以绕开所有工具，
    凭自己的训练记忆讲一堆"看电影、某某雪场多少钱"，而这条路径没有工具输出可供比对。

    第 1 条有个**边界**（实测踩到，2026-09-12）：
        白名单只认本轮证据，而模型的引用是跨轮连续的——追问时它会把上一轮的编号
        带进回答，于是被判"引用了不存在的编号"，重写还带着它，最后降级成拒答。
        所以本轮**有**证据时，允许引用最近 N 轮（settings.CITATION_LOOKBACK_TURNS）
        出现过的编号；但情形 2/3 不放宽——本轮空手而归时，不许拿前几轮的证据顶上。
    """
    turn = _turn_messages(state)
    answer_message = _final_ai_message(turn)
    if answer_message is None:
        return None            # 模型这轮在调工具，还没有最终回答

    from agent.evidence import normalize_citations

    answer = normalize_citations(_text_of(answer_message))
    tool_messages = [m for m in turn if getattr(m, 'type', '') == 'tool']
    # 裸雪场名只需基本介绍：逐字采用本轮位置证据，避免弱模型扩写后漏标引用。
    intro = _resort_intro(turn, tool_messages)
    if intro:
        answer = intro

    # ---- 情形 3：没有调取证工具（仅记录画像/行程也不算查资料）----
    # 为什么不能一律放行：寒暄和追问确实该放行，但"没调工具却讲具体事实"
    # 就是纯粹的凭空输出。判断标准是**有没有具体事实痕迹**，而不是有没有调工具。
    if not any(getattr(m, 'name', '') in EVIDENCE_BLOCK for m in tool_messages):
        claims = _factual_claims(answer, turn)
        # 实测（2026-10-12，glm-4-flash）：问「北大湖哪里看雾凇」不调工具，答成"位于日本群马县"——
        # 没有数字和书名号，上面的事实痕迹检查拦不住。长篇作答时补查唯一明确雪场。
        recovery_call = _resort_recovery_call(state)
        if (_covered_resort_asked(turn) or recovery_call) and (claims or len(answer.strip()) >= 40):
            return _verdict([NO_TOOL_FOR_RESORT], turn, recovery_call=recovery_call)
        if claims:
            return _verdict([
                '本轮没有调用任何工具、没有任何资料依据，却输出了具体事实：'
                + '、'.join(claims[:6]),
            ], turn)
        return None

    evidence_text = '\n'.join(_text_of(m) for m in tool_messages)
    allowed = _evidence_ids(tool_messages)

    # ---- 情形 2：调了工具，但一条证据都没拿到 ----
    # 例如守卫返回"本库暂未收录"。此时**不能直接跳过校验**——那正是幻觉最容易
    # 溜过去的缝隙：模型完全可以无视"未收录"的提示，凭记忆编一个价格讲给用户。
    # 注意这里**不看回看窗口**：本轮空手而归就是空手而归，不许拿前几轮的证据顶上，
    # 否则"未收录的雪场"会被答成上一个雪场的价格，那是最坏的一种错。
    if not allowed:
        reasons: list[str] = []
        if _CITE.search(answer):
            reasons.append('本轮没有任何证据，却标注了来源编号')
        bare_facts = _factual_claims(answer, turn)
        if bare_facts:
            reasons.append('本轮未取得任何资料，这些内容没有依据：'
                           + '、'.join(bare_facts[:6]))
        return _verdict(reasons, turn)

    # ---- 跨轮引用：本轮有证据时，允许沿用前几轮的依据 ----
    # 实测误伤（2026-09-12）：「先问有几条高级道 → 再追问"那价格呢？"」第二轮里，
    # 模型顺手带上了上一轮的编号 E201（那条证据确实返回过、用户在上一条消息里
    # 也看得见），但白名单只认本轮 → 判"引用了不存在的编号" → 打回；重写时
    # 它照旧带着这个编号 → 再打回 → **降级成拒答**。
    # 最普通的追问路径被判死，是误伤里最贵的一种。
    lookback = _lookback_tool_messages(state, settings.CITATION_LOOKBACK_TURNS)
    carried = _evidence_ids(lookback)
    carried_text = '\n'.join(_text_of(m) for m in lookback)
    if carried:
        logger.info('引用校验：沿用前 %s 轮的证据编号 %s',
                    settings.CITATION_LOOKBACK_TURNS,
                    '、'.join(f'E{n}' for n in sorted(carried))[:120])

    cited = {int(x) for x in _CITE.findall(answer)}

    reasons = []
    unknown = sorted(cited - allowed - carried)
    if unknown:
        reasons.append('引用了不存在的来源编号：'
                       + '、'.join(f'E{n}' for n in unknown))
    if not cited:
        reasons.append('回答没有标注任何来源编号')
    # 数字比对也要带上回看窗口：不然"接着说上一轮的 32 条"会被判成编造，
    # 那只是把同一个误伤从编号挪到了数字上
    facts = _check_facts(answer, evidence_text + '\n' + carried_text, turn)
    if facts:
        reasons.append('这些数字在本轮证据里找不到依据：' + '、'.join(facts[:6]))
    board_facts = _check_board_amounts(answer, evidence_text + '\n' + carried_text, turn)
    if board_facts:
        reasons.append('用户只问一种板型，回答却写了另一种板型的金额：' + '、'.join(board_facts[:6]))

    verdict = _verdict(reasons, turn, allowed | carried)
    if verdict is None and intro:
        return {'messages': [AIMessage(content=answer, id=answer_message.id,
                                       usage_metadata=answer_message.usage_metadata,
                                       additional_kwargs={'grounded_intro': True})]}
    return verdict


NO_TOOL_FOR_RESORT = '用户问的是本库已收录的雪场，本轮却没有查任何资料就作答'


def _covered_resort_asked(turn: list) -> bool:
    """本轮用户原话点名了本库收录的雪场"""
    from rag.parser import ResortRegistry
    human = next((m for m in reversed(turn) if getattr(m, 'type', '') == 'human'), None)
    return bool(human is not None and ResortRegistry.detect(_text_of(human)))


def _resort_recovery_call(state) -> dict | None:
    """只为用户明确的唯一雪场补查；沿用现有范围守卫处理别名、换场和省略追问。"""
    human = next((m for m in reversed(_turn_messages(state))
                  if getattr(m, 'type', '') == 'human'), None)
    if human is None:
        return None
    query = _text_of(human).strip()
    call = {'name': 'search_resort_kb', 'args': {'query': query, 'resort': ''},
            'id': f'lookup-{uuid4().hex}', 'type': 'tool_call'}
    call, rejection = _query_resort_guard(call, state)
    resort = call['args'].get('resort')
    if rejection or not resort:
        return None
    # 只报雪场名时先查基本位置，避免泛检索随机挑中一条高级道。
    from rag.parser import ResortRegistry
    if query.strip(' ，。！？!?') in {resort, *ResortRegistry.name_map()}:
        call['args']['query'] = query + ' 在哪里 基本介绍'
    return call


def _resort_intro(turn: list, tool_messages: list) -> str:
    """仅为精确雪场名渲染已取证的位置首句，不从模型草稿或历史补事实。"""
    from agent.evidence import EvidenceDigest
    from rag.parser import ResortRegistry

    human = next((m for m in reversed(turn) if getattr(m, 'type', '') == 'human'), None)
    name = _text_of(human).strip(' ，。！？!?') if human is not None else ''
    mapping = ResortRegistry.name_map()
    resort = mapping.get(name) or (name if name in ResortRegistry.list_resort_ids() else '')
    if not resort:
        return ''
    for evidence in EvidenceDigest.of_messages(tool_messages):
        if evidence['status_key'] not in {'verified', 'corroborated', 'transcribed'}:
            continue
        sentence = re.match(r'[^。！？\n]*?位于[^。！？\n]+[。！？]?', evidence['snippet'])
        if sentence is None or ResortRegistry.detect(sentence[0]) != resort:
            continue
        # 来源元数据与位置句一起保留；仅输出可逐字核对的句子，仍走下方事实/编号校验。
        source = '｜'.join(str(evidence[key]) for key in ('label', 'source', 'date', 'status')
                          if evidence[key])
        return (f'{sentence[0]} [E{evidence["id"]}]\n\n资料：{source}\n\n'
                '你想了解雪道、票价，还是新手建议？')
    return ''


def _verdict(reasons: list[str], turn: list, allowed: set[int] | None = None,
             recovery_call: dict | None = None):
    """根据校验结论决定：放行 / 打回重写 / 降级

    抽成函数是因为现在有两条路径会走到这里：
        1. 有证据，但引用编号或数字不合格
        2. 调了工具却一条证据都没拿到，回答里却出现了事实数字
    """
    if not reasons:
        return None

    # 打回时留痕：否则线上只看到"答案变了"、看不到"为什么被判不合格"，
    # 也就分不清到底是真抓到幻觉，还是校验太严误伤了正常回答。
    # **必须把被判不合格的原文也记下来**：否则只能靠猜"15度"究竟出自哪句话——
    # 这是排查误报时唯一有效的线索。
    # 记到 logs/backend.log（utils/log.py）：服务起在哪个终端里都不影响事后排查。
    rejected = _final_ai_message(turn)
    tool_names = [getattr(m, 'name', '') for m in turn if getattr(m, 'type', '') == 'tool']
    logger.warning(
        '引用校验未通过｜本轮工具=%s｜可用编号=%s｜原因：%s',
        '、'.join(n for n in tool_names if n) or '（未调用任何工具）',
        '、'.join(f'E{n}' for n in sorted(allowed)) if allowed else '（无）',
        '；'.join(reasons))
    if rejected is not None:
        logger.warning('被判不合格的回答：%s', _text_of(rejected)[:400])

    # 直接降级（2026-10-05 决策）：**不做"喂回重写"**。
    #
    # 依据（实测）：
    #   1. 模型对比报告（20 轮 / 5 个模型）：只有最弱的 glm-4-flash 触发过打回（2/4），
    #      glm-4.5-air 以上**一次都没触发** → "重写能救回回答"的收益很小。
    #   2. 成本明确：重写 = 多 1-2 次模型调用；而且这条"打回 → 回到模型节点"的路径
    #      正是与 ModelCallLimitMiddleware 组合把图转死的那条（见 MiddlewareFactory.build）。
    #   3. 与项目命题一致："宁可拒答，不可编造"——资料不足时直接说"暂无可靠来源"
    #      比"再让模型编一次"更符合"答案凭什么可信"。
    #
    # 保留开关 CITATION_RETRY_ON_FAIL 是为了**做对照实验**（想量化"重写能救回多少"时打开）。
    missing_only = (getattr(settings, "CITATION_RETRY_MISSING_ONLY", False)
                    and bool(allowed)
                    and reasons == ["回答没有标注任何来源编号"])
    # 没查资料就答已收录雪场：唯一对象由图补查，歧义对象仍提醒模型澄清/查询。
    skipped_tools = reasons == [NO_TOOL_FOR_RESORT]
    retry_enabled = settings.CITATION_RETRY_ON_FAIL or missing_only or skipped_tools
    if not retry_enabled or any(RETRY_MARK in _text_of(m) for m in turn):
        if retry_enabled:
            logger.warning('重写后仍不合格，降级为「暂无可靠来源」'
                           '（用户看到的是拒答；请按上面的原文确认是真幻觉还是误伤）')
        else:
            logger.warning('直接降级为「暂无可靠来源」（未做打回重写，见 _verdict 注释；'
                           '用户看到的是拒答——请按上面的原文确认是真幻觉还是误伤）')
        return {'messages': [AIMessage(content=DEGRADE_REPLY)]}

    if skipped_tools and recovery_call:
        # 只发提醒仍可能再次不调工具。直接交给图的工具节点执行，
        # 保留证据信封、工具重试和后置校验；本轮最多补查一次。
        logger.info('未查资料的雪场回答由系统补查｜雪场=%s', recovery_call['args']['resort'])
        messages = [RemoveMessage(id=rejected.id)] if rejected is not None and rejected.id else []
        messages += [
            SystemMessage(content=(
                f'{RETRY_MARK}\n上一版回答没有资料依据，系统已发起本轮雪场查询。'
                '请只根据接下来的工具结果重新回答，每条事实标注返回的来源编号；'
                '保留日期、可信度、过期提醒和来源冲突说明。'
                '用户只给雪场名时，先简短说明位置，再问最关心哪方面，'
                '不主动扩展雪道总数等未问项目。资料不足就如实说明，不编造。')),
            AIMessage(content='', tool_calls=[recovery_call],
                      usage_metadata=rejected.usage_metadata if rejected is not None else None),
        ]
        return {'messages': messages, 'jump_to': 'tools'}

    usable = ''
    if allowed:
        usable = (f'本轮可用的来源编号只有：'
                  f'{"、".join(f"E{n}" for n in sorted(allowed))}。\n')
    elif skipped_tools:
        usable = ('本轮还没有查资料。请先调用 search_resort_kb（打卡、雾凇、夜场、通行证、攻略、视频等）'
                  '或 get_resort_runs / get_resort_price / get_resort_lifts 查本库，再依据返回资料作答。\n')
    else:
        usable = '本轮没有取得任何可用资料。\n'
    correction = (
        f'{RETRY_MARK}\n'
        '你上一条回答存在以下问题：\n- ' + '\n- '.join(reasons) + '\n\n'
        f'{usable}'
        '请重新作答：只用本轮工具返回的资料；每个结论后面标注来源编号；'
        '金额、雪道数、坡度、长度必须来自资料原文，算出来的合计要写清怎么算的。'
        '如果资料不足以回答，就直接说"暂无可靠来源"，不要编。'
    )
    return {'messages': [SystemMessage(content=correction)], 'jump_to': 'model'}


@before_model
async def trim_history(state, runtime):
    """消息裁剪（兜底）：消息数超过硬上限时，直接丢掉最老的一段

    它是**保险丝，不是常规手段**。常规路径交给官方的 SummarizationMiddleware：
    那个会把旧消息摘要后保留，信息不丢；而这里是真的丢历史。
    所以阈值（HISTORY_HARD_LIMIT=40）要设得远高于摘要阈值——
    正常情况下摘要早就把消息数压到 12 条左右，这条分支永远不会触发。
    留着它的意义是：万一摘要失效（模型调用报错、token 计数不准），
    上下文也不会无限增长到把窗口撑爆。

    注意：关键事实（日期/预算/偏好）在 TripState 里，不依赖消息历史。

    裁剪有两条硬约束，踩过才知道：
        1. 不能从一条 ToolMessage 开头——它前面必须有发起调用的 AIMessage，
           否则模型 API 会因为"tool 消息找不到对应的 tool_call"直接报错。
        2. 本轮消息一条都不能删——引用校验要读本轮的 ToolMessage 当白名单。
    """
    messages = list((state or {}).get('messages') or [])
    limit = settings.HISTORY_HARD_LIMIT
    if len(messages) <= limit:
        return None

    # 1.先定位本轮起点（最后一条 human），本轮一律保留
    last_human = 0
    for index, message in enumerate(messages):
        if getattr(message, 'type', '') == 'human':
            last_human = index
    keep_from = min(last_human, max(0, len(messages) - limit))

    # 2.再往后挪，直到落点是一条 HumanMessage（保证不出现"孤儿工具消息"）
    while keep_from < last_human and \
            getattr(messages[keep_from], 'type', '') != 'human':
        keep_from += 1
    if keep_from <= 0:
        return None

    # 3.用 RemoveMessage 显式删除，交给 messages 的 reducer 处理
    return {'messages': [RemoveMessage(id=m.id) for m in messages[:keep_from]
                         if getattr(m, 'id', None)]}


class MiddlewareFactory:
    """中间件装配：官方件优先，自研件只补官方没有的能力

    为什么要分开：
        官方件解决的是通用工程问题（工具重试、上下文摘要），能直接用就别自己写；
        自研件解决的是本项目的命题——"回答里的引用与数字站不站得住"，
        这一类官方没有，属于必须自研的部分。

    装配顺序（列表在前 = 更外层）：
        1. ModelCallLimitMiddleware   单轮模型调用次数上限（防不收敛）
        2. ToolCallLimitMiddleware    单轮工具调用次数上限
        3. ToolRetryMiddleware        工具失败自动重试，指数退避
        4. ModelRetryMiddleware       模型调用失败重试（429/网络抖动）
        5. SummarizationMiddleware    上下文超长时摘要（一级）
        6. evidence_envelope          证据编号全轮唯一
        7. citation_check             引用与数字后置校验
        8. trim_history               裁剪兜底（二级，保险丝）
    """

    @staticmethod
    def build(model) -> list:
        """按配置装配中间件栈

        :param model: 已构建的对话模型；摘要中间件需要用它来生成摘要
        """
        # 懒加载官方中间件，避免影响无关模块导入
        from langchain.agents.middleware import (ClearToolUsesEdit,
                                                 ContextEditingMiddleware,
                                                 ModelCallLimitMiddleware,
                                                 ModelRetryMiddleware,
                                                 SummarizationMiddleware,
                                                 ToolCallLimitMiddleware,
                                                 ToolRetryMiddleware)

        from agent.conversation_focus import ConversationFocusMiddleware

        stack = [
            ConversationFocusMiddleware(),
            # ⚠️ ModelCallLimitMiddleware 默认**不挂**（2026-10-04 实测不兼容，见下）
            #    现象：多轮工具对话里 chain:model 被进入 40 次、真正调模型只有 5~7 次，
            #          最终烧穿 recursion_limit=40（用户侧=回答卡住）。
            #    定位过程（都可复现）：
            #      自研三件 + ModelCallLimit      -> ❌ 超限（模型 7 次）
            #      自研三件 + ToolCallLimit       -> ✅ 正常
            #      自研三件 + 两个重试(无上限)     -> ✅ 正常
            #      只留 ModelCallLimit            -> ✅ 正常
            #    结论：它与自研件（引用校验的"打回重写"路径会反复回到模型节点）相互作用，
            #          不是它单独有问题，也不是自研件单独有问题——是**组合**才炸。
            #    替代方案：模型调用次数由 AGENT_RECURSION_LIMIT + GraphRecursionError 降级兜底；
            #          "反复调工具"由 ToolCallLimitMiddleware 兜住（那才是绕圈的常见形态）。
            #    要重新试它：把 AGENT_MAX_MODEL_CALLS 设为正数（>0）即会挂上。
            *([ModelCallLimitMiddleware(run_limit=settings.AGENT_MAX_MODEL_CALLS,
                                        exit_behavior='end')]
              if settings.AGENT_MAX_MODEL_CALLS > 0 else []),
            # 单轮工具调用上限：超限给模型一条提示，让它拿已有资料作答
            ToolCallLimitMiddleware(run_limit=settings.AGENT_MAX_TOOL_CALLS,
                                    exit_behavior='continue'),
            # 工具失败自动重试：原来工具抛异常只会变成一句错误字符串塞给模型，
            # 网络抖动、MySQL 瞬时锁这类"重试就好"的故障白折一轮。
            ToolRetryMiddleware(max_retries=2, backoff_factor=2.0,
                                initial_delay=1.0),
            # 模型调用重试：实测踩过 429（余额/限流）与连接抖动；
            # 带 jitter 是为了避免多个请求同时重试又一起撞上限流。
            ModelRetryMiddleware(max_retries=2, backoff_factor=2.0,
                                 initial_delay=1.0, jitter=True),
            # 上下文清理（**用官方的**，2026-10-04 起）：把旧工具输出清空并留占位符，
            # 消息结构不动 → 天然没有"孤儿工具消息"问题，代码也少得多。
            # 阈值刻意设得高（CONTEXT_EDIT_TRIGGER_TOKENS）：引用校验要读工具消息里的
            # 证据，清得太早会让"合法的引用"被判成没有出处——两个件会互相打架。
            *([ContextEditingMiddleware(edits=[ClearToolUsesEdit(
                trigger=settings.CONTEXT_EDIT_TRIGGER_TOKENS,
                keep=settings.CONTEXT_EDIT_KEEP_TOOL_RESULTS,
                placeholder='[上文工具输出已清理，如需请重新查询]',
            )])] if settings.USE_CONTEXT_EDITING else []),
            # 上下文摘要（一级）：把旧消息摘要后保留，比直接丢历史保信息。
            # ⚠️ 2026-10-04 实测后**默认关闭**（settings.USE_SUMMARY_MIDDLEWARE=False）：
            #    debug 统计显示多轮工具对话里 chain:model 被进入 40 次、真正调模型只有 5 次
            #    ——它与自研的 trim_history 互相打架（摘要后仍高于阈值 → 下一步又触发 → 空转到超限）。
            #    三组中间件各自单独跑都正常，只有全栈一起才炸，属于多件相互作用。
            *([SummarizationMiddleware(
                model=model,
                trigger=('tokens', settings.SUMMARY_TRIGGER_TOKENS),
                keep=('messages', settings.SUMMARY_KEEP_MESSAGES),
            )] if settings.USE_SUMMARY_MIDDLEWARE else []),
            evidence_envelope,
            citation_check,
            # 自研裁剪退居保险丝（默认不挂，见 config.USE_TRIM_HISTORY）：
            # 消息条数无上限时它仍有用，但"清空旧工具输出"交给官方件做更安全。
            *([trim_history] if settings.USE_TRIM_HISTORY else []),
        ]
        return stack


# 只含自研件的清单：留作对照与单测使用（官方件需要 model 实例，没法模块级建好）
CUSTOM_MIDDLEWARE = [evidence_envelope, citation_check, trim_history]
