"""General helper utilities."""
from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from loguru import logger


async def human_delay(min_sec: float = 1.0, max_sec: float = 3.0) -> None:
    """Sleep for a random human-like duration."""
    delay = random.uniform(min_sec, max_sec)
    logger.debug("Sleeping {:.1f}s (human delay)", delay)
    await asyncio.sleep(delay)


async def random_scroll(page: Any, direction: str = "down", amount: int = 300) -> None:
    """Scroll the page by a random amount to seem human."""
    scroll_amount = random.randint(amount - 100, amount + 200)
    if direction == "up":
        scroll_amount = -scroll_amount
    await page.mouse.wheel(0, scroll_amount)
    await human_delay(0.3, 0.8)


def clean_text(text: str) -> str:
    """Strip HTML/whitespace artefacts from text."""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_salary(text: str) -> str:
    """Try to pull a salary string out of job description text."""
    patterns = [
        r"₹[\d,]+\s*[-–]\s*₹[\d,]+",
        r"\$[\d,]+\s*[-–]\s*\$[\d,]+",
        r"[\d,]+\s*(?:LPA|lpa|CTC|ctc|per\s*annum)",
    ]
    for p in patterns:
        match = re.search(p, text)
        if match:
            return match.group(0)
    return ""


def truncate(text: str, max_len: int = 500) -> str:
    """Truncate text to max_len chars with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
