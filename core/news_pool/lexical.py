"""Same-story collapse using title tokens. No embedding API calls."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.news_pool.candidates import PoolCandidate

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "after",
        "over", "into", "its", "are", "was", "has", "have", "will",
        "new", "says", "say",
    }
)


@dataclass
class LexicalDedupeStats:
    input_count: int = 0
    output_count: int = 0
    dropped: int = 0


@dataclass
class LexicalDedupeResult:
    candidates: list[PoolCandidate] = field(default_factory=list)
    stats: LexicalDedupeStats = field(default_factory=LexicalDedupeStats)


def title_tokens(title: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall((title or "").lower())
        if token not in _STOP
    }


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def dedupe_pool_lexical(
    candidates: list[PoolCandidate],
    *,
    threshold: float = 0.5,
    log: LogFn | None = None,
) -> LexicalDedupeResult:
    """Keep the higher-priority copy of a story and record the other sources."""

    def _log(message: str) -> None:
        if log:
            log(message)
        else:
            logger.info("[news-pool-lexical] %s", message)

    result = LexicalDedupeResult()
    result.stats.input_count = len(candidates)
    if not candidates:
        return result

    ordered = sorted(candidates, key=lambda item: (item.priority, -item.pub_date.timestamp()))
    kept: list[PoolCandidate] = []
    kept_tokens: list[set[str]] = []

    for candidate in ordered:
        tokens = title_tokens(candidate.title)
        match_at: int | None = None
        for index, previous in enumerate(kept_tokens):
            if jaccard(tokens, previous) >= threshold:
                match_at = index
                break
        if match_at is None:
            candidate.corroborating_sources = []
            kept.append(candidate)
            kept_tokens.append(tokens)
            continue

        winner = kept[match_at]
        if (
            candidate.source_name
            and candidate.source_name != winner.source_name
            and candidate.source_name not in winner.corroborating_sources
        ):
            winner.corroborating_sources.append(candidate.source_name)
        result.stats.dropped += 1

    survivors = sorted(kept, key=lambda item: item.pub_date, reverse=True)
    result.candidates = survivors
    result.stats.output_count = len(survivors)
    _log(
        f"lexical dedupe {result.stats.input_count} -> {result.stats.output_count} "
        f"(dropped {result.stats.dropped}, no embeddings)"
    )
    return result
