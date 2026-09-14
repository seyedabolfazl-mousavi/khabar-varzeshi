"""Hourly news pool ingestion: collect recent RSS items, priority-dedupe,
rewrite with Gemini, and persist as pending NewsArticle rows.

Run with:

    python manage.py fetch_news
"""

from __future__ import annotations

# --- Force IPv4 for ALL outbound HTTP traffic ---------------------------------
# Must run BEFORE any HTTP client (urllib3, httpx, etc.) is initialized.
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
import time
import traceback
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from dotenv import load_dotenv
from google import genai
from google.genai import types
from requests.exceptions import ConnectTimeout as RequestsConnectTimeout
from requests.exceptions import ReadTimeout as RequestsReadTimeout
from requests.exceptions import Timeout as RequestsTimeout

from core.article_scraper import _clean_html_to_text, scrape_article_html
from core.arvan_ai import ArvanAIRequestError, ArvanChatClient, load_arvan_ai_config
from core.models import NewsArticle, RssSource
from core.news_pool import (
    collect_recent_pool,
    dedupe_pool_by_priority,
    load_news_pool_config,
)
from core.news_pool.candidates import PoolCandidate
from core.semantic_dedup import SemanticDedupFilter, build_semantic_dedup_filter
from core.topic_filter import assess_topic
from django.conf import settings
from dotenv import load_dotenv

from core.url_utils import normalize_article_url


DEFAULT_ARVAN_MODEL = "Gemini-3.1-Flash-Lite-Preview"
GEMINI_REQUEST_TIMEOUT = 120  # seconds (legacy name; used for logging)

load_dotenv(settings.BASE_DIR / ".env")
TELEGRAM_CHANNEL_ID = (
    os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID", "").strip()
    or "@tazenews"
)

# Per-run rewrite budget and spacing between Gemini calls.
# Already-stored article URLs are never sent to Gemini again (see _article_exists).
MAX_REWRITES_PER_RUN = 20
GEMINI_MIN_INTERVAL_SECONDS = 5 * 60  # 5 minutes between rewrite requests

# Every timeout-shaped exception we may encounter from any HTTP library.
TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.TimeoutException,
    RequestsReadTimeout,
    RequestsConnectTimeout,
    RequestsTimeout,
    TimeoutError,
    ArvanAIRequestError,
)

# Keys always required from the LLM.
REQUIRED_KEYS = (
    "decision",
    "selection_reason",
    "site_title",
    "site_lead",
    "site_body",
    "telegram_text",
)
SELECT_DECISIONS = frozenset({"select", "needs_review"})
VALID_DECISIONS = frozenset({"select", "reject", "needs_review"})

# Matches an opening ``` or ```json fence, and the closing ``` fence.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

PROMPT_TEMPLATE = """\
تو دستیار دبیر «تازه‌نیوز» هستی. از ورودی معتبر زیر، اول سخت‌گیرانه تصمیم بگیر که آیا این خبر برای مخاطب ایرانی «قابل‌کلیک و جذاب» است یا نه؛ بعد فقط در صورت انتخاب یا نیاز به بررسی، پیش‌نویس فارسی بنویس. اولویت با فوتبال است. کوتاهی تیتر هرگز نباید دقت، روشنی یا وضعیت قطعی/غیرقطعی خبر را مخدوش کند.

## اصل سخت‌گیرانهٔ انتخاب
هدف: پیشنهاد خبرهایی که مردم ایران واقعاً دوست دارند باز کنند، بخوانند و به اشتراک بگذارند — نه هر خبر ورزشی درست یا صرفاً مرتبط با ایران.
مرتبط‌بودن با ایران شرط لازم نیست. خبر جهانی/اروپایی/آسیایی هم اگر برای مخاطب ایرانی کشش کلیک و بازدید داشته باشد، انتخاب کن.
اما سخت‌گیر باش: اگر شک داری که کسی تیتر را باز کند، یا خبر روزمره/کم‌حاشیه/فقط برای طرفداران محلی است → decision=reject.
برای پُرکردن فهرست، خبر متوسط یا ضعیف اضافه نکن. تیتر جذاب به‌تنهایی خبر کم‌ارزش را نجات نمی‌دهد.

## ترتیب تصمیم
۱) اعتبار و کفایت اطلاعات  ۲) پتانسیل کلیک/بازدید نزد مخاطب ایرانی  ۳) ارزش خبری و حاشیه  ۴) تازگی و تفاوت با خبرهای قبلی  ۵) تیترنویسی.

## جذابیت کلیک و بازدید (معیار اصلی)
از خودت بپرس: مخاطب ایرانی معمولی کانال/سایت ورزشی، با دیدن این تیتر، چقدر احتمال دارد کلیک کند؟
عوامل مثبت (هرچه بیشتر، بهتر): ستاره یا تیم بسیار آشنا برای ایرانی‌ها؛ انتقال بزرگ؛ اخراج/جنجال مربی؛ درگیری/کارت قرمز/حاشیه؛ نتیجه غافلگیرکننده؛ رکورد؛ مبلغ چشمگیر؛ رقابت حساس؛ داستان انسانی قوی؛ طنز یا لحظه وایرال ورزشی؛ رویداد بزرگ جهانی که همه درباره‌اش حرف می‌زنند.
عوامل منفی → معمولاً reject: گزارش مصدومیت روزمره بازیکن گمنام؛ ترکیب احتمالی معمولی؛ پیش‌نمایش کم‌اهمیت؛ آمار خشک بدون داستان؛ نقل‌قول خنثی؛ خبر داخلی باشگاه کوچک؛ پوشش روزمره لیگ‌های کم‌بیننده نزد ایرانی‌ها؛ تکرار همان خبر با زاویه ضعیف‌تر.
نام مشهور یا عدد بزرگ به‌تنهایی کافی نیست؛ باید اتفاق یا زاویهٔ واقعاً جذاب باشد.

## ارزش خبری در selection_reason
در یک جمله بگو: چه اتفاقی افتاده و چرا مخاطب ایرانی ممکن است کلیک کند؟ (یا چرا حذف/بررسی لازم است). صریحاً به کشش بازدید/جذابیت اشاره کن وقتی select می‌کنی.

## محدوده موضوعی
انتخاب کن اگر جذاب است: فوتبال ایران و تیم ملی؛ ستاره‌ها و تیم‌های بزرگ اروپا و جهان؛ لیگ‌های سطح اول و مسابقات مهم؛ حاشیه‌ها و اتفاقات وایرال فوتبال؛ موارد نادر در سایر ورزش‌ها فقط وقتی چهره/رویداد برای ایرانی‌ها خیلی شناخته‌شده یا وایرال است.
حذف کن: اخبار معمول دسته‌های پایین، تیم محلی/دانشگاهی/ذخیره/پایه، پوشش روزمره کریکت/فوتبال آمریکایی/بیسبال/هاکی/دوچرخه/تنیس/بسکتبال/والیبال/گلف و مشابه — مگر استثنای روشن با کشش بالا نزد مخاطب ایرانی.
خبر کاملاً غیرورزشی فقط با اهمیت عمومی استثنایی و ذکر «خارج از زمین» در selection_reason؛ تغییر ظاهر سلبریتی این شرط را ندارد. ارتباط ورزشی اختراع نکن.
وجود در خوراک sports مجوز خودکار انتخاب نیست. «مرتبط با ایران» هم مجوز خودکار نیست؛ باید جذاب باشد.

## دقت ادعا و منبع
وضعیت محتوا: {content_status}
- اگر content_status برابر rss یا blocked است: فقط به اطلاعات صریح عنوان/خلاصه اتکا کن؛ تحلیل، علت، نقل‌قول تازه، جزئیات قرارداد یا متن بلند نساز. پیش‌نویس را کوتاه و محدود نگه دار و محدودیت را در selection_reason بنویس. اگر اصل ادعا مبهم است decision=needs_review.
- اگر full است: از متن کامل استفاده کن؛ از URL، تصویر، شهرت رسانه یا دانش قبلی خبر را تکمیل نکن.
توافق، پیشنهاد، مذاکره، «در آستانه پیوستن»، انتقال نهایی و اعلام رسمی یکسان نیستند. «پیوست» ننویس مگر قطعی باشد. ادعای یک رسانه را اعلام رسمی معرفی نکن.
مبلغ انتقال را با دستمزد/کل ارزش قرارداد اشتباه نگیر؛ واحد پول، پاداش و «تا سقف» را حفظ کن. قرضی‌بودن و اختیار/الزام خرید را حذف نکن.
ادعا یا اتهام را واقعیت اثبات‌شده ننویس. نام تیم/بازیکن/لیگ/زمان را دقیق نگه دار.

## تیتر (site_title)
یک تیتر مستقل فارسی؛ معمولاً ۳ تا ۷ کلمه و در صورت نیاز تا حدود ۹ کلمه. برای کوتاه‌کردن، نام ضروری را حذف نکن.
کاربرد محدود «/»: فقط اگر تیتر خیلی کوتاه برای فهم دقیق به توضیح نیاز دارد، پس از «/» جمله‌ای با فعل در حدود ۶ تا ۸ کلمه بیاور؛ بخش دوم بخش اول را تکرار نکند. تیتر کامل ۸–۹ کلمه‌ای معمولاً بخش دوم نمی‌خواهد.
اصل اتفاق، تغییر، عدد معنادار یا پیامد مستند را جلو بیاور. به‌جای نام کم‌شناخته، سمت آشنا و دقیق بنویس (مثل «مالک آرسنال»، «مدیر اجرایی لالیگا»)؛ نام کامل در لید/متن بماند. نام ستاره‌های شناخته‌شده را حذف نکن. «منچستر» را به‌جای نام دقیق باشگاه ننویس.
شروع‌هایی مثل «بررسی وضعیت»، «تحلیلی بر»، «گزارش تحولات» و عبارت‌های اداری مثل «با هدف تقویت ترکیب»، «در راستای»، «موفق به جذب شد» را مگر ضروری حذف کن.
«بمب»، «شوک»، «زلزله»، «باورنکردنی» الزامی نیستند؛ فقط برای اتفاق واقعاً بزرگ با شواهد روشن و بدون تکرار در بسته. «فوری» فقط برای تحول واقعاً فوری.
«چرا/چگونه» فقط وقتی متن پاسخ مستند دارد. نتیجه یا علت بیرون از متن اضافه نکن.
منبع، URL، نام رسانه، هشتگ و ایموجی داخل تیتر نباشد.

## لید و متن سایت
- site_lead: ۲–۳ جمله رسمی فارسی؛ مهم‌ترین واقعیت‌ها؛ جزئیات تکمیلی اینجا بیاید نه در تیتر.
- site_body: HTML فارسی فقط با <h2> و <p>؛ حداقل دو <h2> وقتی محتوا کافی است. بازنویسی طبیعی، نه ترجمه جمله‌به‌جمله. تکرار نکن. اگر محتوا ناقص است، کوتاه بنویس و بخش‌های تحلیلی اختراع نکن.

## تلگرام (telegram_text)
پست مستقل کانال ورزشی فارسی (نه خلاصه سایت): تیتر کوتاه + پاراگراف کوتاه؛ نقل‌قول فقط اگر مهم؛ نتیجه بازی را روشن بنویس؛ انتقال/مصدومیت/قرارداد را برجسته کن؛ حدود ۴۰–۱۲۰ کلمه؛ حداکثر دو ایموجی مثل ⚽ یا 📌؛ بدون هشتگ و بدون HTML؛ در خط آخر دقیقاً:
{channel_id}

## زبان
تقریباً تمام خروجی فارسی. نام بازیکن/مربی/باشگاه/رقابت را به صورت پذیرفته‌شده فارسی بنویس (لیونل مسی، منچستر یونایتد، لیگ قهرمانان اروپا). انگلیسی فقط برای برند بدون معادل فارسی. رقم فارسی، نیم‌فاصله طبیعی. نقل‌قول‌های داخل JSON را escape کن.

## خروجی JSON (فقط همین کلیدها؛ بدون markdown و بدون متن اضافه)
- "decision": یکی از select | reject | needs_review
- "selection_reason": دلیل کوتاه تحریریه به فارسی (چرا برای مخاطب ایرانی قابل‌کلیک است / چرا حذف / چرا نیازمند بررسی)
- "site_title": تیتر فارسی (برای reject خالی بگذار "")
- "site_lead": لید (برای reject خالی)
- "site_body": بدنه HTML سایت (برای reject خالی)
- "telegram_text": متن تلگرام (برای reject خالی؛ برای select/needs_review با {channel_id} در انتها)

---
Original Title: {title}
Source: {source_name}
Content status: {content_status}
Raw Content:
{content}
---
"""


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` (or plain ```) wrappers Gemini sometimes adds."""
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    cleaned = _FENCE_RE.sub("", cleaned)
    return cleaned.strip()



# Image URLs hosted by these CDNs occasionally don't load when fetched by
# Telegram's servers. We don't filter them out — Telegram will reject them
# with an ApiTelegramException and the bot will gracefully fall back to text.
_IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|bmp)(?:\?.*)?$", re.IGNORECASE)


def _looks_like_image_url(url: str, mime: str = "") -> bool:
    if not url or not isinstance(url, str):
        return False
    if mime and mime.lower().startswith("image/"):
        return True
    return bool(_IMG_EXT_RE.search(url))


def _extract_image_candidates(
    entry: feedparser.FeedParserDict,
    raw_html: str,
) -> list[tuple[str, str]]:
    """Return [(source, url), ...] for every image URL we can find on the entry.

    Sources tried, in priority order, mirror the most common RSS conventions:

      1. media:thumbnail   (Yahoo Media RSS — `entry.media_thumbnail`)
      2. media:content     (Yahoo Media RSS — `entry.media_content`,
                            filtered to image/* types)
      3. enclosure         (RSS 2.0 standard — `entry.enclosures`,
                            filtered to image MIME types)
      4. <img src="..."/>  (first image in the HTML body)
      5. entry.image       (RSS 1.0 / Atom — sometimes `{href: ...}`)
      6. entry.itunes_image, entry.links rel=enclosure as a last resort
    """
    candidates: list[tuple[str, str]] = []

    thumbnails = getattr(entry, "media_thumbnail", None) or []
    for t in thumbnails:
        url = t.get("url") if isinstance(t, dict) else None
        if _looks_like_image_url(url or ""):
            candidates.append(("media:thumbnail", url))

    media_contents = getattr(entry, "media_content", None) or []
    for m in media_contents:
        if not isinstance(m, dict):
            continue
        url = m.get("url")
        mime = m.get("type", "") or m.get("medium", "")
        if _looks_like_image_url(url or "", mime):
            candidates.append(("media:content", url))

    enclosures = getattr(entry, "enclosures", None) or []
    for e in enclosures:
        if not isinstance(e, dict):
            continue
        url = e.get("url") or e.get("href")
        mime = e.get("type", "")
        if _looks_like_image_url(url or "", mime):
            candidates.append(("enclosure", url))

    if raw_html:
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
            img_tag = soup.find("img")
            if img_tag and img_tag.get("src"):
                candidates.append(("html_img", img_tag["src"]))
        except Exception:
            pass

    image_field = getattr(entry, "image", None)
    if isinstance(image_field, dict):
        url = image_field.get("href") or image_field.get("url")
        if _looks_like_image_url(url or ""):
            candidates.append(("entry.image", url))

    itunes_image = getattr(entry, "itunes_image", None)
    if isinstance(itunes_image, dict):
        url = itunes_image.get("href")
        if _looks_like_image_url(url or ""):
            candidates.append(("itunes_image", url))

    for link in getattr(entry, "links", None) or []:
        if not isinstance(link, dict):
            continue
        if link.get("rel") == "enclosure":
            url = link.get("href")
            mime = link.get("type", "")
            if _looks_like_image_url(url or "", mime):
                candidates.append(("link[rel=enclosure]", url))

    return candidates


class Command(BaseCommand):
    help = (
        "Collect a 1-hour in-memory RSS pool from all active sources, "
        "dedupe same stories by source priority, then rewrite newest-first "
        "with Gemini into pending NewsArticle rows. "
        f"At most {MAX_REWRITES_PER_RUN} articles per run, "
        f"≥{GEMINI_MIN_INTERVAL_SECONDS // 60} minutes between Gemini requests. "
        "URLs already in the database are never rewritten again."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        load_dotenv(settings.BASE_DIR / ".env")

        try:
            arvan_config = load_arvan_ai_config()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not gemini_api_key or gemini_api_key == "your_api_key_here":
            raise CommandError(
                "GEMINI_API_KEY is not set. Required for embeddings "
                "(semantic dedup / news pool). Add it to your .env file."
            )

        chat_client = ArvanChatClient(arvan_config)
        model_name = arvan_config.model
        pool_config = load_news_pool_config()

        # Direct Google Gemini client — embeddings only (unchanged from before).
        embed_client = genai.Client(
            api_key=gemini_api_key,
            http_options=types.HttpOptions(
                timeout=GEMINI_REQUEST_TIMEOUT * 1000,
            ),
        )

        self.stdout.write(
            f"Rewrite via Arvan AI model: {model_name}"
        )
        self.stdout.write(f"Chat endpoint: {arvan_config.chat_url}")
        self.stdout.write(
            "Embeddings via Google Gemini (direct API) — unchanged."
        )
        self.stdout.write(
            f"Rewrite budget this run: {MAX_REWRITES_PER_RUN} LLM requests, "
            f"min {GEMINI_MIN_INTERVAL_SECONDS // 60} min between them."
        )
        self.stdout.write(
            f"News pool: lookback={pool_config.lookback_hours:g}h, "
            f"cross-source dedup threshold={pool_config.dedup_threshold:.2f}."
        )

        # Per-run counters: at most MAX_REWRITES_PER_RUN LLM calls.
        # Already-stored URLs are never rewritten again (DB uniqueness).
        self._gemini_requests_done = 0
        self._last_gemini_at: float | None = None

        def _dedup_log(message: str) -> None:
            self.stdout.write(self.style.HTTP_INFO(f"  [semantic-dedup] {message}"))

        def _pool_log(message: str) -> None:
            self.stdout.write(self.style.HTTP_INFO(f"  [news-pool] {message}"))

        semantic_filter = build_semantic_dedup_filter(
            embed_client,
            log=_dedup_log,
        )

        sources = list(
            RssSource.objects.filter(is_active=True).order_by("priority", "name")
        )
        if not sources:
            self.stdout.write(self.style.WARNING("No active RSS sources found."))
            return

        # --- Phase 1: short-term pool (last N hours from all feeds) ----------
        collect_result = collect_recent_pool(
            sources,
            config=pool_config,
            log=_pool_log,
        )

        # --- Phase 2: same-story collapse by source priority -----------------
        dedupe_result = dedupe_pool_by_priority(
            collect_result.candidates,
            embedding_service=semantic_filter.embedding_service,
            config=pool_config,
            log=_pool_log,
        )
        queue = dedupe_result.candidates

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n>>> Processing pool "
                f"({len(queue)} candidates after priority dedupe, "
                f"newest first)"
            )
        )

        totals = {
            "created": 0,
            "rejected_by_llm": 0,
            "topic_skipped": 0,
            "skipped": 0,
            "semantic_skipped": 0,
            "errors": 0,
            "pool_dropped": dedupe_result.stats.dropped,
        }
        limit_reached = False

        for index, candidate in enumerate(queue, start=1):
            if self._gemini_requests_done >= MAX_REWRITES_PER_RUN:
                limit_reached = True
                break

            self.stdout.write(
                self.style.HTTP_INFO(
                    f"\n  [{index}/{len(queue)}] "
                    f"{candidate.pub_date.isoformat()} | "
                    f"p={candidate.priority} | {candidate.source_name} | "
                    f"{candidate.title[:70]}"
                )
            )

            stats = self._process_candidate(
                candidate,
                chat_client,
                model_name,
                semantic_filter,
            )
            for key, value in stats.items():
                totals[key] += value

        if limit_reached:
            self.stdout.write(
                self.style.WARNING(
                    f"\nReached rewrite budget ({MAX_REWRITES_PER_RUN} LLM "
                    "requests). Remaining pool items will wait for the next "
                    "hourly cycle (if still within the lookback window)."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                "\nDone. "
                f"Created (pending): {totals['created']}, "
                f"rejected by editorial filter: {totals['rejected_by_llm']}, "
                f"skipped (topic prefilter): {totals['topic_skipped']}, "
                f"skipped (URL duplicate): {totals['skipped']}, "
                f"skipped (semantic vs site): {totals['semantic_skipped']}, "
                f"dropped (cross-source pool): {totals['pool_dropped']}, "
                f"errors: {totals['errors']}."
            )
        )

    def _wait_for_gemini_slot(self) -> None:
        """Block until at least GEMINI_MIN_INTERVAL_SECONDS since the last request."""
        if self._last_gemini_at is None:
            return

        elapsed = time.monotonic() - self._last_gemini_at
        remaining = GEMINI_MIN_INTERVAL_SECONDS - elapsed
        if remaining <= 0:
            return

        minutes = remaining / 60.0
        self.stdout.write(
            self.style.HTTP_INFO(
                f"  ⏳ waiting {remaining:.0f}s ({minutes:.1f} min) before next "
                f"Gemini rewrite "
                f"({self._gemini_requests_done}/{MAX_REWRITES_PER_RUN} requests used)..."
            )
        )
        time.sleep(remaining)

    def _scrape_log(self, message: str, error: bool = False) -> None:
        timestamp = time.strftime("%H:%M:%S")
        stream = self.stderr if error else self.stdout
        style = self.style.ERROR if error else self.style.HTTP_INFO
        stream.write(style(f"  [{timestamp}] [scrape] {message}") + "\n")
        stream.flush()

    def _process_candidate(
        self,
        candidate: PoolCandidate,
        chat_client: ArvanChatClient,
        model_name: str,
        semantic_filter: SemanticDedupFilter,
    ) -> dict[str, int]:
        """Run site semantic-dedup + scrape + Arvan rewrite for one pooled candidate."""
        return self._process_entry(
            candidate.entry,
            candidate.source,
            chat_client,
            model_name,
            semantic_filter,
            canonical_url=candidate.canonical_url,
            title=candidate.title,
        )

    def _process_entry(
        self,
        entry: feedparser.FeedParserDict,
        source: RssSource,
        chat_client: ArvanChatClient,
        model_name: str,
        semantic_filter: SemanticDedupFilter,
        *,
        canonical_url: str | None = None,
        title: str | None = None,
    ) -> dict[str, int]:
        stats = {
            "created": 0,
            "rejected_by_llm": 0,
            "topic_skipped": 0,
            "skipped": 0,
            "semantic_skipped": 0,
            "errors": 0,
        }

        link = (getattr(entry, "link", "") or "").strip()
        title = (title or getattr(entry, "title", "") or "").strip()

        if not link or not title:
            self.stderr.write(self.style.WARNING("  Skipping entry without link/title."))
            stats["errors"] += 1
            return stats

        if not canonical_url:
            canonical_url = normalize_article_url(link)
        if not canonical_url:
            self.stderr.write(self.style.WARNING("  Skipping entry with empty URL."))
            stats["errors"] += 1
            return stats

        if link != canonical_url:
            self.stdout.write(
                f"  → URL normalized: {link!r} → {canonical_url!r}"
            )

        # Hard guarantee: any URL already in the DB (pending/published/rejected)
        # is never sent to the LLM again — including on later hourly cycles.
        if self._article_exists(canonical_url):
            self.stdout.write(
                f"  - duplicate, skipped: {title[:80]} ({canonical_url})"
            )
            stats["skipped"] += 1
            return stats

        # Semantic dedup runs BEFORE scrape/LLM so we avoid expensive work
        # on stories already covered by Khabar Varzeshi in the last 24 hours.
        match = semantic_filter.check_entry(entry)
        if match.skipped_due_to_error:
            self.stderr.write(self.style.WARNING(
                f"  ! semantic dedup unavailable for '{title[:60]}' "
                f"— continuing ({match.detail})"
            ))
        elif match.is_duplicate:
            self.stdout.write(self.style.WARNING(
                f"  - semantic duplicate, skipped: {title[:80]} "
                f"| score={match.similarity:.3f} "
                f"| matched={match.matched_title[:80]!r}"
            ))
            if match.matched_url:
                self.stdout.write(f"      baseline url: {match.matched_url}")
            stats["semantic_skipped"] += 1
            return stats
        else:
            self.stdout.write(self.style.HTTP_INFO(
                f"  → semantic ok | score={match.similarity:.3f} "
                f"| {match.detail}"
            ))

        rss_summary = ""
        try:
            rss_summary = _clean_html_to_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            )
        except Exception:
            rss_summary = ""

        topic = assess_topic(title, rss_summary)
        if topic.skip:
            self.stdout.write(self.style.WARNING(
                f"  - topic prefilter skip: {title[:80]} | {topic.reason}"
            ))
            # Persist as rejected so the URL is not reconsidered every cycle.
            try:
                NewsArticle.objects.create(
                    source=source,
                    original_title=title[:255],
                    original_url=canonical_url,
                    editorial_note=f"حذف پیش‌فیلتر موضوع: {topic.reason}",
                    content_status="prefilter",
                    status=NewsArticle.Status.REJECTED,
                )
            except IntegrityError:
                pass
            stats["topic_skipped"] += 1
            return stats

        raw_html, content_source, scrape_detail = scrape_article_html(
            link, canonical_url, entry, scrape_log=self._scrape_log,
        )
        clean_text = _clean_html_to_text(raw_html)
        if not clean_text:
            clean_text = title

        if content_source == "webpage":
            content_status = "full"
        elif content_source == "rss":
            content_status = "rss"
        else:
            content_status = "blocked"

        self.stdout.write(self.style.HTTP_INFO(
            f"  → article content "
            f"| source={content_source} "
            f"| status={content_status} "
            f"| html={len(raw_html)} chars "
            f"| text={len(clean_text)} chars"
            + (f" | {scrape_detail}" if scrape_detail and content_source == "webpage" else "")
        ))
        if content_source == "rss":
            self.stderr.write(self.style.WARNING(
                f"  ! webpage scrape unavailable for '{title[:60]}' "
                f"— using RSS fallback ({scrape_detail})."
            ))

        image_candidates = _extract_image_candidates(entry, raw_html)
        image_url = image_candidates[0][1] if image_candidates else None
        present_fields = [
            attr for attr in (
                "media_thumbnail", "media_content", "enclosures",
                "image", "itunes_image", "links",
            )
            if getattr(entry, attr, None)
        ]
        self.stdout.write(self.style.HTTP_INFO(
            f"  → image scan for '{title[:60]}' "
            f"| entry has: {present_fields or 'none'} "
            f"| candidates: {len(image_candidates)} "
            f"| picked: {image_candidates[0][0] if image_candidates else 'NONE'}"
        ))
        for src, url in image_candidates[:5]:
            self.stdout.write(f"      • {src}: {url}")
        if not image_candidates:
            self.stderr.write(self.style.WARNING(
                f"      ! no image found for '{title[:60]}' — Telegram will "
                "be sent text-only."
            ))

        prompt = PROMPT_TEMPLATE.format(
            channel_id=TELEGRAM_CHANNEL_ID,
            title=title,
            source_name=source.name,
            content_status=content_status,
            content=clean_text[:8000],
        )

        self._wait_for_gemini_slot()

        self._gemini_requests_done += 1
        self.stdout.write(self.style.HTTP_INFO(
            f"  → Arvan LLM request "
            f"| model={model_name!r} "
            f"| prompt={len(prompt)} chars "
            f"| timeout={chat_client.config.timeout_seconds}s "
            f"| request={self._gemini_requests_done}/{MAX_REWRITES_PER_RUN}"
        ))

        # Stamp before the call so spacing is measured between request starts
        # (and still ≥5 min even if a call fails).
        self._last_gemini_at = time.monotonic()

        try:
            raw_text = chat_client.complete(
                prompt,
                temperature=0.5,
                max_tokens=8000,
                json_mode=True,
            )
            if not raw_text.strip():
                raise ValueError("Empty response from Arvan AI.")

            parsed = json.loads(_strip_markdown_fences(raw_text))
            if not isinstance(parsed, dict):
                raise ValueError("Arvan AI did not return a JSON object.")

            missing = [k for k in REQUIRED_KEYS if k not in parsed]
            if missing:
                raise ValueError(f"Missing keys in LLM response: {missing}")

            decision = str(parsed.get("decision") or "").strip().lower()
            if decision not in VALID_DECISIONS:
                raise ValueError(f"Invalid decision from LLM: {decision!r}")

            selection_reason = (parsed.get("selection_reason") or "").strip()
            telegram_text = (parsed.get("telegram_text") or "").strip()
            site_title = (parsed.get("site_title") or "").strip()
            site_lead = (parsed.get("site_lead") or "").strip()
            site_body = (parsed.get("site_body") or "").strip()

            if decision in SELECT_DECISIONS:
                if not site_title or not telegram_text:
                    raise ValueError(
                        f"decision={decision} requires site_title and telegram_text"
                    )
                if TELEGRAM_CHANNEL_ID not in telegram_text:
                    telegram_text = f"{telegram_text}\n\n{TELEGRAM_CHANNEL_ID}".strip()
                if decision == "needs_review" and selection_reason:
                    editorial_note = f"نیازمند بررسی: {selection_reason}"
                elif selection_reason:
                    editorial_note = f"چرا مهم: {selection_reason}"
                else:
                    editorial_note = None
                article_status = NewsArticle.Status.PENDING
            else:
                editorial_note = (
                    f"حذف عامل: {selection_reason}" if selection_reason
                    else "حذف عامل: کم‌جذاب یا کم‌کلیک برای مخاطب ایرانی"
                )
                article_status = NewsArticle.Status.REJECTED
                site_title = site_title or None
                site_lead = None
                site_body = None
                telegram_text = None

            # Re-check after the (slow) LLM call — another worker may have
            # inserted the same URL while we were waiting.
            if self._article_exists(canonical_url):
                self.stdout.write(
                    self.style.WARNING(
                        f"  - duplicate after LLM, skipped: {title[:80]} "
                        f"({canonical_url})"
                    )
                )

                stats["skipped"] += 1
                return stats

            try:
                NewsArticle.objects.create(
                    source=source,
                    original_title=title[:255],
                    original_url=canonical_url,
                    image_url=(image_url or None) if decision in SELECT_DECISIONS else None,
                    site_title=(site_title[:255] if site_title else None),
                    site_lead=site_lead or None,
                    site_body=site_body or None,
                    telegram_text=telegram_text or None,
                    editorial_note=editorial_note,
                    content_status=content_status,
                    status=article_status,
                )
            except IntegrityError:
                self.stdout.write(
                    self.style.WARNING(
                        f"  - duplicate on save (DB constraint), skipped: "
                        f"{title[:80]} ({canonical_url})"
                    )
                )
                stats["skipped"] += 1
                return stats

            if decision in SELECT_DECISIONS:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  + created ({decision}): {title[:80]} "
                        f"(LLM {self._gemini_requests_done}/{MAX_REWRITES_PER_RUN})"
                    )
                )
                if selection_reason:
                    self.stdout.write(f"      reason: {selection_reason[:160]}")
                stats["created"] += 1
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  - editorial reject: {title[:80]} "
                        f"| {selection_reason[:120]}"
                    )
                )
                stats["rejected_by_llm"] += 1

        except TIMEOUT_EXCEPTIONS as exc:
            self.stderr.write(self.style.ERROR(
                f"  ! Arvan LLM failure for '{title[:60]}': "
                f"type={type(exc).__name__} | message={exc!s}"
            ))
            self.stderr.write(self.style.ERROR(
                f"    model={model_name!r}, "
                f"timeout={chat_client.config.timeout_seconds}s, "
                f"prompt size={len(prompt)} chars"
            ))
            self.stderr.write(self.style.ERROR(traceback.format_exc()))
            stats["errors"] += 1
        except json.JSONDecodeError as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"  ! Invalid JSON from Arvan AI for '{title[:60]}': {exc}. Skipping."
                )
            )
            stats["errors"] += 1
        except Exception as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"  ! LLM/processing failure for '{title[:60]}': {exc!r}. Skipping."
                )
            )
            self.stderr.write(self.style.WARNING(traceback.format_exc()))
            stats["errors"] += 1

        return stats

    @staticmethod
    def _article_exists(canonical_url: str) -> bool:
        """Return True if an article with this canonical URL is already stored."""
        return NewsArticle.objects.filter(original_url=canonical_url).exists()
