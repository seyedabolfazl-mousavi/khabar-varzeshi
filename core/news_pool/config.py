"""Configuration for the hourly in-memory news pool."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_LOOKBACK_HOURS = 1
DEFAULT_DEDUP_THRESHOLD = 0.88


@dataclass(frozen=True)
class NewsPoolConfig:
    lookback_hours: float
    dedup_threshold: float


def load_news_pool_config() -> NewsPoolConfig:
    return NewsPoolConfig(
        lookback_hours=float(
            os.getenv("POOL_LOOKBACK_HOURS", str(DEFAULT_LOOKBACK_HOURS))
        ),
        dedup_threshold=float(
            os.getenv("POOL_DEDUP_THRESHOLD", str(DEFAULT_DEDUP_THRESHOLD))
        ),
    )
