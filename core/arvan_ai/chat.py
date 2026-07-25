"""Arvan Cloud AI chat completions client (OpenAI-compatible)."""

from __future__ import annotations

import logging
from typing import Any

from core.arvan_ai.config import ArvanAIConfig, load_arvan_ai_config
from core.arvan_ai.http import ArvanAIRequestError, post_json

logger = logging.getLogger(__name__)


class ArvanChatClient:
    """Thin client for ``POST .../v1/chat/completions`` via Arvan gateway."""

    def __init__(self, config: ArvanAIConfig | None = None) -> None:
        self.config = config or load_arvan_ai_config()

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 8000,
        json_mode: bool = False,
        system: str | None = None,
    ) -> str:
        """Return assistant message content for a single user prompt."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            # Best-effort; some gateways ignore unknown fields.
            payload["response_format"] = {"type": "json_object"}

        try:
            data = post_json(
                self.config.chat_url,
                api_key=self.config.api_key,
                payload=payload,
                timeout=self.config.timeout_seconds,
            )
        except ArvanAIRequestError as exc:
            # Retry once without response_format if the gateway rejects it.
            if json_mode and exc.status_code in {400, 422}:
                logger.warning(
                    "Arvan chat rejected json_mode (%s); retrying without "
                    "response_format.",
                    exc.status_code,
                )
                payload.pop("response_format", None)
                data = post_json(
                    self.config.chat_url,
                    api_key=self.config.api_key,
                    payload=payload,
                    timeout=self.config.timeout_seconds,
                )
            else:
                raise

        choices = data.get("choices") or []
        if not choices:
            raise ValueError(f"Arvan chat returned no choices: {data!r}")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            # Some gateways return multimodal content parts.
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            ]
            content = "".join(parts)
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"Arvan chat returned empty content: {data!r}")
        return content.strip()
