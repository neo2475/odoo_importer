# core/logger.py
from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger

_DEF_FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level:<8}</level> | "
    "{name}:{function}:{line} - <level>{message}</level>"
)


def setup_logging():
    level = os.getenv("LOG_LEVEL", "INFO")
    logger.remove()
    logger.add(sys.stderr, level=level, format=_DEF_FMT)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "app.log",
        level=level,
        format=_DEF_FMT,
        rotation="10 MB",
        retention="10 days",
        encoding="utf-8",
    )


def get_logger():
    return logger

