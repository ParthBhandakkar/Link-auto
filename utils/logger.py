"""Logging setup using loguru."""
from __future__ import annotations

import sys
from datetime import datetime

from loguru import logger

from config import LOG_DIR


def setup_logger() -> None:
    """Configure loguru with console + file sinks."""
    logger.remove()  # Remove default handler

    # Console — coloured, concise
    logger.add(
        sys.stdout,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File — detailed, rotated daily
    log_file = LOG_DIR / f"bot_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger.add(
        str(log_file),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} — {message}",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
    )

    logger.info("Logger initialised — file: {}", log_file.name)


setup_logger()
