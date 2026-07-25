"""Collect recent RSS entries into an in-memory short-term news pool."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser

from core.models import NewsArticle, RssSource
from core.news_pool.candidates import PoolCandidate
from core.news_pool.config import NewsPoolConfig, load_news_pool_config
from core.semantic_dedup.baseline import parse_entry_pub_date
from core.semantic_dedup.text import entry_description
from core.url_utils import normalize_article_url

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


@dataclass
class CollectStats:
    feeds_ok: int = 0
    feeds_failed: int = 0
    entries_seen: int = 0
    missing_pub_date: int = 0
    outside_window: int = 0
    missing_link_or_title: int = 0
    already_in_db: int = 0
    pooled: int = 0
    # canonical_url -> first source that claimed it (for cross-feed URL collisions)
    url_collisions: int = 0


@dataclass
class CollectResult:
    candidates: list[PoolCandidate] = field(default_factory=list)
    stats: CollectStats = field(default_factory=CollectStats)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _existing_urls(canonical_urls: Iterable[str]) -> set[str]:
    urls = [u for u in canonical_urls if u]
    if not urls:
        return set()
    return set(
        NewsArticle.objects.filter(original_url__in=urls).values_list(
            "original_url", flat=True
        )
    )


def collect_recent_pool(
    sources: Iterable[RssSource] | None = None,
    *,
    config: NewsPoolConfig | None = None,
    log: LogFn | None = None,
    now: datetime | None = None,
) -> CollectResult:
    """Read all active feeds and keep entries published within the lookback window.

    The pool lives only for this process/call — nothing is persisted.
    Entries whose canonical URL is already in ``NewsArticle`` are dropped early.
    """
    cfg = config or load_news_pool_config()

    def _log(message: str) -> None:
        if log:
            log(message)
        else:
            logger.info("[news-pool] %s", message)

    if sources is None:
        source_list = list(RssSource.objects.filter(is_active=True).order_by("priority", "name"))
    else:
        source_list = list(sources)

    result = CollectResult()
    if not source_list:
        _log("no active RSS sources")
        return result

    cutoff = _as_aware_utc(now or datetime.now(timezone.utc)) - timedelta(
        hours=cfg.lookback_hours
    )
    _log(
        f"collecting | sources={len(source_list)} "
        f"| lookback={cfg.lookback_hours:g}h "
        f"| cutoff={cutoff.isoformat()}"
    )

    # Pass 1: parse feeds into provisional candidates (before DB URL filter).
    provisional: list[PoolCandidate] = []
    seen_urls: dict[str, PoolCandidate] = {}

    for source in source_list:
        priority = int(getattr(source, "priority", 100) or 100)
        try:
            feed = feedparser.parse(source.url)
        except Exception as exc:
            result.stats.feeds_failed += 1
            _log(f"feed error | {source.name}: {exc!r}")
            continue

        if feed.bozo and not feed.entries:
            result.stats.feeds_failed += 1
            _log(
                f"feed unloadable | {source.name}: "
                f"{getattr(feed, 'bozo_exception', None)!r}"
            )
            continue

        result.stats.feeds_ok += 1
        entries = list(getattr(feed, "entries", None) or [])
        accepted_from_feed = 0

        for entry in entries:
            result.stats.entries_seen += 1
            link = (getattr(entry, "link", "") or "").strip()
            title = (getattr(entry, "title", "") or "").strip()
            if not link or not title:
                result.stats.missing_link_or_title += 1
                continue

            pub_date = parse_entry_pub_date(entry)
            if pub_date is None:
                result.stats.missing_pub_date += 1
                continue
            pub_date = _as_aware_utc(pub_date)
            if pub_date < cutoff:
                result.stats.outside_window += 1
                continue

            canonical_url = normalize_article_url(link)
            if not canonical_url:
                result.stats.missing_link_or_title += 1
                continue

            candidate = PoolCandidate(
                source=source,
                entry=entry,
                canonical_url=canonical_url,
                title=title,
                description=entry_description(entry),
                pub_date=pub_date,
                priority=priority,
            )

            existing = seen_urls.get(canonical_url)
            if existing is not None:
                result.stats.url_collisions += 1
                # Same URL from two feeds: keep higher-priority (lower number).
                if (priority, -pub_date.timestamp()) < (
                    existing.priority,
                    -existing.pub_date.timestamp(),
                ):
                    seen_urls[canonical_url] = candidate
                continue

            seen_urls[canonical_url] = candidate
            accepted_from_feed += 1

        _log(
            f"feed ok | {source.name} priority={priority} "
            f"| entries={len(entries)} | in_window≈{accepted_from_feed}"
        )

    provisional = list(seen_urls.values())

    # Pass 2: drop URLs already stored in the DB (any status).
    already = _existing_urls(c.canonical_url for c in provisional)
    kept: list[PoolCandidate] = []
    for candidate in provisional:
        if candidate.canonical_url in already:
            result.stats.already_in_db += 1
            continue
        kept.append(candidate)

    # Newest first for downstream processing order.
    kept.sort(key=lambda c: c.pub_date, reverse=True)
    result.candidates = kept
    result.stats.pooled = len(kept)

    _log(
        "collect done | "
        f"feeds_ok={result.stats.feeds_ok} "
        f"feeds_failed={result.stats.feeds_failed} "
        f"seen={result.stats.entries_seen} "
        f"no_date={result.stats.missing_pub_date} "
        f"old={result.stats.outside_window} "
        f"in_db={result.stats.already_in_db} "
        f"url_collisions={result.stats.url_collisions} "
        f"pooled={result.stats.pooled}"
    )
    return result
