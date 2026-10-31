"""Ingest نماد صنعت: score suitability cheaply, rewrite only approvals.

Gemini embeddings are not called. The model scores a short RSS snippet, and
only an approved score is scraped and rewritten. That keeps the API bill on
the articles that can actually be published.

Run with:

    python manage.py fetch_news
"""

from __future__ import annotations

# --- Force IPv4 for ALL outbound HTTP traffic ---------------------------------
import socket

import urllib3.util.connection as urllib3_cn

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo
# -----------------------------------------------------------------------------

import json
import os
import re
import sys
import time
import traceback
from typing import Any
from zoneinfo import ZoneInfo

import feedparser
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.utils import timezone
from dotenv import load_dotenv

from core.article_scraper import _clean_html_to_text, scrape_article_html
from core.arvan_ai import ArvanAIRequestError, ArvanChatClient, load_arvan_ai_config
from core.editorial.config import EditorialConfig, load_editorial_config
from core.editorial.persian import persian_problems
from core.editorial.prompts import (
    REPAIR_PROMPT_TEMPLATE,
    REWRITE_PROMPT_TEMPLATE,
    SCORE_PROMPT_TEMPLATE,
)
from core.editorial.taxonomy import (
    AXES,
    BRAND_NAME,
    CATEGORIES,
    REGIONS,
    canonicalize,
    canonicalize_axes,
)
from core.models import NewsArticle, RssSource
from core.news_pool import collect_recent_pool, load_news_pool_config
from core.news_pool.candidates import PoolCandidate
from core.news_pool.lexical import dedupe_pool_lexical
from core.topic_filter import assess_topic
from core.url_utils import normalize_article_url

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_TAG_RE = re.compile(
    r"</?(?!h2\b|p\b|a\b|/h2|/p|/a)[a-zA-Z][^>]*>",
    re.IGNORECASE,
)
_TEHRAN = ZoneInfo("Asia/Tehran")

SCORE_KEYS = ("score", "approve", "category", "region", "reason")
REWRITE_KEYS = ("site_title", "site_lead", "site_body", "telegram_text")


def _strip_markdown_fences(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    cleaned = _FENCE_RE.sub("", cleaned)
    return cleaned.strip()


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "approve"}


def _as_score(value: object) -> int:
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def _sanitize_body(html: str) -> str:
    text = (html or "").strip()
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", "", text)
    return _TAG_RE.sub("", text).strip()


def _joined_text(*parts: str | None) -> str:
    return "\n".join(part for part in parts if part)


class Command(BaseCommand):
    help = (
        "Collect active RSS feeds, drop same-story duplicates without embeddings, "
        "score each new item for نماد صنعت, and rewrite only approved items."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        load_dotenv(settings.BASE_DIR / ".env")
        try:
            arvan_config = load_arvan_ai_config()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        chat_client = ArvanChatClient(arvan_config)
        editorial = load_editorial_config()
        pool_config = load_news_pool_config()
        channel_id = os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID", "").strip() or BRAND_NAME

        self.editorial = editorial
        self.channel_id = channel_id
        self.chat_client = chat_client
        self.model_name = arvan_config.model
        self._scores_done = 0
        self._rewrites_done = 0
        self._auth_failed = False
        self._last_llm_at: float | None = None

        published_today = self._published_today()
        self.stdout.write(f"Rewrite model: {self.model_name} (embeddings off)")
        self.stdout.write(
            f"Budget: score<={editorial.max_scores_per_run}/run, "
            f"rewrite<={editorial.max_rewrites_per_run}/run, "
            f"daily cap {editorial.daily_cap} "
            f"({published_today} already written today), "
            f"approve at score≥{editorial.score_threshold}."
        )

        if published_today >= editorial.daily_cap:
            self.stdout.write(self.style.WARNING(
                "Daily cap already reached. No score or rewrite calls this run."
            ))
            return

        sources = list(RssSource.objects.filter(is_active=True).order_by("priority", "name"))
        if not sources:
            self.stdout.write(self.style.WARNING("No active RSS sources found."))
            return

        def _pool_log(message: str) -> None:
            self.stdout.write(self.style.HTTP_INFO(f"  [news-pool] {message}"))

        collect_result = collect_recent_pool(sources, config=pool_config, log=_pool_log)
        deduped = dedupe_pool_lexical(collect_result.candidates, log=_pool_log)

        totals = {
            "created": 0,
            "queued": 0,
            "rejected": 0,
            "topic_skipped": 0,
            "skipped": 0,
            "errors": 0,
            "pool_dropped": deduped.stats.dropped,
        }
        totals["created"] += self._drain_queue()

        for index, candidate in enumerate(deduped.candidates, start=1):
            if self._published_today() >= editorial.daily_cap:
                self.stdout.write(self.style.WARNING("Daily cap reached."))
                break
            if self._rewrites_done >= editorial.max_rewrites_per_run and self._queue_is_full():
                self.stdout.write(self.style.WARNING(
                    "Rewrite budget and queue are full. Remaining items wait."
                ))
                break
            if getattr(self, "_auth_failed", False):
                self.stdout.write(self.style.ERROR(
                    "Stopped after an authorization failure. No further API calls."
                ))
                break
            if self._scores_done >= editorial.max_scores_per_run:
                self.stdout.write(self.style.WARNING(
                    "Score budget reached. Unscored items stay in the lookback window."
                ))
                break

            self.stdout.write(self.style.HTTP_INFO(
                f"\n  [{index}/{len(deduped.candidates)}] "
                f"p={candidate.priority} | {candidate.source_name} | {candidate.title[:70]}"
            ))
            stats = self._process_candidate(candidate)
            for key, value in stats.items():
                totals[key] += value

        self.stdout.write(self.style.SUCCESS(
            "\nDone. "
            f"Created: {totals['created']}, "
            f"queued: {totals['queued']}, "
            f"rejected: {totals['rejected']}, "
            f"topic-skipped: {totals['topic_skipped']}, "
            f"url-skipped: {totals['skipped']}, "
            f"lexical-dropped: {totals['pool_dropped']}, "
            f"errors: {totals['errors']}. "
            f"LLM scores={self._scores_done}, rewrites={self._rewrites_done}."
        ))

    def _published_today(self) -> int:
        now = timezone.now().astimezone(_TEHRAN)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return NewsArticle.objects.filter(
            rewritten_at__gte=start,
            status__in=(NewsArticle.Status.PENDING, NewsArticle.Status.PUBLISHED),
        ).count()

    def _queue_is_full(self) -> bool:
        waiting = NewsArticle.objects.filter(status=NewsArticle.Status.QUEUED).count()
        return waiting >= self.editorial.queue_limit

    def _wait_slot(self) -> None:
        if self._last_llm_at is None:
            return
        remaining = self.editorial.llm_interval_seconds - (time.monotonic() - self._last_llm_at)
        if remaining > 0:
            time.sleep(remaining)

    def _call_llm(
        self,
        prompt: str,
        required_keys: tuple[str, ...],
        *,
        stage: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        self._wait_slot()
        self._last_llm_at = time.monotonic()
        self.stdout.write(self.style.HTTP_INFO(
            f"  → LLM {stage} | {len(prompt)} chars | max_tokens={max_tokens}"
        ))
        raw_text = self.chat_client.complete(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        parsed = json.loads(_strip_markdown_fences(raw_text))
        if not isinstance(parsed, dict):
            raise ValueError(f"{stage} did not return a JSON object")
        missing = [key for key in required_keys if key not in parsed]
        if missing:
            raise ValueError(f"Missing keys in {stage}: {missing}")
        return parsed

    def _score(self, title: str, source_name: str, snippet: str) -> dict[str, Any]:
        prompt = SCORE_PROMPT_TEMPLATE.format(
            brand=BRAND_NAME,
            categories=" | ".join(CATEGORIES),
            axes=" | ".join(AXES),
            regions=" | ".join(REGIONS),
            threshold=self.editorial.score_threshold,
            title=title,
            source_name=source_name,
            snippet=snippet[: self.editorial.snippet_chars],
        )
        self._scores_done += 1
        return self._call_llm(
            prompt,
            SCORE_KEYS,
            stage="score",
            temperature=0.1,
            max_tokens=self.editorial.score_max_tokens,
        )

    def _rewrite(
        self,
        *,
        title: str,
        source_name: str,
        source_url: str,
        content: str,
        content_status: str,
        category: str,
        axes: str,
        region: str,
        corroboration: str,
    ) -> dict[str, str]:
        prompt = REWRITE_PROMPT_TEMPLATE.format(
            brand=BRAND_NAME,
            channel_id=self.channel_id,
            corroboration=corroboration,
            category=category,
            axes=axes,
            region=region,
            content_status=content_status,
            title=title,
            source_name=source_name,
            source_url=source_url,
            content=content[: self.editorial.rewrite_chars],
        )
        self._rewrites_done += 1
        parsed = self._call_llm(
            prompt,
            REWRITE_KEYS,
            stage="rewrite",
            temperature=0.4,
            max_tokens=self.editorial.rewrite_max_tokens,
        )
        return self._finalize_copy(parsed, source_name=source_name, source_url=source_url)

    def _repair(self, bad: dict[str, str], *, source_name: str, source_url: str) -> dict[str, str]:
        prompt = REPAIR_PROMPT_TEMPLATE.format(
            source_name=source_name,
            channel_id=self.channel_id,
            bad_json=json.dumps(bad, ensure_ascii=False)[:4000],
        )
        self._rewrites_done += 1
        parsed = self._call_llm(
            prompt,
            REWRITE_KEYS,
            stage="repair",
            temperature=0.2,
            max_tokens=self.editorial.rewrite_max_tokens,
        )
        return self._finalize_copy(parsed, source_name=source_name, source_url=source_url)

    def _finalize_copy(
        self,
        parsed: dict[str, Any],
        *,
        source_name: str,
        source_url: str,
    ) -> dict[str, str]:
        site_title = str(parsed.get("site_title") or "").strip()
        site_lead = str(parsed.get("site_lead") or "").strip()
        site_body = _sanitize_body(str(parsed.get("site_body") or ""))
        telegram_text = str(parsed.get("telegram_text") or "").strip()
        if "اهمیت برای ایران" not in site_body:
            site_body += (
                "<h2>اهمیت برای ایران</h2>"
                "<p>اثر مستقیم این خبر بر صنعت ایران در متن منبع نیامده است "
                "و باید با قیمت، تأمین و مقررات داخلی سنجیده شود.</p>"
            )
        if source_name and source_name not in site_body:
            safe_url = source_url.replace('"', "%22")
            site_body += f'<p>منبع: <a href="{safe_url}">{source_name}</a></p>'
        if self.channel_id and self.channel_id not in telegram_text:
            telegram_text = f"{telegram_text}\n\n{self.channel_id}".strip()
        return {
            "site_title": site_title,
            "site_lead": site_lead,
            "site_body": site_body,
            "telegram_text": telegram_text,
        }

    def _copy_is_clean(self, copy: dict[str, str]) -> list[str]:
        return persian_problems(_joined_text(
            copy.get("site_title"),
            copy.get("site_lead"),
            copy.get("site_body"),
            copy.get("telegram_text"),
        ))

    def _scrape_log(self, message: str, error: bool = False) -> None:
        stream = self.stderr if error else self.stdout
        style = self.style.ERROR if error else self.style.HTTP_INFO
        stream.write(style(f"  [scrape] {message}") + "\n")

    def _load_body(
        self,
        link: str,
        canonical_url: str,
        entry: feedparser.FeedParserDict,
        fallback_text: str,
    ) -> tuple[str, str]:
        raw_html, content_source, detail = scrape_article_html(
            link, canonical_url, entry, scrape_log=self._scrape_log,
        )
        clean_text = _clean_html_to_text(raw_html) or fallback_text
        if content_source == "webpage":
            content_status = "full"
        elif content_source == "rss":
            content_status = "rss"
        else:
            content_status = "blocked"
        if detail and content_status != "full":
            self.stderr.write(self.style.WARNING(
                f"  ! content {content_status}: {detail}"
            ))
        return clean_text, content_status

    def _drain_queue(self) -> int:
        """Rewrite items already scored and approved, without calling the scorer again."""
        created = 0
        room = self.editorial.max_rewrites_per_run - self._rewrites_done
        daily_room = self.editorial.daily_cap - self._published_today()
        limit = max(0, min(room, daily_room))
        if limit <= 0:
            return 0

        queued = list(
            NewsArticle.objects.filter(status=NewsArticle.Status.QUEUED)
            .select_related("source")
            .order_by("-suitability_score", "created_at")[:limit]
        )
        for article in queued:
            if self._rewrites_done >= self.editorial.max_rewrites_per_run:
                break
            if self._published_today() >= self.editorial.daily_cap:
                break
            try:
                if self._rewrite_existing(article):
                    created += 1
            except Exception as exc:
                self.stderr.write(self.style.WARNING(
                    f"  ! queued rewrite failed #{article.id}: {exc!r}"
                ))
                self.stderr.write(self.style.WARNING(traceback.format_exc()))
        return created

    def _rewrite_existing(self, article: NewsArticle) -> bool:
        entry = feedparser.FeedParserDict(
            link=article.original_url,
            title=article.original_title,
            summary=article.source_excerpt or "",
        )
        clean_text, content_status = self._load_body(
            article.original_url,
            article.original_url,
            entry,
            article.source_excerpt or article.original_title,
        )
        corroboration = "فقط همین منبع؛ ادعا را چندمنبعه ننویس."
        copy = self._rewrite(
            title=article.original_title,
            source_name=article.source.name,
            source_url=article.original_url,
            content=clean_text,
            content_status=content_status,
            category=article.industry_category,
            axes=article.theme_axes,
            region=article.region,
            corroboration=corroboration,
        )
        problems = self._copy_is_clean(copy)
        if problems:
            self.stdout.write(self.style.WARNING(
                f"  ! persian check: {'; '.join(problems)} — repairing"
            ))
            copy = self._repair(
                copy,
                source_name=article.source.name,
                source_url=article.original_url,
            )
            problems = self._copy_is_clean(copy)
        if problems or not copy["site_title"] or not copy["telegram_text"]:
            article.status = NewsArticle.Status.REJECTED
            article.editorial_note = "رد شد: فارسی خروجی قابل انتشار نبود"
            article.save(update_fields=["status", "editorial_note"])
            self.stdout.write(self.style.WARNING(
                f"  - dropped unclean rewrite: {article.original_title[:70]}"
            ))
            return False

        article.site_title = copy["site_title"][:255]
        article.site_lead = copy["site_lead"]
        article.site_body = copy["site_body"]
        article.telegram_text = copy["telegram_text"]
        article.content_status = content_status
        article.image_url = None
        article.status = NewsArticle.Status.PENDING
        article.rewritten_at = timezone.now()
        article.save()
        self.stdout.write(self.style.SUCCESS(
            f"  + rewritten from queue: {article.original_title[:70]}"
        ))
        return True

    def _process_candidate(self, candidate: PoolCandidate) -> dict[str, int]:
        stats = {
            "created": 0,
            "queued": 0,
            "rejected": 0,
            "topic_skipped": 0,
            "skipped": 0,
            "errors": 0,
        }
        link = (getattr(candidate.entry, "link", "") or "").strip()
        title = (candidate.title or "").strip()
        canonical_url = candidate.canonical_url or normalize_article_url(link)
        if not link or not title or not canonical_url:
            stats["errors"] += 1
            return stats
        if NewsArticle.objects.filter(original_url=canonical_url).exists():
            stats["skipped"] += 1
            return stats

        snippet = (candidate.description or "").strip()
        topic = assess_topic(title, snippet)
        if topic.skip:
            self._save_rejected(
                candidate,
                canonical_url,
                note=f"حذف پیش‌فیلتر: {topic.reason}",
                content_status="prefilter",
            )
            stats["topic_skipped"] += 1
            return stats

        try:
            scored = self._score(title, candidate.source_name, snippet or title)
        except ArvanAIRequestError as exc:
            if exc.status_code in {401, 403}:
                self._auth_failed = True
                self.stderr.write(self.style.ERROR(
                    "AvalAI rejected the key (HTTP "
                    f"{exc.status_code}). Set AVALAI_API_KEY in .env, "
                    "then run fetch_news again."
                ))
            else:
                self.stderr.write(self.style.WARNING(
                    f"  ! score failed for '{title[:60]}': {exc}"
                ))
            stats["errors"] += 1
            return stats
        except (json.JSONDecodeError, ValueError, TimeoutError) as exc:
            self.stderr.write(self.style.WARNING(
                f"  ! score failed for '{title[:60]}': {exc}"
            ))
            stats["errors"] += 1
            return stats

        score = _as_score(scored.get("score"))
        approved = _as_bool(scored.get("approve")) and score >= self.editorial.score_threshold
        category = canonicalize(str(scored.get("category") or ""), CATEGORIES)
        axes = canonicalize_axes(scored.get("axes"))
        region = canonicalize(str(scored.get("region") or ""), REGIONS)
        reason = str(scored.get("reason") or "").strip()
        if approved and (not category or not region or not axes):
            approved = False
            reason = reason or "برچسب دسته، محور یا منطقه معتبر نبود"

        if not approved:
            self._save_rejected(
                candidate,
                canonical_url,
                note=f"نمره {score}: {reason or 'برای نماد صنعت مناسب نبود'}",
                content_status="scored",
                score=score,
                category=category,
                axes="، ".join(axes),
                region=region,
                excerpt=snippet,
            )
            self.stdout.write(self.style.WARNING(
                f"  - score {score} reject: {title[:70]}"
            ))
            stats["rejected"] += 1
            return stats

        self.stdout.write(self.style.HTTP_INFO(
            f"  → score {score} approve | {category} | {region}"
        ))
        can_rewrite = (
            self._rewrites_done < self.editorial.max_rewrites_per_run
            and self._published_today() < self.editorial.daily_cap
        )
        if not can_rewrite:
            self._save_queued(
                candidate,
                canonical_url,
                note=f"نمره {score}: {reason}",
                score=score,
                category=category,
                axes="، ".join(axes),
                region=region,
                excerpt=snippet,
            )
            stats["queued"] += 1
            return stats

        try:
            clean_text, content_status = self._load_body(
                link, canonical_url, candidate.entry, snippet or title,
            )
            others = "، ".join(candidate.corroborating_sources)
            corroboration = (
                f"همچنین در {others} دیده شده؛ باز هم هر عدد را به منبع خودش نسبت بده."
                if others
                else "فقط همین منبع؛ ادعا را چندمنبعه ننویس."
            )
            copy = self._rewrite(
                title=title,
                source_name=candidate.source_name,
                source_url=canonical_url,
                content=clean_text,
                content_status=content_status,
                category=category,
                axes="، ".join(axes),
                region=region,
                corroboration=corroboration,
            )
            problems = self._copy_is_clean(copy)
            if problems:
                self.stdout.write(self.style.WARNING(
                    f"  ! persian check: {'; '.join(problems)} — repairing"
                ))
                copy = self._repair(
                    copy,
                    source_name=candidate.source_name,
                    source_url=canonical_url,
                )
                problems = self._copy_is_clean(copy)
            if problems or not copy["site_title"] or not copy["telegram_text"]:
                self._save_rejected(
                    candidate,
                    canonical_url,
                    note="رد شد: فارسی خروجی قابل انتشار نبود",
                    content_status=content_status,
                    score=score,
                    category=category,
                    axes="، ".join(axes),
                    region=region,
                    excerpt=snippet,
                )
                stats["rejected"] += 1
                return stats

            if NewsArticle.objects.filter(original_url=canonical_url).exists():
                stats["skipped"] += 1
                return stats
            NewsArticle.objects.create(
                source=candidate.source,
                original_title=title[:255],
                original_url=canonical_url,
                image_url=None,
                site_title=copy["site_title"][:255],
                site_lead=copy["site_lead"],
                site_body=copy["site_body"],
                telegram_text=copy["telegram_text"],
                editorial_note=f"نمره {score}: {reason}"[:1000],
                content_status=content_status,
                industry_category=category,
                theme_axes="، ".join(axes),
                region=region,
                suitability_score=score,
                source_excerpt=snippet[:4000],
                rewritten_at=timezone.now(),
                status=NewsArticle.Status.PENDING,
            )
        except IntegrityError:
            stats["skipped"] += 1
            return stats
        except Exception as exc:
            self.stderr.write(self.style.WARNING(
                f"  ! rewrite failed for '{title[:60]}': {exc!r}"
            ))
            self.stderr.write(self.style.WARNING(traceback.format_exc()))
            # Keep the passing score so the next cycle rewrites without a new score call.
            self._save_queued(
                candidate,
                canonical_url,
                note=f"نمره {score}: بازنویسی ناتمام — {reason}",
                score=score,
                category=category,
                axes="، ".join(axes),
                region=region,
                excerpt=snippet,
            )
            stats["queued"] += 1
            return stats

        self.stdout.write(self.style.SUCCESS(f"  + created: {title[:70]}"))
        stats["created"] += 1
        return stats

    def _save_rejected(
        self,
        candidate: PoolCandidate,
        canonical_url: str,
        *,
        note: str,
        content_status: str,
        score: int | None = None,
        category: str = "",
        axes: str = "",
        region: str = "",
        excerpt: str = "",
    ) -> None:
        try:
            NewsArticle.objects.create(
                source=candidate.source,
                original_title=candidate.title[:255],
                original_url=canonical_url,
                editorial_note=note[:1000],
                content_status=content_status,
                industry_category=category,
                theme_axes=axes,
                region=region,
                suitability_score=score,
                source_excerpt=excerpt[:4000],
                status=NewsArticle.Status.REJECTED,
            )
        except IntegrityError:
            return

    def _save_queued(
        self,
        candidate: PoolCandidate,
        canonical_url: str,
        *,
        note: str,
        score: int,
        category: str,
        axes: str,
        region: str,
        excerpt: str,
    ) -> None:
        try:
            NewsArticle.objects.create(
                source=candidate.source,
                original_title=candidate.title[:255],
                original_url=canonical_url,
                editorial_note=note[:1000],
                content_status="scored",
                industry_category=category,
                theme_axes=axes,
                region=region,
                suitability_score=score,
                source_excerpt=(excerpt or candidate.title)[:4000],
                status=NewsArticle.Status.QUEUED,
            )
        except IntegrityError:
            return
        self.stdout.write(self.style.HTTP_INFO(
            f"  ~ queued for rewrite: {candidate.title[:70]}"
        ))
