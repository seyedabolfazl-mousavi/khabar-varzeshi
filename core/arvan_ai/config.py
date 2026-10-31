"""AvalAI chat configuration (OpenAI-compatible /v1/chat/completions)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from dotenv import load_dotenv


DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_BASE_URL = "https://api.avalai.ir/v1"
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ArvanAIConfig:
    api_key: str
    chat_url: str
    model: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def load_arvan_ai_config() -> ArvanAIConfig:
    load_dotenv(settings.BASE_DIR / ".env")

    api_key = os.getenv("AVALAI_API_KEY", "").strip()
    base_url = os.getenv("AVALAI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    chat_url = base_url.rstrip("/") + "/chat/completions"
    model = os.getenv("AVALAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    if not api_key or api_key.startswith("your_"):
        raise ValueError(
            "AVALAI_API_KEY is not set. Add your AvalAI key to .env "
            "(Authorization: Bearer <key>)."
        )

    timeout = int(os.getenv("AVALAI_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))

    return ArvanAIConfig(
        api_key=api_key,
        chat_url=chat_url,
        model=model,
        timeout_seconds=timeout,
    )
