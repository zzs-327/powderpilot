"""每轮对话的账本（本地可观测性，零外部依赖）。

为什么需要（2026-10-05）：
    我们能说出"排行程贵 6 倍""Agent 缓存省 1.7 秒",靠的都是临时诊断脚本——
    跑完就没了，也没法回答"最近 50 轮里 P95 是多少、降级率多少"。
    面试/答辩/企业沟通都会被问到这两类数字，所以必须**持续记录**。

为什么不用 Langfuse：
    它是更完整的方案，但要自建 Postgres + Clickhouse（服务器只有 2 核 3.6G，已经在挤内存）。
    先落一个 JSONL 账本：够用、零依赖、可随时被替换（真接 Langfuse 时这层就是数据源）。

记录什么（一行一轮，追加到 logs/turns.jsonl）：
    ts / thread / model / 模型调用次数 / 输入输出 token / 工具序列 /
    耗时 / 是否降级 / 是否出卡 / 引用编号个数

设计取舍：
    - **只记数字与工具名，不记用户原话与回答正文**：账本会长期留在磁盘上，
      正文里可能含用户信息；要复现问题有 logs/backend.log 的告警与判罚原文。
    - 写失败不能影响对话：整个记录过程包在 try 里（观测是附属品，不是主流程）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from config.config import settings


class TurnMetrics(BaseCallbackHandler):
    """挂在 config.callbacks 上，收集一轮对话的用量与耗时"""

    def __init__(self) -> None:
        self.started = time.time()
        self.model_calls = 0
        self.tools: list[str] = []
        self.input_tokens = 0
        self.output_tokens = 0
        self._llm_started: dict[Any, float] = {}

    # ---------------- 模型 ----------------
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self.model_calls += 1
        self._llm_started[kwargs.get('run_id')] = time.time()

    def on_llm_end(self, response, **kwargs) -> None:
        self._llm_started.pop(kwargs.get('run_id'), None)
        usage = _usage_of(response)
        self.input_tokens += usage[0]
        self.output_tokens += usage[1]

    def on_llm_error(self, error, **kwargs) -> None:
        self._llm_started.pop(kwargs.get('run_id'), None)

    # ---------------- 工具 ----------------
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        name = (serialized or {}).get('name') or 'unknown'
        self.tools.append(str(name))

    # ---------------- 汇总 ----------------
    def snapshot(self, *, degraded: bool = False, card: bool = False,
                 citations: int = 0, model: str = '') -> dict:
        return {
            'ts': datetime.now().isoformat(timespec='seconds'),
            'thread': '',
            'model': model or f'{settings.LLM_PROVIDER}/{settings.LLM_MODEL}',
            'model_calls': self.model_calls,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'tools': self.tools,
            'seconds': round(time.time() - self.started, 1),
            'degraded': bool(degraded),
            'card': bool(card),
            'citations': int(citations),
        }


def _usage_of(response: Any) -> tuple[int, int]:
    """从 on_llm_end 的响应里取 (输入, 输出) token

    两条来源都试：新版 langchain 把用量放在 message.usage_metadata，
    老版/部分供应商仍走 llm_output['token_usage']。取不到就记 0（不抛异常）。
    """
    try:
        llm_output = getattr(response, 'llm_output', None) or {}
        usage = llm_output.get('token_usage') or llm_output.get('usage') or {}
        if usage:
            return (int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0),
                    int(usage.get('completion_tokens') or usage.get('output_tokens') or 0))
        for generation in (getattr(response, 'generations', None) or []):
            for item in generation or []:
                message = getattr(item, 'message', None)
                meta = getattr(message, 'usage_metadata', None) or {}
                if meta:
                    return (int(meta.get('input_tokens') or 0),
                            int(meta.get('output_tokens') or 0))
    except Exception:                                            # noqa: BLE001
        pass
    return (0, 0)


def record_turn(metrics: TurnMetrics, *, thread: str = '', degraded: bool = False,
                card: bool = False, citations: int = 0) -> None:
    """把一轮账本追加到 logs/turns.jsonl（失败只告警，绝不影响对话）"""
    try:
        path = Path(settings.LOG_DIR) / 'turns.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        row = metrics.snapshot(degraded=degraded, card=card, citations=citations)
        row['thread'] = thread
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    except Exception as exc:                                     # noqa: BLE001
        import logging

        logging.getLogger('ski.agent').warning('账本写入失败（不影响对话）：%s', exc)
