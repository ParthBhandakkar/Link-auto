"""Logging setup using loguru."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from rich.console import Console
from rich.text import Text

from config import LOG_DIR

_RICH_CONSOLE = Console(force_terminal=True, color_system="auto")
_LEVEL_STYLES = {
    "TRACE": "cyan",
    "DEBUG": "blue",
    "INFO": "bright_green",
    "SUCCESS": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bright_red",
}


def _rich_sink(message: Any) -> None:
    """Render logs with Rich for cleaner terminal readability."""
    record = message.record
    level_name = record["level"].name
    style = _LEVEL_STYLES.get(level_name, "white")
    logger_line = Text()
    logger_line.append(record["time"].strftime("%H:%M:%S"), style="dim")
    logger_line.append(" │ ")
    logger_line.append(f"{level_name: <8}", style=f"bold {style}")
    logger_line.append(" │ ")
    logger_line.append(
        f"{Path(record['name']).name}:{record['function']}:{record['line']}",
        style="blue",
    )
    logger_line.append(" ─ ")
    logger_line.append(str(record["message"]), style="white")
    _RICH_CONSOLE.print(logger_line)

    if record["exception"] is not None:
        _RICH_CONSOLE.print(Text(str(record["exception"]), style="red"))

def setup_logger() -> None:
    """Configure loguru with console + file sinks."""
    logger.remove()  # Remove default handler

    # Console — Rich, colourful, structured
    logger.add(_rich_sink, level="INFO")

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
