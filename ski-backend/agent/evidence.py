"""证据信封（Evidence）：本项目溯源机制的核心数据结构。

为什么需要：
    医疗项目的资料来自医院官方，用户默认相信；滑雪项目里宣传图可能是照骗、
    价格可能过期、攻略可能是营销号——用户无法判断该不该信。
    所以工具返回值不能是裸文本，必须自带"凭什么信"。

    Evidence 是所有工具返回值的统一结构，由 wrap_tool_call 中间件统一包装，
    最终由 after_model 中间件校验"回答里的引用是否来自本轮证据"。
"""
import re
from dataclasses import dataclass
from datetime import date, datetime

# 置信级别：A 官方 / B 实拍 / C 经验
SOURCE_TYPE_LABEL = {'A': 'A 官方', 'B': 'B 实拍', 'C': 'C 经验'}


def normalize_citations(text: str) -> str:
    """把模型误写的 Markdown 来源链接归一为编号，后续仍校验编号白名单和事实。"""
    return re.sub(r'\[([^\]\n]+)\]\(\s*E(\d+)\s*\)', r'\1 [E\2]', text or '')

# 数据可信度分级：我们"怎么拿到这批数据"的，与 source_type（信息本身的性质）正交。
#
# 为什么两个维度都要留：
#     一条官方雪道参数被票务代理页转引后，性质仍是 A，但获取方式只是转引。
#     只留 source_type 会把转引说成官方直采；只留 data_status 又丢掉了
#     "这条参数的原始出处是官方"这个信息。
#
# ⚠️ 这份表要和 rag/parser.py 的 DATA_STATUS_LABEL **逐字一致**——
#     同一个档位在两处显示成不同说法，用户就会看到自相矛盾的可信度。
#     scripts/test_provenance.py 里有断言专门盯着这件事（防漂移）。
DATA_STATUS_LABEL = {
    'verified': '✅ 已核对（官方直采）',
    'corroborated': '🔁 多源印证（独立来源相互印证，仍非官方直采）',
    'transcribed': '⚠️ 二手转引（非官网直采）',
    'sample': '🚫 示例数据（未核实，不得作为事实引用）',
}


@dataclass
class Evidence:
    """一条可溯源的证据"""

    content: str            # 给模型看的内容
    source_type: str        # A / B / C
    source: str             # 出处：手册名 / 链接 / 拍摄者
    date: str = ''          # 信息日期（YYYY-MM-DD）
    expire_at: str = ''     # 保鲜期，空表示无时效
    level: str = 'all'      # beginner / intermediate / advanced / all
    board_type: str = 'all'  # snowboard / ski / all
    doc_type: str = ''      # run / price / tip / video / photo / lift
    resort_id: str = ''
    data_status: str = ''   # verified 官方直采 / transcribed 二手转引 / sample 示例

    @property
    def label(self) -> str:
        """中文来源标签，如"A 官方" """
        return SOURCE_TYPE_LABEL.get(self.source_type, self.source_type)

    @property
    def status_label(self) -> str:
        """可信度标签；没标注时返回空串（B/C 级经验与实拍本来就不适用这套分级）"""
        return DATA_STATUS_LABEL.get(self.data_status, '')

    @property
    def is_expired(self) -> bool:
        """是否超过保鲜期（价格 90 天 / 手册 1 季 / 教学视频无时效）"""
        if not self.expire_at:
            return False
        try:
            return datetime.strptime(self.expire_at, '%Y-%m-%d').date() < date.today()
        except ValueError:
            return False


class EvidenceFormatter:
    """证据信封构造与格式化工具"""

    @staticmethod
    def from_document(doc, index: int = 0) -> Evidence:
        """把检索到的 Document 转成 Evidence；metadata 缺失时给保守默认值"""
        meta = doc.metadata or {}
        return Evidence(
            content=doc.page_content,
            source_type=meta.get('source_type', 'C'),
            source=meta.get('source', '未标注来源'),
            date=meta.get('date', ''),
            expire_at=meta.get('expire_at', ''),
            level=meta.get('level', 'all'),
            board_type=meta.get('board_type', 'all'),
            doc_type=meta.get('doc_type', ''),
            resort_id=meta.get('resort_id', ''),
            data_status=meta.get('data_status', ''),
        )

    @staticmethod
    def format_block(evidences: list[Evidence]) -> str:
        """把证据列表拼成给模型看的文本块，每条带编号与来源，便于强制引用"""
        lines: list[str] = []
        for index, evidence in enumerate(evidences, 1):
            warn = '｜⚠️ 该信息可能已过期' if evidence.is_expired else ''
            status = f'｜{evidence.status_label}' if evidence.status_label else ''
            lines.append(f'[E{index}] ({evidence.label}｜{evidence.source}'
                         f'｜{evidence.date}{status}{warn})\n{evidence.content}')
        return '\n\n'.join(lines)


# ---------------- 兼容旧调用 ----------------
from_document = EvidenceFormatter.from_document
format_block = EvidenceFormatter.format_block

# ---------------- 证据摘要（给前端做"来源溯源"用） ----------------

# 证据头形如：[E201] (A 官方｜崇礼票务聚合站…｜2026-06-11｜⚠️ 二手转引（非官网直采）)
_EVIDENCE_LINE = re.compile(r'\[E(\d+)\]\s*\(([^)]*)\)')


class EvidenceDigest:
    """把工具返回里的证据头解析成结构化列表

    为什么需要（2026-09-13 做前端溯源时加）：
        回答里的 [E201] 对用户是个**黑箱**——他知道"有来源"，但看不到来源是什么。
        而这些信息本来就写在工具返回的证据头里（格式见 format_block），
        只是从来没露到前端。解析出来之后，回答里的编号就能点开看
        "谁说的、哪天的、几手数据"，这才是"可溯源"落到界面上的样子。
    """

    #: 档位 → 前端徽章配色（前端不硬编码中文，只认 key）
    STATUS_KEYS = ('verified', 'corroborated', 'transcribed', 'sample')

    @staticmethod
    def status_key(status_label: str) -> str:
        """从中文标签里认出档位 key（认不出返回空串，前端按"未标注"处理）"""
        if '已核对' in status_label or '官方直采' in status_label:
            return 'verified'
        if '多源印证' in status_label:
            return 'corroborated'
        if '二手转引' in status_label:
            return 'transcribed'
        if '示例数据' in status_label:
            return 'sample'
        return ''

    @staticmethod
    def of_messages(messages: list) -> list[dict]:
        """从消息列表里（通常只喂本轮的 ToolMessage）抽出证据摘要

        返回：[{id, label, source, date, status, status_key, warn, snippet}, ...]
        """
        out: list[dict] = []
        seen: set[int] = set()
        for message in messages or []:
            text = message if isinstance(message, str) else getattr(message, 'content', '')
            if isinstance(text, list):        # 兼容内容块列表（MCP 工具返回过这种）
                text = ' '.join(str(block.get('text', '')) if isinstance(block, dict) else str(block)
                                for block in text)
            for match in _EVIDENCE_LINE.finditer(str(text or '')):
                eid = int(match.group(1))
                if eid in seen:
                    continue
                seen.add(eid)
                parts = [part.strip() for part in match.group(2).split('｜')]
                label = parts[0] if parts else ''
                source = parts[1] if len(parts) > 1 else ''
                date = parts[2] if len(parts) > 2 else ''
                rest = '｜'.join(parts[3:]) if len(parts) > 3 else ''
                # 证据正文：取这一条到下一个证据头之前的文本，给前端做摘要
                tail = str(text)[match.end():]
                nxt = _EVIDENCE_LINE.search(tail)
                snippet = (tail[:nxt.start()] if nxt else tail).strip()
                out.append({
                    'id': eid,
                    'label': label,
                    'source': source,
                    'date': date,
                    'status': rest.replace('｜', ' ').strip(),
                    'status_key': EvidenceDigest.status_key(rest),
                    'warn': '可能已过期' if '过期' in rest else '',
                    # 截断长度（2026-09-13：160 → 2000）：
                    #   160 字实测**经常把证据切在半句话上**——用户点开来源，看到
                    #   "…回答时把两个口径都告诉用户，不要只报一个数，也不要断"
                    #   （本该是"不要断言"），第一反应就是"你这数据给不全"，
                    #   直接砸掉"可溯源"的说服力。
                    #   提到 800 后仍会切尾巴（万龙那条口径分歧说明有 1500+ 字），
                    #   索性放到 2000：常见证据块（雪道清单/价格表/分歧说明）都能完整放下。
                    #   前端弹层已经改成限高可滚动，长文本不会撑爆屏幕。
                    'snippet': snippet[:2000],
                })
        return sorted(out, key=lambda item: item['id'])
