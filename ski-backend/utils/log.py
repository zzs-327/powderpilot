"""统一日志：把关键诊断落到文件，而不是只留在"启动它的那个控制台"里。

为什么必须落盘（2026-09-12 实际踩到）：
    引用校验打回重写时会在控制台打一行 `[citation] 校验未通过：...`，
    那是排查"到底真抓到幻觉、还是校验误伤了正常回答"的**唯一线索**。
    但服务是在某个终端 / IDE 里起的，stdout 没人接：用户只看到回答变成了
    拒答话术，我们看不到原因——**等于自断线索**（当时只能靠猜"35 是谁算出来的"）。
    所以诊断信息一律走 logging：同时进控制台和 logs/backend.log。

为什么不用 main.py 里的 basicConfig 就够了：
    basicConfig 只配 root 的控制台输出，进程一关就什么都没了。
    这里补的是**文件句柄**，服务跑到哪一步都能事后翻。

用法：
    from utils.log import LogUtil
    logger = LogUtil.get_logger('citation')
    logger.warning('引用校验未通过：%s', reason)
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config.config import settings

# 日志格式与 main.py 的控制台格式保持一致，方便两边对照着看。
# 带进程号（pid）：服务、压测脚本、离线测试会同时往同一个文件里写，
# 不带 pid 就分不清某条打回是谁产生的——压测统计会被别的进程污染。
_FORMAT = '%(asctime)s %(levelname)s [%(name)s:%(process)d] %(message)s'
_MAX_BYTES = 2 * 1024 * 1024    # 单文件 2MB
_BACKUP_COUNT = 3               # 保留 3 个滚动备份

# 已经装配过 handler 的 logger 名，避免重复 addHandler（同一行会打两遍）
_configured: set[str] = set()


class LogUtil:
    """日志工厂：同名 logger 只装配一次 handler"""

    @staticmethod
    def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
        """取一个"控制台 + 文件"双输出的 logger

        # 1.装配过就直接返回，重复装配会让同一行日志出现两次
        # 2.文件：带滚动切分，避免长期跑把磁盘写满；写不进去也不能反过来搞挂服务
        # 3.propagate 关掉：否则 root 的 basicConfig handler 会再打一遍
        """
        logger = logging.getLogger(name)
        if name in _configured:
            return logger

        logger.setLevel(level)
        formatter = logging.Formatter(_FORMAT)

        # 控制台：独立脚本（chat_cli / stress_citation）没人调 basicConfig，这里自己带
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)

        # 文件：落盘才是重点——服务起在哪个终端里都不影响事后排查
        try:
            from pathlib import Path
            log_dir = Path(settings.LOG_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / 'backend.log', maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT, encoding='utf-8')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:      # 只读盘 / 权限不足：降级成控制台输出
            logger.warning('日志文件不可写（%s），本次仅输出到控制台', exc)

        logger.propagate = False
        _configured.add(name)
        return logger
