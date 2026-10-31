"""AvalAI chat completions client (OpenAI-compatible)."""

from __future__ import annotations

import logging
from typing import Any

from core.arvan_ai.config import ArvanAIConfig, load_arvan_ai_config
from core.arvan_ai.http import ArvanAIRequestError, post_json

logger = logging.getLogger(__name__)


class ArvanChatClient:
    """Thin client for ``POST .../v1/chat/completions`` via AvalAI."""

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
            # Haiku otherwise spends the whole token budget on hidden reasoning
            # and returns content=null with finish_reason=length.
            "thinking": {"type": "disabled"},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            data = post_json(
                self.config.chat_url,
                api_key=self.config.api_key,
                payload=payload,
                timeout=self.config.timeout_seconds,
            )
        except ArvanAIRequestError as exc:
            if exc.status_code in {400, 422} and "thinking" in payload:
                logger.warning(
                    "AvalAI rejected thinking=disabled (%s); retrying without it.",
                    exc.status_code,
                )
                payload.pop("thinking", None)
                try:
                    data = post_json(
                        self.config.chat_url,
                        api_key=self.config.api_key,
                        payload=payload,
                        timeout=self.config.timeout_seconds,
                    )
                except ArvanAIRequestError as retry_exc:
                    if retry_exc.status_code not in {400, 422} or "response_format" not in payload:
                        raise
                    payload.pop("response_format", None)
                    data = post_json(
                        self.config.chat_url,
                        api_key=self.config.api_key,
                        payload=payload,
                        timeout=self.config.timeout_seconds,
                    )
            elif exc.status_code in {400, 422} and "response_format" in payload:
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
            raise ValueError("AvalAI chat returned no choices.")

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
            finish = choices[0].get("finish_reason")
            raise ValueError(f"AvalAI returned empty content (finish_reason={finish}).")
        return content.strip()
