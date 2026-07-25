"""Arvan Cloud AI gateway (OpenAI-compatible) configuration — chat rewrite only."""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from dotenv import load_dotenv


DEFAULT_MODEL = "Gemini-3.1-Flash-Lite-Preview"
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ArvanAIConfig:
    api_key: str
    chat_url: str
    model: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def load_arvan_ai_config() -> ArvanAIConfig:
    load_dotenv(settings.BASE_DIR / ".env")

    api_key = os.getenv("ARVAN_AI_API_KEY", "").strip()
    chat_url = os.getenv("ARVAN_AI_CHAT_URL", "").strip()
    model = os.getenv("ARVAN_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    if not api_key or api_key.startswith("your_"):
        raise ValueError(
            "ARVAN_AI_API_KEY is not set. Add your Arvan Cloud AI apikey to .env."
        )
    if not chat_url or "your_" in chat_url:
        raise ValueError(
            "ARVAN_AI_CHAT_URL is not set. Add the full "
            ".../v1/chat/completions gateway URL to .env."
        )

    timeout = int(os.getenv("ARVAN_AI_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))

    return ArvanAIConfig(
        api_key=api_key,
        chat_url=chat_url,
        model=model,
        timeout_seconds=timeout,
    )
