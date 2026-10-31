"""AvalAI chat completions client (OpenAI-compatible)."""

from __future__ import annotations

import logging
from typing import Any

from core.arvan_ai.config import ArvanAIConfig, load_arvan_ai_config
from core.arvan_ai.http import ArvanAIRequestError, post_json

logger = logging.getLogger(__name__)

# Haiku 5.5 bills hidden reasoning from the same output budget as the answer.
# `thinking: disabled` is not a supported off-switch; low effort keeps that
# share small enough for the JSON to finish.
_EFFORT = "low"
_DROP_ON_REJECT = ("output_config", "temperature", "response_format")


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
            "max_completion_tokens": max_tokens,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "output_config": {"effort": _EFFORT},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = self._post(payload)
        text = _message_text(data)
        if text:
            return text

        # Reasoning still filled the budget. One wider retry, then surface the miss.
        wider = min(max(max_tokens * 3, 4000), 12000)
        if wider > max_tokens:
            logger.warning(
                "AvalAI returned no visible text at max_tokens=%s; retrying at %s.",
                max_tokens,
                wider,
            )
            payload["max_completion_tokens"] = wider
            payload["max_tokens"] = wider
            data = self._post(payload)
            text = _message_text(data)
            if text:
                return text

        finish = _finish_reason(data)
        raise ValueError(f"AvalAI returned empty content (finish_reason={finish}).")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        while True:
            try:
                return post_json(
                    self.config.chat_url,
                    api_key=self.config.api_key,
                    payload=payload,
                    timeout=self.config.timeout_seconds,
                )
            except ArvanAIRequestError as exc:
                if exc.status_code not in {400, 422}:
                    raise
                dropped = next((key for key in _DROP_ON_REJECT if key in payload), None)
                if dropped is None:
                    raise
                logger.warning(
                    "AvalAI rejected the request (%s); retrying without %s.",
                    exc.status_code,
                    dropped,
                )
                payload.pop(dropped, None)


def _finish_reason(data: dict[str, Any]) -> str | None:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    finish = choices[0].get("finish_reason")
    return str(finish) if finish else None


def _message_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
        ]
        content = "".join(parts)
    if not isinstance(content, str):
        return ""
    return content.strip()
