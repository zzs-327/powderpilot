"""雪道展示：档案参数单独成卡，两个口径被说成一个总数时补一句。

模型正文仍然要引用资料。这层只做两件模型做不稳的事：
把长度、落差、坡度按档案原样摆出来；发现「总共 N 条」漏了另一个口径时补上。
"""
import re
from pathlib import Path

import yaml

from config.config import settings
from schemas.guidance import RunBrief, RunFact

_LEVEL = (('beginner', '初级'), ('intermediate', '中级'), ('advanced', '高级'))
_RUN_ASK = re.compile(r'雪道|坡度|滑哪|夜场|夜滑|初级|中级|高级|难度')
_BEGINNER = re.compile(r'新手|第一次|初学|入门|零基础')
_COUNT = re.compile(r'几条|多少条|总数|总共|有多少')
_TOTAL_PAIR = re.compile(
    r'(?:档案来源口径总数|一个来源称)\s*(\d+)\s*条[\s\S]{0,160}?'
    r'(?:分级统计合计|分难度合计为)\s*(\d+)\s*条')
_TOTAL_CLAIM = re.compile(r'(?:总共|一共|共有|共)\s*有?\s*(\d+)\s*条')


def _load(resort_id: str) -> dict:
    if not re.fullmatch(r'[a-z0-9_-]+', resort_id or ''):
        return {}
    path = Path(settings.RESORT_DIR) / f'{resort_id}.yaml'
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _aliases(name: str) -> list[str]:
    code = re.match(r'[A-Z]+\d+', name)
    rest = name[code.end():] if code else ''
    return [item for item in (name, rest, code.group(0) if code else '') if len(item) >= 2]


def _hits(text: str, runs: list[dict]) -> list[dict]:
    found = []
    for run in runs:
        name = str(run.get('name') or '')
        position = None
        for alias in _aliases(name):
            if re.fullmatch(r'[A-Z]+\d+', alias):
                match = re.search(r'(?<![A-Z0-9])' + re.escape(alias) + r'(?!\d)', text)
                index = match.start() if match else -1
            else:
                index = text.find(alias)
            if index >= 0 and (position is None or index < position[0] or (
                    index == position[0] and len(alias) > position[1])):
                position = (index, len(alias))
        if position is not None:
            found.append((position[0], -position[1], run))
    found.sort(key=lambda item: (item[0], item[1]))
    chosen = []
    seen = set()
    for _, _, run in found:
        name = str(run.get('name') or '')
        if name in seen:
            continue
        seen.add(name)
        chosen.append(run)
    return chosen


def _note(run: dict, data: dict) -> str:
    aliases = _aliases(str(run.get('name') or ''))
    tips = []
    for info in (data.get('by_level') or {}).values():
        if isinstance(info, dict):
            tips.extend(str(item) for item in info.get('tips') or [])
    for tip in data.get('tips') or []:
        if isinstance(tip, dict):
            tips.append(str(tip.get('text') or ''))
    matched = [tip for tip in tips if any(alias in tip for alias in aliases)]
    if not matched:
        return ''
    return min(matched, key=len)[:120]


def _fact(run: dict, data: dict) -> RunFact | None:
    name = str(run.get('name') or '').strip()
    difficulty = str(run.get('difficulty') or '').strip()
    if not name or not difficulty:
        return None
    return RunFact(
        name=name, difficulty=difficulty,
        length_m=run.get('length_m') if isinstance(run.get('length_m'), int) else None,
        vertical_m=run.get('vertical_m') if isinstance(run.get('vertical_m'), int) else None,
        slope_deg=run.get('avg_slope_deg') if isinstance(run.get('avg_slope_deg'), (int, float)) else None,
        note=_note(run, data))


def _caveat(data: dict) -> str:
    official = (data.get('official') or {}).get('runs_total')
    summary = data.get('runs_summary') or {}
    if not isinstance(official, int) or not isinstance(summary, dict):
        return ''
    parts = []
    total = 0
    for key, label in _LEVEL:
        value = summary.get(key)
        if isinstance(value, int):
            parts.append(f'{label} {value} 条')
            total += value
    if not total or official == total:
        return ''
    return (f'两个口径：一个来源称 {official} 条；分级合计 {total} 条（{"、".join(parts)}）。'
            '不能把其中一个说成唯一总数。')


def build_run_brief(resort_id: str, resort_name: str, query: str) -> RunBrief | None:
    """问雪道时给出最多 6 条档案参数。示例数据不出卡。"""
    data = _load(resort_id)
    if not data or (data.get('provenance') or {}).get('data_status') == 'sample':
        return None
    if not query or not (_RUN_ASK.search(query) or _hits(query, data.get('runs') or [])):
        return None
    runs = [run for run in data.get('runs') or [] if isinstance(run, dict)]
    named = _hits(query, runs)
    title = '雪道档案'
    chosen = named[:6]
    if named:
        title = named[0].get('name') or title
        if len(named) > 1:
            title = '问到的雪道'
    elif re.search(r'夜场|夜滑', query):
        night = next((str(tip.get('text') or '') for tip in data.get('tips') or []
                      if isinstance(tip, dict) and re.search(r'夜场|夜滑', str(tip.get('text') or ''))
                      and '雪道' in str(tip.get('text') or '')), '')
        chosen = _hits(night, runs)[:6]
        title = '攻略里的夜场雪道'
    elif _BEGINNER.search(query):
        summary = str(((data.get('by_level') or {}).get('beginner') or {}).get('summary') or '')
        chosen = _hits(summary, runs)[:3]
        title = '新手先看这几条'
    elif not _COUNT.search(query):
        chosen = []
    facts = []
    seen_notes = set()
    for run in chosen:
        fact = _fact(run, data)
        if fact is None:
            continue
        if fact.note in seen_notes:
            fact = fact.model_copy(update={'note': ''})
        elif fact.note:
            seen_notes.add(fact.note)
        facts.append(fact)
    caveat = _caveat(data) if (_COUNT.search(query) or named or facts) else ''
    if not facts and not caveat:
        return None
    if not facts and caveat:
        title = '雪道条数有两个口径'
    source = str((data.get('provenance') or {}).get('primary_source') or f'{resort_name}档案')
    date = str((data.get('meta') or {}).get('last_reviewed_at') or '未注明')
    return RunBrief(title=title[:40], source=source[:80], date=date[:40], caveat=caveat, runs=facts)


_REFUSAL = re.compile(r'没有找到|暂无可靠来源|没有具体信息|没有收录|未检索到|无法提供具体')


def _resort_name(resort_id: str) -> str:
    from rag.parser import ResortRegistry
    names = [name for name, rid in ResortRegistry.name_map().items() if rid == resort_id]
    return max(names, key=len) if names else resort_id


def _fmt(value) -> str:
    if isinstance(value, float):
        text = f'{value:.1f}'.rstrip('0').rstrip('.')
        return text
    return str(value)


def lead_from_brief(brief: RunBrief) -> str:
    """档案里已经有雪道时，用同一份参数作开头，不再先说「没找到」。"""
    if not brief.runs:
        return brief.caveat
    if brief.title == '攻略里的夜场雪道':
        names = '、'.join(run.name for run in brief.runs)
        return f'攻略里写的夜场雪道有{names}。难度和长度在下面的档案卡里，雪季不同会变。'
    first = brief.runs[0]
    bits = [first.difficulty]
    if first.length_m:
        bits.append(f'{first.length_m} 米')
    if first.slope_deg is not None:
        bits.append(f'平均坡度约 {_fmt(first.slope_deg)}°')
    rest = brief.runs[1:3]
    others = '、'.join(run.name for run in rest)
    text = f'可以先看{first.name}（{"，".join(bits)}）。'
    if others:
        same = all(run.difficulty == first.difficulty for run in rest)
        text += f'{"同档还有" if same else "另外还有"}{others}。'
    if first.note:
        text += first.note if first.note.endswith(('。', '！', '？')) else first.note + '。'
    text += '下面卡片是同一份档案，不是模型另外编的。'
    return text


def present_reply(reply: str, evidence: str, question: str = '',
                  prior_humans: list[str] | None = None) -> str:
    """正文已经通过引用校验之后，补漏掉的口径，并避免「没找到」和档案卡打架。"""
    text = (reply or '').strip()
    if not text:
        return text
    text = _prefer_archive(text, question, prior_humans or [])
    pair = _TOTAL_PAIR.search(evidence or '')
    claims = _TOTAL_CLAIM.findall(text)
    if pair and claims:
        first, second = pair.group(1), pair.group(2)
        if first != second and any(item in (first, second) for item in claims) and not (
                first in text and second in text):
            text = _replace_single_total(text, first, second)
    if '未观看' in (evidence or '') and '未观看' not in text and '没有看过' not in text and '推荐' in text:
        text = re.sub(r'(滑雪视频|视频)推荐', r'\1链接', text, count=1)
        text += '\n\n这些链接只核对了页面或标题，内容未观看，不能当成已验证的推荐。'
    return text


def _replace_single_total(text: str, first: str, second: str) -> str:
    """把「总共 74 条」换成两个口径，保留同一句里已经写对的雪道参数。"""
    pattern = re.compile(
        rf'(?:总共|一共|共有|共)\s*有?\s*(?:{re.escape(first)}|{re.escape(second)})\s*条(?:雪道)?'
        rf'(?:，[^。！？\n]{{0,48}}(?:包括|其中)[^。！？\n]*)?')
    replacement = (f'雪道条数有两个口径：一个来源称 {first} 条，分级合计 {second} 条，'
                   f'不能把其中一个说成唯一总数')
    updated, count = pattern.subn(replacement, text, count=1)
    if count:
        return updated
    return text + (f'\n\n补充（档案里的两个口径，不是估算）：一个来源称 {first} 条，'
                   f'分级合计 {second} 条。两者不一致，不能把其中一个说成唯一总数。')


def _names_runs(text: str, brief: RunBrief) -> bool:
    return any(run.name and run.name in text for run in brief.runs)


def _resolve_resort(question: str, reply: str, prior_humans: list[str]) -> str:
    """这一句没写雪场时，用回答里的雪场名，再往前看上一轮。"""
    from rag.parser import ResortRegistry
    for blob in (question, reply, *reversed(prior_humans)):
        resort_id = ResortRegistry.detect(blob or '')
        if resort_id:
            return resort_id
    return ''


def _ikon_text(resort_id: str) -> str:
    data = _load(resort_id)
    for tip in data.get('tips') or []:
        if not isinstance(tip, dict):
            continue
        text = ' '.join(str(tip.get('text') or '').split())
        if 'Ikon' in text and '不可用日期' in text:
            return text
    return ''


def _prefer_archive(text: str, question: str, prior_humans: list[str]) -> str:
    if not question or not _REFUSAL.search(text):
        return text
    resort_id = _resolve_resort(question, text, prior_humans)
    if not resort_id:
        return text
    brief = build_run_brief(resort_id, _resort_name(resort_id), question)
    if brief is not None and brief.runs and not _names_runs(text, brief):
        return lead_from_brief(brief)
    if brief is not None and not brief.runs and brief.caveat and '两个口径' not in text:
        return brief.caveat
    if re.search(r'Ikon|通行证', question, re.I):
        tip = _ikon_text(resort_id)
        if tip and '不可用日期' not in text:
            return tip
    if re.search(r'视频|照片|图片|雾凇|打卡', question):
        from service.media_service import media_for_resorts
        if media_for_resorts([resort_id], query=question):
            return '下面是和这个问题相关的已收录链接。没看过内容的只提供链接，不是已验证的推荐。'
    return text
