"""
LLM Client — unified interface for Kimi K2.5, Ollama, and OpenAI-compatible APIs.

Uses the OpenAI SDK (which works with all three providers) so we can switch
between providers by changing the base URL and API key.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings


class LLMClient:
    """Async wrapper around the configured LLM provider."""

    def __init__(self) -> None:
        provider = settings.llm_provider
        # Fall back to Ollama if Kimi is selected but API key is missing
        if provider == "kimi" and not (settings.kimi_api_key or "").strip():
            logger.info("KIMI_API_KEY missing — falling back to Ollama")
            provider = "ollama"
        if provider == "kimi":
            self._client = AsyncOpenAI(
                api_key=settings.kimi_api_key,
                base_url=settings.kimi_base_url,
            )
            self._model = settings.kimi_model
        elif provider == "ollama":
            self._client = AsyncOpenAI(
                api_key="ollama",  # Ollama doesn't need a real key
                base_url=f"{settings.ollama_base_url}/v1",
            )
            self._model = settings.ollama_model
        elif provider == "openai":
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )
            self._model = settings.openai_model
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

        logger.info("LLM client initialised — provider={}, model={}", provider, self._model)

    # ── Text completion ─────────────────────────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Send a text chat message and return the assistant's reply."""
        logger.debug("LLM chat — system_prompt[:80]={!r}", system_prompt[:80])
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content or ""
        logger.debug("LLM reply[:200]={!r}", text[:200])
        return text.strip()

    # ── Vision (screenshot analysis) ────────────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def chat_with_image(
        self,
        system_prompt: str,
        user_message: str,
        image_path: str | Path,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Send a message with an image (screenshot) for visual analysis."""
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Screenshot not found: {image_path}")

        img_bytes = image_path.read_bytes()
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        mime = "image/png" if image_path.suffix == ".png" else "image/jpeg"

        logger.debug("LLM vision — image={}, size={:.0f}KB", image_path.name, len(img_bytes) / 1024)

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_message},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64}",
                            },
                        },
                    ],
                },
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content or ""
        logger.debug("LLM vision reply[:200]={!r}", text[:200])
        return text.strip()

    # ── Structured JSON output ──────────────────────────────────────────
    async def chat_json(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Send a chat and parse the reply as JSON."""
        raw = await self.chat(
            system_prompt=system_prompt + "\n\nYou MUST respond with valid JSON only. No markdown, no explanation.",
            user_message=user_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (```json / ```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON — raw[:300]={!r}", raw[:300])
            # Try to extract JSON from the response
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start != -1 and end > start:
                return json.loads(cleaned[start:end])
            raise

    async def chat_json_with_image(
        self,
        system_prompt: str,
        user_message: str,
        image_path: str | Path,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Send a message with image and parse reply as JSON."""
        raw = await self.chat_with_image(
            system_prompt=system_prompt + "\n\nYou MUST respond with valid JSON only. No markdown, no explanation.",
            user_message=user_message,
            image_path=image_path,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM vision JSON — raw[:300]={!r}", raw[:300])
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start != -1 and end > start:
                return json.loads(cleaned[start:end])
            raise


# Singleton
llm_client = LLMClient()
