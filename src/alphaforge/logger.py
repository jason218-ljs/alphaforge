"""AlphaForge 日志（文件 + 控制台，rotate）。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_INITIALIZED = False


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None) -> None:
    """初始化根日志器。

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）。
        log_file: 日志文件路径；None 则仅控制台输出。
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    root = logging.getLogger("alphaforge")
    if root.handlers:
        root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(_DEFAULT_FORMAT, _DATE_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # 避免上层 root logger 重复输出
    root.propagate = False
    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """获取模块 logger。"""
    return logging.getLogger(f"alphaforge.{name}")