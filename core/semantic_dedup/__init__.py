"""Semantic deduplication against the site's 24-hour baseline RSS feed."""

from core.semantic_dedup.filter import (
    SemanticDedupFilter,
    SemanticMatchResult,
    build_semantic_dedup_filter,
)

__all__ = [
    "SemanticDedupFilter",
    "SemanticMatchResult",
    "build_semantic_dedup_filter",
]
