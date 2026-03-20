"""Logging setup using loguru."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import sys
import unicodedata

from loguru import logger

from config import LOG_DIR

def _as_ascii_text(text: str) -> str:
    """Return a console-safe ASCII string for environments with limited encodings."""
    replacements = {
        "┌": "+",
        "┐": "+",
        "└": "+",
        "┘": "+",
        "├": "+",
        "┤": "+",
        "┬": "+",
        "┴": "+",
        "┼": "+",
        "─": "-",
        "━": "-",
        "│": "|",
        "┃": "|",
        "▏": "|",
        "▕": "|",
        "—": "-",
        "➜": "->",
        "→": "->",
        "…": "...",
        "✅": "[ok]",
        "✓": "v",
        "✕": "x",
        "⚠": "warning",
        "🔑": "key",
        "🌐": "internet",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _console_sink(message: Any) -> None:
    """Render logs with a safe ASCII console format."""
    record = message.record
    level_name = record["level"].name
    logger_line = (
        f"{record['time'].strftime('%H:%M:%S')} | {level_name: <8} | "
        f"{Path(record['name']).name}:{record['function']}:{record['line']} - "
        f"{record['message']}"
    )
    print(_as_ascii_text(logger_line), file=sys.stdout, flush=True)

    if record["exception"] is not None:
        print(f"Exception: {_as_ascii_text(str(record['exception']))}", file=sys.stdout, flush=True)

def setup_logger() -> None:
    """Configure loguru with console + file sinks."""
    logger.remove()  # Remove default handler

    # Console — ASCII-safe, Windows-friendly format
    logger.add(_console_sink, level="INFO")

    # File — detailed, rotated daily
    log_file = LOG_DIR / f"bot_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger.add(
        str(log_file),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
    )

    logger.info("Logger initialised - file: {}", log_file.name)


setup_logger()
