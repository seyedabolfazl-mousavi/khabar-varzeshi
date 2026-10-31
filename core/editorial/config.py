"""Knobs for the cheap score-then-rewrite editorial pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EditorialConfig:
    score_threshold: int
    daily_cap: int
    max_rewrites_per_run: int
    max_scores_per_run: int
    llm_interval_seconds: float
    score_max_tokens: int
    rewrite_max_tokens: int
    snippet_chars: int
    rewrite_chars: int
    queue_limit: int


def load_editorial_config() -> EditorialConfig:
    return EditorialConfig(
        score_threshold=int(os.getenv("NEMAD_SCORE_THRESHOLD", "58")),
        daily_cap=int(os.getenv("NEMAD_DAILY_CAP", "150")),
        max_rewrites_per_run=int(os.getenv("NEMAD_MAX_REWRITES_PER_RUN", "12")),
        max_scores_per_run=int(os.getenv("NEMAD_MAX_SCORES_PER_RUN", "40")),
        llm_interval_seconds=float(os.getenv("NEMAD_LLM_INTERVAL_SECONDS", "1.0")),
        score_max_tokens=int(os.getenv("NEMAD_SCORE_MAX_TOKENS", "500")),
        rewrite_max_tokens=int(os.getenv("NEMAD_REWRITE_MAX_TOKENS", "2200")),
        snippet_chars=int(os.getenv("NEMAD_SNIPPET_CHARS", "700")),
        rewrite_chars=int(os.getenv("NEMAD_REWRITE_CHARS", "3500")),
        queue_limit=int(os.getenv("NEMAD_QUEUE_LIMIT", "30")),
    )
