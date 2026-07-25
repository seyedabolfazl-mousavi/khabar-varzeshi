"""Priority-aware same-story deduplication inside the short-term news pool."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from core.news_pool.candidates import PoolCandidate
from core.news_pool.config import NewsPoolConfig, load_news_pool_config
from core.semantic_dedup.embeddings import EmbeddingService
from core.semantic_dedup.text import build_embedding_document
from core.semantic_dedup.vectors import cosine_similarity

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


@dataclass
class DedupeStats:
    input_count: int = 0
    output_count: int = 0
    clusters: int = 0
    dropped: int = 0
    embed_failures: int = 0
    skipped_no_embed: bool = False


@dataclass
class DedupeResult:
    candidates: list[PoolCandidate] = field(default_factory=list)
    stats: DedupeStats = field(default_factory=DedupeStats)


def dedupe_pool_by_priority(
    candidates: list[PoolCandidate],
    *,
    embedding_service: EmbeddingService | None,
    config: NewsPoolConfig | None = None,
    log: LogFn | None = None,
) -> DedupeResult:
    """Collapse same-story items across sources; keep the highest-priority source.

    Algorithm:
    1. Sort by ``(priority ASC, pub_date DESC)`` so better sources are seen first.
    2. Embed title+description once per candidate (batch via EmbeddingService).
    3. Greedy clustering: assign to an existing cluster if cosine ≥ threshold
       against that cluster's representative; otherwise start a new cluster.
    4. Each cluster keeps its representative (first member = best priority).
    5. Return survivors sorted by ``pub_date DESC`` for processing order.
    """
    cfg = config or load_news_pool_config()

    def _log(message: str) -> None:
        if log:
            log(message)
        else:
            logger.info("[news-pool-dedupe] %s", message)

    result = DedupeResult()
    result.stats.input_count = len(candidates)

    if not candidates:
        return result

    if embedding_service is None:
        result.stats.skipped_no_embed = True
        _log(
            "embedding service unavailable — skipping pool dedupe; "
            "keeping all candidates"
        )
        survivors = sorted(candidates, key=lambda c: c.pub_date, reverse=True)
        result.candidates = survivors
        result.stats.output_count = len(survivors)
        result.stats.clusters = len(survivors)
        return result

    # Priority first (lower wins), then newer within same priority.
    ordered = sorted(
        candidates,
        key=lambda c: (c.priority, -c.pub_date.timestamp()),
    )

    documents: list[str] = []
    embeddable_indexes: list[int] = []
    for index, candidate in enumerate(ordered):
        document = build_embedding_document(
            title=candidate.title,
            description=candidate.description,
        )
        if document:
            embeddable_indexes.append(index)
            documents.append(document)

    vectors_by_index: dict[int, list[float]] = {}
    if documents:
        try:
            vectors = embedding_service.embed_many(
                documents,
                task_type="SEMANTIC_SIMILARITY",
            )
            for idx, vector in zip(embeddable_indexes, vectors):
                vectors_by_index[idx] = vector
        except Exception as exc:
            result.stats.embed_failures += 1
            result.stats.skipped_no_embed = True
            _log(f"pool embed failed ({exc!r}) — keeping all candidates")
            survivors = sorted(candidates, key=lambda c: c.pub_date, reverse=True)
            result.candidates = survivors
            result.stats.output_count = len(survivors)
            result.stats.clusters = len(survivors)
            return result

    # Cluster representatives: (candidate_index, embedding)
    clusters: list[tuple[int, list[float] | None]] = []
    dropped_notes: list[str] = []

    for index, candidate in enumerate(ordered):
        vector = vectors_by_index.get(index)
        matched_cluster: int | None = None
        best_score = 0.0

        if vector is not None:
            for cluster_i, (rep_index, rep_vector) in enumerate(clusters):
                if rep_vector is None:
                    continue
                score = cosine_similarity(vector, rep_vector)
                if score >= cfg.dedup_threshold and score > best_score:
                    best_score = score
                    matched_cluster = cluster_i

        if matched_cluster is None:
            clusters.append((index, vector))
            continue

        # Already have a better-priority representative in this cluster.
        result.stats.dropped += 1
        rep = ordered[clusters[matched_cluster][0]]
        dropped_notes.append(
            f"drop '{candidate.title[:60]}' ({candidate.source_name}, "
            f"p={candidate.priority}) → keep '{rep.title[:60]}' "
            f"({rep.source_name}, p={rep.priority}) score={best_score:.3f}"
        )

    survivors = [ordered[rep_index] for rep_index, _ in clusters]
    survivors.sort(key=lambda c: c.pub_date, reverse=True)

    result.candidates = survivors
    result.stats.clusters = len(clusters)
    result.stats.output_count = len(survivors)

    _log(
        f"dedupe done | in={result.stats.input_count} "
        f"out={result.stats.output_count} "
        f"clusters={result.stats.clusters} "
        f"dropped={result.stats.dropped} "
        f"threshold={cfg.dedup_threshold:.2f}"
    )
    for note in dropped_notes[:20]:
        _log(f"  {note}")
    if len(dropped_notes) > 20:
        _log(f"  … and {len(dropped_notes) - 20} more drops")

    return result
