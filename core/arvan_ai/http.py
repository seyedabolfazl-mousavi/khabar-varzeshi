"""HTTP helpers shared by Arvan Cloud AI clients."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def auth_headers(api_key: str) -> dict[str, str]:
    """Arvan docs: ``Authorization: apikey <key>`` (literal word apikey + space)."""
    key = api_key.strip()
    if key.lower().startswith("apikey "):
        value = key
    else:
        value = f"apikey {key}"
    return {
        "Authorization": value,
        "Content-Type": "application/json",
    }


def post_json(
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    response = requests.post(
        url,
        headers=auth_headers(api_key),
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise ArvanAIRequestError(
            status_code=response.status_code,
            body=response.text,
            url=url,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise ArvanAIRequestError(
            status_code=response.status_code,
            body=response.text,
            url=url,
        ) from exc
    if not isinstance(data, dict):
        raise ArvanAIRequestError(
            status_code=response.status_code,
            body=f"Expected JSON object, got {type(data).__name__}",
            url=url,
        )
    return data


class ArvanAIRequestError(RuntimeError):
    def __init__(self, *, status_code: int, body: str, url: str) -> None:
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"Arvan AI HTTP {status_code} for {url}: {body[:500]}")

    @property
    def is_rate_limited(self) -> bool:
        if self.status_code == 429:
            return True
        upper = self.body.upper()
        return (
            "RATE LIMIT" in upper
            or "RESOURCE_EXHAUSTED" in upper
            or "QUOTA" in upper
        )
