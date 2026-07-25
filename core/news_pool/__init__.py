"""Short-lived in-memory news pool for one hourly ingestion cycle."""

from core.news_pool.collect import collect_recent_pool
from core.news_pool.config import load_news_pool_config
from core.news_pool.dedupe import dedupe_pool_by_priority

__all__ = [
    "collect_recent_pool",
    "dedupe_pool_by_priority",
    "load_news_pool_config",
]
