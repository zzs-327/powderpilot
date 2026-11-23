"""雪场档案解析：把 data/resorts 下的 YAML/PDF/MD 读成 LangChain Document。

数据来源（单一真相源）：
    data/resorts/<resort_id>.yaml   —— 雪场档案（手册要点/价格/避坑/视频卡/实拍元数据）
    data/resorts/<resort_id>/*.md   —— 手册正文（可选，长文放这里）

设计要点：
    1. 每个"语义单元"生成一条 Document（一条雪道/一条价格/一条避坑/一个视频卡）
    2. 每条 Document 必须带齐 metadata，否则检索无法分层与溯源
    3. expire_at = date + TTL 自动计算（价格 90 天 / 雪道 180 天 / 避坑 365 天 / 视频与实拍无时效）
"""
import datetime as dt
import re
from pathlib import Path

import yaml
from langchain_core.documents import Document

from config.config import settings
from config.exceptions import ServiceException
from rag.pricing import price_text, validate_prices

# ---------------- 归一化映射 ----------------
DIFFICULTY_TO_LEVEL = {
    '初级': 'beginner', '新手': 'beginner', '绿道': 'beginner',
    '中级': 'intermediate', '蓝道': 'intermediate',
    '高级': 'advanced', '专家': 'advanced', '黑道': 'advanced', '野雪': 'advanced',
    # 雪场自己的复合分级（北大湖官网就是这么写的，不能丢）：
    #   初中级道 -> 算新手（宁松不紧；但不会推给老手当挑战）
    #   中高级道 -> 按**保守**算老手：宁可新手看不到，也不要把人骗上陡坡
    #   连接道   -> 不参与分层（联络道而已，跟水平无关）
    '初级道': 'beginner', '初中级道': 'beginner',
    '中级道': 'intermediate',
    '中高级道': 'advanced', '高级道': 'advanced',
    '连接道': 'all',
}
BOARD_TO_CODE = {'单板': 'snowboard', '双板': 'ski', '都玩': 'all', 'both': 'all', 'all': 'all', '': 'all'}
LEVEL_LABEL = {'beginner': '新手', 'intermediate': '进阶', 'advanced': '老手', 'all': '通用'}

# 保鲜期（天）：0 表示无时效
TTL_DAYS = {'price': 90, 'run': 180, 'tip': 365, 'video': 0, 'photo': 0, 'lift': 180,
            'overview': 365}

# 数据可信度分级：我们"怎么拿到这批数据"的，与 source_type（信息本身的性质）是两个维度。
#
# 为什么必须分开：
#     一条"官方雪道参数"被票务代理页转引后，它的**性质**仍是 A（官方参数），
#     但我们的**获取方式**是转引。合成一个字段就会两头撒谎——
#     要么把转引说成官方直采，要么把官方参数降级成"经验"。
#
# 现状（2026-09 复查）：万龙官网域名 wanlongski.com 已失效并被抢注，
#     所以万龙/丝绸之路的档案目前都够不到 verified。
#
# ⚠️ **分级的目的是"告诉用户这是几手数据"，不是"给数据设门槛"**（用户明确要求）：
#     有些数据官方确实没有，只能靠多个来源互相印证——那就如实说"多源印证"，
#     而不是因为拿不到官方直采就把它标成"不可信"。
DATA_STATUS_LABEL = {
    'verified': '✅ 已核对（官方直采）',
    'corroborated': '🔁 多源印证（独立来源相互印证，仍非官方直采）',
    'transcribed': '⚠️ 二手转引（非官网直采）',
    'sample': '🚫 示例数据（未核实，不得作为事实引用）',
}

# 每一档"该怎么说"（会被拼进证据，模型照此措辞）
#
# 为什么单独一张表：档位现在**按数据类型**生效，同一座雪场里可能同时存在
# "雪道=多源印证、价格=二手转引"，提醒必须逐档给，不能只看雪场默认档
# （只看默认档时，那句"别说成官方发布"会整条消失——万龙就是这个形状）。
DATA_STATUS_CAVEAT = {
    'verified': '为官方直采，可作为事实陈述。',
    'corroborated': ('非官方直采，但有独立来源相互印证；引用时应说"多个来源均可查到"，'
                     '不要写成"官方发布"。'),
    'transcribed': '为二手转引、未经官网直采；引用时应说明信息来源性质，建议以官方渠道为准。',
    'sample': '仍为示例占位、未核实，不得作为事实回答用户。',
}

# 按"有多可信"从高到低排；weakest_status() 依赖这个顺序做保守取值
STATUS_RANK = {'verified': 3, 'corroborated': 2, 'transcribed': 1, 'sample': 0}

# 逐字段印证记录里的"印证结论"。**分歧也是一等公民**，不许只记一致的：
#     只记一致 = 把"来源互相打架"这件事藏起来，而那恰恰是用户最该知道的信息
#     （例如丝绸之路的雪道总数，四个来源给了 12/8/28/6 四种说法）。
CORROBORATION_AGREEMENT = {
    'consistent': '一致',
    'partial': '部分一致',
    'conflict': '有分歧',
    'new_field': '本库原档案没有这个字段（由印证来源补充）',
}


def weakest_status(statuses) -> str:
    """取最保守的那一档可信度

    为什么需要：可信度现在是**按数据类型**给的（雪道有多源印证，价格只有单一转引），
    但 resort 表只有一个 data_status 列。凡是要用一个值代表整座雪场时，一律取最弱那档——
    **宁可少说，不可多说**，这是本项目的底线。
    """
    known = [str(s) for s in statuses if str(s) in STATUS_RANK]
    if not known:
        return 'sample'
    return min(known, key=lambda item: STATUS_RANK[item])


# 允许出现在 YAML 里的合法取值
DATA_STATUS_VALUES = tuple(DATA_STATUS_LABEL)

# 数据类型的中文名（来源登记块里按类型列可信度时用）
DOC_TYPE_LABEL = {
    'overview': '概览', 'transport': '交通参考', 'run': '雪道', 'lift': '索道',
    'price': '价格', 'tip': '避坑与经验', 'video': '教学视频', 'photo': '实拍图',
    'provenance': '来源登记',
}

# **元信息**类型：这些文档是"本库自己整理的说明"，不是数据本身。
# 它们自身的档位不该参与"这座雪场的数据是几手的"——踩过两次：
#   1. 万龙：模型把默认档的笼统说法概括成"雪场静态数据一律二手转引"
#   2. 北大湖：模型在可信度清单里逮到"来源登记=⚠️ 二手转引"，
#      就把**官网直采的雪道数据**说成"标记为二手转引、建议以官方渠道为准"
# 所以：元信息既不进 status_map、也不出现在给模型看的可信度清单里，
# 它们自己的文档也不挂档位标签（data_status='' 表示"不适用"）。
META_DOC_TYPES = {'provenance'}

# 形如"太舞滑雪场""XX雪场"的雪场名提及
_RESORT_MENTION = re.compile(r'([\u4e00-\u9fff]{2,6})(?:滑雪场|滑雪度假区|滑雪小镇|雪场)')
# 名字里出现这些字，多半是"崇礼的雪场"这类泛问，不是具体雪场名
_NOT_A_NAME = set('的和与去到在有个想看这那哪几多少')

# 懒加载并缓存的雪场名册
_name_map: dict[str, str] | None = None

# 懒加载并缓存的"已知但未收录"雪场名单（收录范围守卫用，见 data/uncovered_resorts.yaml）
_uncovered_map: dict[str, str] | None = None


class ResortRegistry:
    """雪场名册：读取 data/resorts 下的雪场 id / 名称 / 别名，供识别与范围守卫使用"""

    @staticmethod
    def list_resort_ids() -> list[str]:
        """列出所有雪场 id（下划线开头的模板文件跳过）"""
        folder = Path(settings.RESORT_DIR)
        return sorted(p.stem for p in folder.glob('*.yaml') if not p.stem.startswith('_'))

    @staticmethod
    def name_map() -> dict[str, str]:
        """{雪场名 / 简称 / 别名: resort_id}，懒加载并缓存"""
        global _name_map
        if _name_map is not None:
            return _name_map

        mapping: dict[str, str] = {}
        for resort_id in ResortRegistry.list_resort_ids():
            path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
            data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            name = str(data.get('name') or resort_id)
            mapping[name] = resort_id
            # 支持用户只说"万龙"：去掉常见后缀
            short = re.sub(r'(滑雪场|滑雪度假区|滑雪小镇|雪场|度假区|国际)$', '', name)
            if short and short != name:
                mapping[short] = resort_id
            for alias in data.get('aliases') or []:
                mapping[str(alias)] = resort_id

        _name_map = mapping
        return _name_map

    @staticmethod
    def curated_tips(term: str) -> list[dict]:
        """读 YAML 里人工写的避坑，不经过 MySQL，也不改向量库文本

        term 可以是雪场 id、中文名或别名。对不上就返回空列表。
        """
        key = (term or '').strip()
        resort_id = ResortRegistry.name_map().get(key, '')
        if not resort_id and key in ResortRegistry.list_resort_ids():
            resort_id = key
        if not resort_id:
            return []
        path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        rows = []
        for tip in data.get('tips') or []:
            if not isinstance(tip, dict):
                continue
            text = ' '.join(str(tip.get('text') or '').split())
            if not text:
                continue
            rows.append({
                'text': text,
                'source': str(tip.get('source') or '').strip(),
                'source_type': str(tip.get('source_type') or 'C').strip() or 'C',
                'date': str(tip.get('date') or '').strip(),
            })
        return rows

    @staticmethod
    def uncovered_map() -> dict[str, str]:
        """{未收录雪场的名称/别名: 规范名}，懒加载并缓存

        数据源：data/uncovered_resorts.yaml（人工策展）。
        为什么不能只靠正则猜：用户说"二世谷雪票多少钱"时并没有"滑雪场"三个字，
        正则匹配不到，守卫就形同虚设——加了第二个雪场后立刻出事。
        """
        global _uncovered_map
        if _uncovered_map is not None:
            return _uncovered_map

        mapping: dict[str, str] = {}
        path = Path(settings.DATA_DIR) / 'uncovered_resorts.yaml'
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            for item in data.get('resorts') or []:
                if not isinstance(item, dict):
                    continue
                canonical = str(item.get('name') or '').strip()
                if not canonical:
                    continue
                mapping[canonical] = canonical
                for alias in item.get('aliases') or []:
                    alias = str(alias).strip()
                    if alias:
                        mapping[alias] = canonical

        # 已收录的雪场优先：即使名单里还留着，也不该被判成"未收录"
        for name in ResortRegistry.name_map():
            mapping.pop(name, None)

        _uncovered_map = mapping
        return _uncovered_map

    @staticmethod
    def detect(text: str) -> str:
        """从用户问题中识别雪场 id；识别不到返回空串

        按名称长度倒序匹配，避免短名先命中。
        """
        for name, resort_id in sorted(ResortRegistry.name_map().items(),
                                      key=lambda kv: -len(kv[0])):
            if name and name in text:
                return resort_id
        return ''

    @staticmethod
    def detect_unknown(text: str) -> str:
        """识别"提到一个我们没收录的雪场"，返回该名称（没提到则返回空串）

        为什么需要：
            "太舞滑雪场雪票多少钱"里的"滑雪场/雪票/多少钱"能匹配上万龙的 chunk，
            光靠检索闸门拦不住——会把万龙的价格答成太舞的。
            系统知道自己的收录范围，就该在这里硬拦一道（scope guard）。

        两道检查，顺序不能反：
            1. 策展名单（data/uncovered_resorts.yaml）——覆盖"不带后缀"的说法，
               如"二世谷雪票多少钱"
            2. 后缀正则兜底——覆盖名单还没登记的新雪场，如"XX滑雪场"
        """
        # 1.先查策展名单；长名优先，避免"太舞"先于"太舞小镇"命中导致返回名不一致
        for name, canonical in sorted(ResortRegistry.uncovered_map().items(),
                                      key=lambda kv: -len(kv[0])):
            if name and name in text:
                return canonical

        # 2.再用后缀正则兜底
        known = ResortRegistry.name_map()
        for match in _RESORT_MENTION.finditer(text):
            name, full = match.group(1), match.group(0)
            if any(ch in _NOT_A_NAME for ch in name):
                continue
            if any(known_name in full for known_name in known):
                continue
            return full
        return ''


class ResortProvenance:
    """来源解析器：把 YAML 的 provenance / source_defaults 解析成"每条数据该引用谁"。

    为什么要有这一层（而不是在 parser 和 sync 脚本里各写一遍）：
        这两个地方原来都硬编码了 `source_type='A'` 和 `f'{name}官方资料'`——
        无论数据其实是几手转引，对外一律宣称"官方"。
        这跟"反照骗"的项目前提直接冲突，所以出处必须在**一处**解析、两处共用。
    """

    @staticmethod
    def resolve(data: dict) -> dict:
        """解析雪场档案的来源信息

        :param data: YAML 反序列化后的 dict
        :return: {
            data_status: 雪场默认档（未单独指定的数据类型继承它）,
            status_label: 中文标签,
            status_map: {doc_type: 该类数据实际生效的档位},
            sources: {doc_type: {source, source_type, date, data_status}},
            corroborations: [逐字段的交叉印证记录],
            caveats: [给模型看的可信度提醒, ...],
        }
        """
        # 1.取可信度分级：优先读 provenance.data_status，兼容旧的 meta.data_status
        prov = data.get('provenance') or {}
        meta = data.get('meta') or {}
        status = str(prov.get('data_status') or meta.get('data_status') or 'sample')
        if status not in DATA_STATUS_LABEL:
            status = 'sample'

        name = str(data.get('name') or data.get('id') or '')
        official = data.get('official') or {}
        fallback_date = str(prov.get('retrieved_at') or meta.get('last_reviewed_at')
                            or official.get('price_updated_at') or '')

        # 2.默认出处：YAML 里显式写清楚，没写才退回"官方资料"这个保守表述。
        #    可信度**可以按数据类型单独覆盖**：真实情况常常是"雪道有多源印证、
        #    价格只有单一转引"，一个雪场级的档位表达不了这种差别——
        #    表达不了就会说谎（把价格一起说成有印证，或者把雪道一起否掉）。
        defaults = data.get('source_defaults') or {}
        sources: dict[str, dict] = {}
        for doc_type, item in defaults.items():
            if isinstance(item, dict):
                item_status = str(item.get('data_status') or status)
                sources[str(doc_type)] = {
                    'source': str(item.get('source') or f'{name}资料（未标注具体出处）'),
                    'source_type': str(item.get('source_type') or 'A'),
                    'date': str(item.get('date') or fallback_date),
                    'data_status': item_status if item_status in DATA_STATUS_LABEL else status,
                }

        # 3.逐字段的交叉印证记录（谁印证了谁、哪条一致、哪里分歧）
        corroborations = ResortProvenance.parse_corroboration(prov.get('corroboration'))

        # 4.可信度提醒：**按实际生效的档位逐档生成**，而不是只看雪场默认档。
        #    为什么：真实档案常有"雪场默认=二手转引、雪道=多源印证"这种组合，
        #    只看默认档时，那句"别写成官方发布"会整条消失（万龙就是这个形状）。
        #
        #    ⚠️ 措辞顺序也踩过坑（2026-09-12 实测）：原来第一句放的是**雪场默认档**的
        #    笼统说法（"本雪场数据为二手转引…"），按类型的例外放在后面——结果模型把它
        #    概括成"雪场静态数据一律按二手转引对待"，而万龙的概览类其实已够到 verified。
        #    所以现在：**只要各类档位不一致，第一句就按类型把话说清**，并明确禁止
        #    "整体"式概括；笼统句式只在所有类型同档时才用。
        caveats: list[str] = []
        by_status: dict[str, list[str]] = {}
        for doc_type, item in sources.items():
            if doc_type in META_DOC_TYPES:
                continue        # 元信息不参与"这雪场的数据是几手的"
            by_status.setdefault(item['data_status'], []).append(
                DOC_TYPE_LABEL.get(doc_type, doc_type))
        if len(by_status) > 1:
            order = sorted(by_status, key=lambda s: -STATUS_RANK.get(s, 0))
            summary = '；'.join(f'{"、".join(by_status[s])} = {DATA_STATUS_LABEL[s]}'
                               for s in order)
            caveats.append(
                f'本雪场**各类数据的可信度不一致**，按类型给：{summary}。'
                '引用时必须按类型区分——不要说成"整体都是二手的"，'
                '也不要说成"整体都是官方直采的"。')
            caveats.extend(DATA_STATUS_CAVEAT.get(s, '') for s in order)
        else:
            caveats.append(DATA_STATUS_CAVEAT.get(status, ''))
        if prov.get('stated_source'):
            caveats.append(f'页面自述的原始出处：{prov["stated_source"]}。')
        if prov.get('caveat'):
            caveats.append(str(prov['caveat']))
        # 逐字段的印证/分歧**不写进这里**：它可能很长，塞进概览块会把文档撑过
        # 分块阈值（本机导入 langchain_text_splitters 会挂死，见docs/TROUBLESHOOTING.md #10），
        # 也会把概览淹掉。它单独成块（每个字段一条短文档），见 parse_resort。
        if corroborations:
            caveats.append('逐字段的多来源印证与分歧记录，见"数据来源登记"。')

        return {
            'data_status': status,
            'status_label': DATA_STATUS_LABEL[status],
            'status_map': {doc_type: item['data_status']
                           for doc_type, item in sources.items()
                           if doc_type not in META_DOC_TYPES},
            'sources': sources,
            'corroborations': corroborations,
            'caveats': caveats,
        }

    @staticmethod
    def parse_corroboration(raw) -> list[dict]:
        """解析 provenance.corroboration：逐字段记录"谁印证了谁、一致还是分歧"

        为什么逐字段，而不是打一个"已多源印证"的总标记：
            印证从来不是整体性的——万龙的**雪道总数**有另一个来源对得上，
            但它的**价格**仍然只有单一来源、而且已经过期。
            一个总标记要么把价格一起带上（过度宣称），要么把雪道也一起否掉（漏报）。
            所以记录结构是 (field, claim, source, agreement)，**分歧同样是一条记录**。

        几个必须留的字段：
            stated_source  印证来源自己写的原始出处——用来回答"它俩是不是同源"
            independence   独立性判断。**交叉印证 ≠ 独立**：两个页面都抄同一篇宣传稿时，
                           它们的"一致"什么都证明不了。这句必须能写进证据。
            checked_by     谁复核过。查到的链接不等于看过内容，没复核就如实空着。
        """
        entries: list[dict] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            agreement = str(item.get('agreement') or 'consistent')
            if agreement not in CORROBORATION_AGREEMENT:
                agreement = 'consistent'
            entries.append({
                'field': str(item.get('field') or ''),
                'claim': str(item.get('claim') or ''),
                'source': str(item.get('source') or ''),
                'url': str(item.get('url') or ''),
                'retrieved_at': str(item.get('retrieved_at') or ''),
                'stated_source': str(item.get('stated_source') or ''),
                'independence': str(item.get('independence') or ''),
                'checked_by': str(item.get('checked_by') or ''),
                # 可选：这条记录涉及的数据类型实际是什么档（没写就用雪场默认档）
                'data_status': str(item.get('data_status') or '')
                                if str(item.get('data_status') or '') in DATA_STATUS_LABEL
                                else '',
                'agreement': agreement,
                'agreement_label': CORROBORATION_AGREEMENT[agreement],
                'note': str(item.get('note') or ''),
            })
        return entries

    @staticmethod
    def corroboration_caveats(corroborations: list[dict],
                              include_note: bool = True) -> list[str]:
        """把印证记录转成给模型看的提醒（会被写进证据 / 来源登记块）

        每条记录出两句：印证了什么、以及独立性说明。第二句不能省——
        少了它，"多源印证"这四个字本身就会变成新的照骗。

        include_note=False 时只出"结论 + 出处 + 一致性"骨架、不带细节 note：
        入库时要按分块阈值拆块（见 ResortParser.parse_resort），骨架和细节分开进两块。
        """
        if not corroborations:
            return []
        caveats: list[str] = []
        for entry in corroborations:
            line = f'交叉印证｜{entry["field"]}：{entry["claim"]}'
            if entry['source']:
                line += f'（印证来源：{entry["source"]}'
                line += f'，检索于 {entry["retrieved_at"]}）' if entry['retrieved_at'] else '）'
            line += f'，结论：{entry["agreement_label"]}。'
            if include_note and entry['note']:
                line += f'{entry["note"]}'
            caveats.append(line)
            if entry['independence']:
                caveats.append(f'独立性（{entry["field"]}）：{entry["independence"]}')
        return caveats

    @staticmethod
    def status_of(provenance: dict, doc_type: str) -> str:
        """取某类数据**实际生效**的可信度档位

        优先级：source_defaults.<类型>.data_status > provenance.data_status > sample
        """
        entry = (provenance.get('sources') or {}).get(doc_type) or {}
        status = str(entry.get('data_status') or provenance.get('data_status') or 'sample')
        return status if status in DATA_STATUS_LABEL else 'sample'

    @staticmethod
    def source_of(provenance: dict, doc_type: str, name: str,
                  item: dict | None = None) -> tuple[str, str, str]:
        """取某条数据的 (source_type, source, date)

        优先级：单条 item 上写的 > source_defaults 里的默认 > 兜底"官方资料"。
        """
        default = (provenance.get('sources') or {}).get(doc_type) or {}
        item = item or {}
        source_type = str(item.get('source_type') or default.get('source_type') or 'A')
        source = str(item.get('source') or default.get('source')
                     or f'{name}资料（未标注具体出处）')
        date = str(item.get('date') or item.get('updated_at') or default.get('date') or '')
        return source_type, source, date


def _split_long_text(text: str, budget: int) -> list[str]:
    """把长文本按句切成若干块，保证每块不超过 budget

    为什么要在**生成阶段**就切好：这些块最终进向量库，而超过分块阈值的文档会去构造
    `langchain_text_splitters` —— 那个依赖在本机 import 即无限挂死（docs/TROUBLESHOOTING.md #10），
    现象是"入库打完可用雪场就再无输出"。与其指望下游分块器，不如这里按句切干净。

    先按句号/分号切（不破坏语义），单句仍超长时才硬切。
    """
    if not text:
        return []
    if len(text) <= budget:
        return [text]
    pieces: list[str] = []
    current = ''
    for part in re.split(r'(?<=[。；])', text):
        if current and len(current) + len(part) > budget:
            pieces.append(current)
            current = part
        else:
            current += part
        while len(current) > budget:            # 单句超长：只能硬切
            pieces.append(current[:budget])
            current = current[budget:]
    if current:
        pieces.append(current)
    return pieces


class ResortParser:
    """雪场档案解析工具：YAML/PDF/MD -> Document（每个语义单元一条）"""

    @staticmethod
    def _expire_at(date_str: str, doc_type: str) -> str:
        """按信息类型算保鲜期；算不出就返回空（表示无时效）"""
        ttl = TTL_DAYS.get(doc_type, 0)
        if not ttl or not date_str:
            return ''
        try:
            return (dt.date.fromisoformat(str(date_str)) + dt.timedelta(days=ttl)).isoformat()
        except ValueError:
            return ''

    @staticmethod
    def _build_doc(content: str, *, resort_id: str, resort_name: str, doc_type: str,
                   level: str = 'all', board_type: str = 'all', source_type: str = 'A',
                   source: str = '', date: str = '', seq: int = 0,
                   data_status: str = '', **extra) -> Document:
        """统一构造 Document，保证 metadata 字段齐全（缺失就没法分层与溯源）"""
        metadata = {
            'resort_id': resort_id,
            'resort_name': resort_name,
            'doc_type': doc_type,
            'level': level,
            'board_type': board_type,
            'source_type': source_type,
            'source': source,
            'date': date,
            'expire_at': ResortParser._expire_at(date, doc_type),
            'chunk_id': f'{resort_id}:{doc_type}:{seq:03d}',
            # 数据可信度：A 级结构化数据的采集方式（官方直采/二手转引/示例）
            'data_status': data_status,
        }
        metadata.update(extra)
        return Document(page_content=content, metadata=metadata)

    @staticmethod
    def parse_resort(resort_id: str) -> list[Document]:
        """读取单个雪场的 YAML 档案，按语义单元拆成多条 Document"""
        path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
        if not path.exists():
            raise ServiceException(f'雪场档案不存在：{path}')

        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        name = data.get('name', resort_id)
        region = data.get('region', '')
        official = data.get('official') or {}
        price_date = str(official.get('price_updated_at') or '')
        reviewed_at = str((data.get('meta') or {}).get('last_reviewed_at') or price_date)
        build = ResortParser._build_doc

        # 来源解析：出处与可信度一律从 YAML 读，不在代码里假设"官方"
        prov = ResortProvenance.resolve(data)

        def src(doc_type: str, item: dict | None = None) -> tuple[str, str, str]:
            """取某条数据的 (source_type, source, date)"""
            return ResortProvenance.source_of(prov, doc_type, name, item)

        def status_of(doc_type: str) -> str:
            """取该类数据**实际生效**的可信度档位（可按类型覆盖，见 resolve）"""
            return ResortProvenance.status_of(prov, doc_type)

        def caveat_head(doc_type: str) -> str:
            """短标签：挂在该类数据的每个块上"""
            return '｜数据可信度：' + DATA_STATUS_LABEL[status_of(doc_type)]

        # 可信度提醒分两级写：
        #   短标签（caveat_head）挂在该类数据的每个块上——几十条雪道 chunk 各背一段
        #   完整说明纯属给检索塞噪声；
        #   完整说明（caveats）**单独成块**（见 1.2），不塞进概览——
        #   丝绸之路的来源说明本身就有 200+ 字，加上状态说明会把概览顶过
        #   分块阈值，而一旦超过阈值就会去构造 langchain_text_splitters，
        #   那个依赖在本机导入即挂死（docs/TROUBLESHOOTING.md #10，表现为"入库无输出地卡住"）。
        docs: list[Document] = []

        # 1.概览：只放**这一档真能兜住**的内容
        #    官网直采过的雪场，概览就用官网字段（地址/电话/雪季/资质），
        #    这样"✅ 官方直采"这个标签才名副其实；
        #    交通参考另有出处（本库整理、且带未核实提示），单独成块，免得蹭到官网的档位。
        overview = f'{name}位于{region}。'
        if official.get('address'):
            overview += f'地址：{official["address"]}。'
        if official.get('phone'):
            overview += f'咨询电话：{official["phone"]}。'
        if official.get('season'):
            overview += f'雪季：{official["season"]}。'
        if official.get('highlights'):
            overview += f'{official["highlights"]}。'
        overview_type, overview_src, overview_date = src('overview')
        docs.append(build(overview + caveat_head('overview'), resort_id=resort_id,
                          resort_name=name, doc_type='overview', source_type=overview_type,
                          source=overview_src, date=overview_date or reviewed_at, seq=0,
                          data_status=status_of('overview')))

        # 1.1 交通参考（独立成块：它的出处通常不是官网，不能跟着概览一起显示"官方直采"）
        if data.get('transport_hint'):
            transport_type, transport_src, transport_date = src('transport')
            docs.append(build(f'{name}交通参考：{data["transport_hint"]}。',
                              resort_id=resort_id, resort_name=name, doc_type='transport',
                              source_type=transport_type, source=transport_src,
                              date=transport_date or reviewed_at, seq=1,
                              data_status=status_of('transport')))

        # 入库文档的安全长度预算：低于阈值才不会被分块器接手（见 _split_long_text）
        budget = settings.RAG_CHUNK_SIZE - 40

        # 1.2 可信度说明：**每条提醒单独成块**
        #     为什么逐条、而不是拼成一整块：拼起来会超过分块阈值，一超过就会去构造
        #     langchain_text_splitters —— 那个依赖在本机导入即挂死（坑 #10）。
        #     逐条还有个好处：用户问"这数据几手的"时，命中的是具体那条提醒，而不是一整段。
        for index, caveat in enumerate(prov['caveats']):
            lead = (f'{name}的数据可信度说明（{DATA_STATUS_LABEL[status_of("overview")]}）：'
                    if index == 0 else f'{name}可信度补充：')
            # 同样按句切块：单条提醒也可能超过阈值（见 _split_long_text 的说明）
            for block in _split_long_text(lead + caveat, budget):
                docs.append(build(block, resort_id=resort_id, resort_name=name,
                                  doc_type='provenance', source_type=overview_type,
                                  source=overview_src, date=overview_date or reviewed_at,
                                  # 元信息不挂档位：挂上去就会被模型当成"数据本身的档位"
                                  seq=890 + index, data_status=''))

        # 1.5 来源登记：逐字段的交叉印证记录，**每个字段一条短文档**
        #     为什么值得单独成块：用户问"你这数据几手的、可信吗"时，
        #     检索要能命中一份**写清楚印证与分歧**的记录，
        #     而不是让模型凭印象说一句"应该挺可靠的"。
        #     为什么拆成一条条短的：整份登记会超过分块阈值，一超过就会去构造
        #     langchain_text_splitters —— 那个依赖在本机导入即挂死（docs/TROUBLESHOOTING.md #10），
        #     表现为"入库没有任何输出地卡住"。一个字段一条，既避开它，
        #     也符合本项目"一个 chunk 一个语义单元"的入库约定。
        #     单条记录本身过长时（claim 写得很细的那种），再把"骨架"和"细节"拆两块。
        prov_seq = 900
        for entry in prov['corroborations']:
            prov_type, prov_src, prov_date = src('provenance')
            head = (f'{name}的数据来源登记（{entry["field"]}）：'
                    + ' '.join(ResortProvenance.corroboration_caveats(
                        [entry], include_note=False)))
            tail = (f'{name}的来源登记细节（{entry["field"]}）：{entry["note"]}'
                    if entry['note'] else '')
            # 骨架与细节各自按句切块：单条记录本身也可能超过阈值（实测 525 字）
            for block in _split_long_text(head, budget) + _split_long_text(tail, budget):
                docs.append(build(
                    block, resort_id=resort_id, resort_name=name, doc_type='provenance',
                    source_type=prov_type, source=prov_src,
                    date=prov_date or reviewed_at, seq=prov_seq,
                    # 这条记录涉及的数据类型是什么档，就用什么档（没写才退回雪场默认档）
                    data_status=entry['data_status'] or status_of('overview')))
                prov_seq += 1

        # 2.雪道总览（回答"有几条高级道"这类统计问题，打 is_summary 供提权）
        summary = data.get('runs_summary') or {}
        if summary:
            total = sum(int(v) for v in summary.values() if v)
            reported_total = official.get('runs_total')
            text = f'{name}雪道总览：'
            if reported_total:
                text += f'档案来源口径总数 {reported_total} 条雪道；'
            text += (f'分级统计合计 {total} 条——'
                     f'初级道 {summary.get("beginner", 0)} 条、'
                     f'中级道 {summary.get("intermediate", 0)} 条、'
                     f'高级道 {summary.get("advanced", 0)} 条。')
            if reported_total and int(reported_total) != total:
                text += '分级合计与来源总数不一致，不能将分级合计当作雪道总数。'
            if official.get('source_note'):
                text += f'口径说明：{official["source_note"]}'
            run_type, run_src, run_date = src('run')
            docs.append(build(text + caveat_head('run'), resort_id=resort_id, resort_name=name,
                              doc_type='run', source_type=run_type, source=run_src,
                              date=run_date, seq=1, is_summary=True, data_status=status_of('run')))

        # 3.每条雪道（坡度/海拔比落差更能回答"陡不陡、我能不能下"）
        for index, run in enumerate(data.get('runs') or [], start=2):
            level = DIFFICULTY_TO_LEVEL.get(str(run.get('difficulty', '')), 'all')
            parts = [f'{name}的雪道「{run.get("name", "未命名")}」：难度{run.get("difficulty", "未知")}'
                     f'（{LEVEL_LABEL.get(level, level)}）']
            if run.get('length_m'):
                parts.append(f'长度约 {run["length_m"]} 米')
            if run.get('vertical_m'):
                parts.append(f'落差约 {run["vertical_m"]} 米')
            if run.get('top_altitude_m'):
                parts.append(f'最高海拔约 {run["top_altitude_m"]} 米')
            if run.get('avg_slope_deg'):
                parts.append(f'平均坡度约 {run["avg_slope_deg"]} 度')
            if run.get('max_slope_deg'):
                parts.append(f'最大坡度约 {run["max_slope_deg"]} 度')
            if run.get('note'):
                parts.append(str(run['note']))
            run_type, run_src, item_date = src('run', run)
            docs.append(build('，'.join(parts) + '。', resort_id=resort_id, resort_name=name,
                              doc_type='run', level=level, source_type=run_type,
                              source=run_src, date=item_date, seq=index,
                              run_name=str(run.get('name', '')), data_status=status_of('run')))

        # 4.价格（带录入日期，供保鲜期检查）
        validate_prices(resort_id, data.get('prices') or [])
        for index, price in enumerate(data.get('prices') or []):
            price_type, price_src, default_date = src('price', price)
            updated = str(price.get('updated_at') or default_date or price_date)
            docs.append(build(price_text(name, price, updated), resort_id=resort_id,
                              resort_name=name, doc_type='price',
                              source_type=price_type, source=price_src, date=updated,
                              seq=index, item=str(price.get('item', '')),
                              data_status=price.get('data_status') or status_of('price')))

        # 5.索道/缆车/魔毯（只进 RAG，不建表：它回答的是"排队久不久、运力够不够"这类体验问题）
        #
        # ⚠️ 每个块都带上**总数**（2026-10-05 端到端评测抓到的误伤）：
        #    模型答"万龙有 5 条索道"——**它是对的**（数了明细），但证据里没有"共 5 条"
        #    这句话，事实校验按"数字必须有出处"把它判成编造 → 整条回答降级成拒答。
        #    雪道没暴露这个问题，是因为雪道有 runs_summary（"共 32 条"）可引；
        #    索道没有总数，模型一"数条目"就踩雷。
        #    教训：**让证据自己写出可被引用的数字**，比事后放宽校验规则安全得多。
        lift_rows = data.get('lifts') or []
        lift_total = len(lift_rows)
        for index, lift in enumerate(lift_rows, start=200):
            parts = [f'{name}本库共收录 {lift_total} 条索道/缆车/魔毯',
                     f'本条为{lift.get("category", "索道")}「{lift.get("name", "未命名")}」']
            if lift.get('spec'):
                parts.append(f'规格 {lift["spec"]}')
            if lift.get('length_m'):
                parts.append(f'长度约 {lift["length_m"]} 米')
            if lift.get('capacity_per_hour'):
                parts.append(f'每小时运力约 {lift["capacity_per_hour"]} 人')
            if lift.get('serves'):
                parts.append(f'服务对象：{lift["serves"]}')
            if lift.get('note'):
                parts.append(str(lift['note']))
            lift_type, lift_src, lift_date = src('lift', lift)
            docs.append(build('，'.join(parts) + '。', resort_id=resort_id, resort_name=name,
                              doc_type='lift', source_type=lift_type, source=lift_src,
                              date=lift_date, seq=index, data_status=status_of('lift')))

        # 6.分层经验
        for index, (level, info) in enumerate((data.get('by_level') or {}).items()):
            if not isinstance(info, dict):
                continue
            text = f'{name}对{LEVEL_LABEL.get(level, level)}的建议：{info.get("summary", "")}'
            if info.get('tips'):
                text += ' 注意：' + '；'.join(str(t) for t in info['tips']) + '。'
            docs.append(build(text, resort_id=resort_id, resort_name=name, doc_type='tip',
                              level=level, source_type='C',
                              source=f'{name}雪友经验整理', date=reviewed_at, seq=index))

        # 7.避坑
        for index, tip in enumerate(data.get('tips') or [], start=100):
            docs.append(build(f'{name}避坑：{tip.get("text", "")}', resort_id=resort_id,
                              resort_name=name, doc_type='tip',
                              source_type=str(tip.get('source_type', 'C')),
                              source=str(tip.get('source', '未标注来源')),
                              date=str(tip.get('date') or reviewed_at), seq=index))

        # 8.教学视频卡
        for index, video in enumerate(data.get('videos') or []):
            board = BOARD_TO_CODE.get(str(video.get('board', '')), 'all')
            level = str(video.get('level', 'all'))
            text = (f'教学视频《{video.get("title", "")}》：适合{LEVEL_LABEL.get(level, level)}｜'
                    f'{video.get("board", "通用")}｜时长 {video.get("duration", "未知")}。')
            if video.get('key_points'):
                text += '要点：' + '；'.join(str(p) for p in video['key_points']) + '。'
            # 链接为空时不要输出"视频链接："这种半截话——占位链接不如不写
            url = str(video.get('url') or '')
            if not url:
                text += '（视频链接待补充）'
            docs.append(build(text, resort_id=resort_id, resort_name=name, doc_type='video',
                              level=level, board_type=board, source_type='C',
                              source=f'视频链接：{url}' if url else '教学视频卡（链接待补充）',
                              date=reviewed_at, seq=index, url=url))

        # 9.实拍图（B 级，带拍摄日期）
        for index, photo in enumerate(data.get('photos') or []):
            shot_at = str(photo.get('shot_at') or '')
            text = (f'{name}实拍：{photo.get("location", "未标注位置")}'
                    f'（拍摄于 {shot_at}，拍摄者 {photo.get("credit", "未标注")}）')
            docs.append(build(text, resort_id=resort_id, resort_name=name, doc_type='photo',
                              source_type='B', source=f'实拍图 {photo.get("key", "")}',
                              date=shot_at, seq=index, photo_key=str(photo.get('key', ''))))

        return docs

    @staticmethod
    def parse_markdown(path: str, metadata: dict | None = None) -> list[Document]:
        """加载长文手册（md/txt），返回原始 Document（分块交给 ChunkUtil）"""
        text = Path(path).read_text(encoding='utf-8')
        meta = {'doc_type': 'handbook', 'source_type': 'A',
                'chunk_id': f'{Path(path).stem}:000'}
        meta.update(metadata or {})
        return [Document(page_content=text, metadata=meta)]

    @staticmethod
    def parse_pdf(path: str, metadata: dict | None = None) -> list[Document]:
        """加载 PDF 手册。扫描件解析不出文字，需要先 OCR

        ⚠️ PDF 解析是**可选扩展**（requirements-optional.txt）：PyMuPDF 是
        AGPL-3.0 双许可（或 Artifex 商业许可），放进核心依赖会污染整个项目的许可合规性；
        而当前语料全是 YAML，主流程用不到它。所以这里给人话提示，而不是让用户撞
        一个 ModuleNotFoundError。
        """
        # 懒加载 langchain_community，避免影响无关模块导入
        try:
            from langchain_community.document_loaders import PyMuPDFLoader
        except ImportError as exc:
            raise ServiceException(
                '解析 PDF 需要可选依赖 PyMuPDF（AGPL-3.0 双许可）：'
                'pip install -r requirements-optional.txt'
                '（核心流程只读 YAML 档案，不需要它）') from exc

        docs = PyMuPDFLoader(path).load()
        for index, doc in enumerate(docs):
            doc.metadata.update({
                'doc_type': 'handbook', 'source_type': 'A',
                'chunk_id': f'{Path(path).stem}:{index:03d}',
                **(metadata or {}),
            })
        return docs

    @staticmethod
    def parse_all(resort_ids: list[str] | None = None) -> list[Document]:
        """加载全部（或指定）雪场档案，入库脚本入口"""
        ids = resort_ids or ResortRegistry.list_resort_ids()
        if not ids:
            raise ServiceException(f'{settings.RESORT_DIR} 下没有雪场档案（*.yaml）')
        docs: list[Document] = []
        for resort_id in ids:
            docs.extend(ResortParser.parse_resort(resort_id))
        return docs


# ---------------- 兼容旧调用（模块级别名，import 不用改） ----------------
list_resort_ids = ResortRegistry.list_resort_ids
resort_name_map = ResortRegistry.name_map
uncovered_resort_map = ResortRegistry.uncovered_map
detect_resort = ResortRegistry.detect
detect_unknown_resort = ResortRegistry.detect_unknown
load_yaml_resort = ResortParser.parse_resort
load_all_resorts = ResortParser.parse_all
load_markdown = ResortParser.parse_markdown
load_pdf = ResortParser.parse_pdf
resolve_provenance = ResortProvenance.resolve
source_of = ResortProvenance.source_of
