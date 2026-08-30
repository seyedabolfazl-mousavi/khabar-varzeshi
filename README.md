# Khabar Varzeshi — Editorial Automation Platform

Automated sports news ingestion, AI-powered Persian rewriting, semantic deduplication, and editorial workflow for [**Khabar Varzeshi**](https://www.khabarvarzeshi.com) (`خبرورزشی`).

English-language RSS feeds are collected on an hourly schedule, rewritten into professional Persian via **Arvan Cloud AI** (OpenAI-compatible chat completions), filtered against the site's recent corpus using **Google Gemini embeddings**, and routed through a **Telegram editorial bot** for human review before broadcast to a public channel and/or publication on the newsroom CMS.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Technology Stack](#technology-stack)
- [Pipeline Stages](#pipeline-stages)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Management Commands](#management-commands)
- [Telegram Editorial Bot](#telegram-editorial-bot)
- [Semantic Deduplication](#semantic-deduplication)
- [Newsroom Publishing](#newsroom-publishing)
- [Data Models](#data-models)
- [Project Structure](#project-structure)
- [Operational Notes](#operational-notes)
- [Troubleshooting](#troubleshooting)

---

## Architecture Overview

The system runs as **two long-lived processes** in production:

| Process | Command | Responsibility |
|---------|---------|----------------|
| **Worker** | `python manage.py run_worker` | Hourly RSS ingestion, AI rewrite, admin notifications |
| **Bot** | `python manage.py run_bot` | Telegram editorial UI (aiogram long polling) |

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         RSS Sources (RssSource)                         │
│              Active feeds, priority-ranked, English sports news         │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  fetch_news  (hourly via run_worker)                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ 1. News Pool     │ Collect items from last 1h across all feeds    │  │
│  │ 2. URL Dedup     │ Normalize URLs; skip already-ingested articles  │  │
│  │ 3. Pool Dedup    │ Cross-source same-story clustering (embeddings) │  │
│  │ 4. Semantic Dedup│ Compare vs Khabar Varzeshi 24h RSS baseline     │  │
│  │ 5. Scrape        │ Selenium/BeautifulSoup article body extraction  │  │
│  │ 6. AI Rewrite    │ Arvan Cloud AI → site_title/lead/body + TG text │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ NewsArticle (status=pending)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  run_worker  →  Telegram notification to ALLOWED_ADMIN_IDS              │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  run_bot  (aiogram 3 + FSM)                                             │
│  • Review pending articles (inline keyboard)                              │
│  • Approve → publish to TELEGRAM_PUBLIC_CHANNEL_ID                      │
│  • Reject / edit body / attach site link                                  │
│  • Publish to newsroom.khabarvarzeshi.com via Selenium automation       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

### Backend & Persistence

| Technology | Version | Role |
|------------|---------|------|
| **Python** | 3.11+ | Runtime |
| **Django** | 5.2.x | ORM, admin panel, management commands, SQLite (default) |
| **SQLite** | — | Default database (`db.sqlite3`); swappable for PostgreSQL in production |
| **python-dotenv** | 1.2.x | Environment variable loading from `.env` |

### AI & NLP

| Technology | Role |
|------------|------|
| **Arvan Cloud AI** | OpenAI-compatible `POST /v1/chat/completions` gateway for Persian article rewriting (JSON-structured output) |
| **Google Gemini** (`google-genai`) | Embedding generation only — `gemini-embedding-001` at 768 dimensions |
| **Cosine similarity** | Vector comparison for semantic dedup (pool + baseline corpus) |

### News Ingestion

| Technology | Role |
|------------|------|
| **feedparser** | RSS/Atom feed parsing |
| **httpx** / **requests** | HTTP client for feed fetch and image download |
| **BeautifulSoup4** | HTML parsing, text extraction, image URL discovery |
| **Selenium 4** + **webdriver-manager** | Headless Chrome for article scraping and newsroom form automation |
| **tenacity** | Retry logic for transient failures |

### Telegram & Scheduling

| Technology | Role |
|------------|------|
| **aiogram 3** | Async Telegram bot framework (long polling, FSM, inline keyboards, HTML parse mode) |
| **pyTelegramBotAPI** (`telebot`) | Admin notifications from the hourly worker |
| **schedule** | Cron-like hourly trigger inside `run_worker` |
| **asyncio** | Non-blocking site publish jobs from the bot event loop |

### Supporting Libraries

| Library | Role |
|---------|------|
| **pydantic** | Configuration validation |
| **aiohttp** | Async HTTP (aiogram dependency chain) |
| **cryptography** | TLS / security primitives |

---

## Pipeline Stages

### 1. Short-Term News Pool (`core/news_pool/`)

Every `fetch_news` cycle:

1. Collect RSS items published within the last **1 hour** (`POOL_LOOKBACK_HOURS`) from all active `RssSource` records.
2. Sort by publication date descending (newest first).
3. **Cross-source deduplication**: cluster items describing the same story using Gemini embeddings (default threshold `0.88`). When duplicates exist across feeds, retain the source with the **lowest `priority` value** (lower = higher priority).
4. Skip URLs already present in the database (normalized via `normalize_article_url`).

### 2. Semantic Baseline Filter (`core/semantic_dedup/`)

Before scraping or rewriting, each candidate is compared against the **last 24 hours** of published articles from `https://www.khabarvarzeshi.com/rss`:

- Embeddings are cached in `BaselineArticleEmbedding`.
- Cosine similarity ≥ `SEMANTIC_DEDUP_THRESHOLD` (default `0.80`) → article rejected as duplicate.
- On embedding service failure, **fail-open** behavior (`SEMANTIC_DEDUP_FAIL_OPEN=1`) allows ingestion to continue.

### 3. Article Scraping (`core/article_scraper.py`)

- Primary: headless Chrome with stealth options and configurable user-agent rotation.
- Fallback: RSS `content:encoded` / description fields via BeautifulSoup.
- Image extraction from RSS media tags, enclosures, and Open Graph / article HTML.

### 4. AI Rewrite (`core/arvan_ai/`)

Structured JSON output via Arvan Cloud AI chat completions:

| Field | Description |
|-------|-------------|
| `site_title` | Persian SEO headline |
| `site_lead` | 2–3 sentence lead paragraph |
| `site_body` | HTML body restricted to `<h2>` and `<p>` tags |
| `telegram_text` | Standalone channel post (~40–120 words), ending with `@KhabarVarzeshi` |

**Rate limits per cycle:**

| Constraint | Value |
|------------|-------|
| Max rewrites per run | 10 |
| Min interval between rewrite requests | 5 minutes |
| Re-rewrite of stored URLs | Disabled |

### 5. Editorial Review & Publishing

Pending articles surface in the Telegram bot. Admins approve, reject, edit, attach site links, and optionally trigger Selenium-based CMS publishing.

---

## Prerequisites

- **Python 3.11+**
- [Google AI Studio](https://aistudio.google.com/app/apikey) API key (`GEMINI_API_KEY`) — embeddings only
- [Arvan Cloud AI](https://arvancloudai.ir) account — rewrite gateway (`ARVAN_AI_API_KEY`, `ARVAN_AI_CHAT_URL`)
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Numeric Telegram user IDs from [@userinfobot](https://t.me/userinfobot)
- Public Telegram channel where the bot is an **administrator** with **Post Messages** permission
- *(Optional)* Newsroom CMS credentials + Chrome/Chromium for site publishing

---

## Installation

```bash
git clone <repository-url>
cd khabar_varzeshi

# Virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# Dependencies
pip install -r requirements.txt

# Environment configuration
copy .env.example .env   # Windows
# cp .env.example .env   # Linux / macOS

# Database migrations
python manage.py migrate

# Django superuser (for /admin panel)
python manage.py createsuperuser
```

Configure `.env` with real credentials (see [Environment Variables](#environment-variables)).

In Django Admin (`/admin`), create at least one active **RssSource** (`is_active=True`).

Start the system:

```bash
# Terminal 1 — hourly ingestion worker
python manage.py run_worker

# Terminal 2 — editorial Telegram bot
python manage.py run_bot
```

Manual one-shot ingestion:

```bash
python manage.py fetch_news
```

---

## Environment Variables

Copy `.env.example` to `.env`. **Never commit `.env` to version control.**

### Required

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google Gemini API key (embeddings / semantic dedup only) |
| `ARVAN_AI_API_KEY` | Arvan Cloud AI key (`Authorization: apikey …`) |
| `ARVAN_AI_CHAT_URL` | Full gateway URL ending in `/v1/chat/completions` |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `ALLOWED_ADMIN_IDS` | Comma-separated Telegram user IDs (e.g. `123456789,987654321`) |
| `TELEGRAM_PUBLIC_CHANNEL_ID` | Public channel (`@username` or numeric ID like `-1001234567890`) |

If `ALLOWED_ADMIN_IDS` is empty, `TELEGRAM_ADMIN_CHAT_ID` is used as a single-admin fallback.

### AI Model Overrides

| Variable | Default | Description |
|----------|---------|-------------|
| `ARVAN_AI_MODEL` | `Gemini-3.1-Flash-Lite-Preview` | Rewrite model identifier |
| `ARVAN_AI_TIMEOUT` | `120` | Chat completion timeout (seconds) |
| `SEMANTIC_DEDUP_EMBEDDING_MODEL` | `gemini-embedding-001` | Gemini embedding model |

### Newsroom CMS (Selenium)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEWSROOM_USERNAME` | — | Newsroom dashboard username |
| `NEWSROOM_PASSWORD` | — | Newsroom dashboard password |
| `NEWSROOM_LOGIN_URL` | `https://newsroom.khabarvarzeshi.com/login/login.xhtml` | Login page |
| `NEWSROOM_CREATE_URL` | `https://newsroom.khabarvarzeshi.com/news.xhtml` | Article creation page |
| `NEWSROOM_HEADLESS` | `1` | Headless browser mode |
| `SELENIUM_WAIT_TIMEOUT` | `25` | General element wait (seconds) |
| `SELENIUM_LOGIN_WAIT_TIMEOUT` | `30` | Login flow wait (seconds) |
| `SELENIUM_CKEDITOR_WAIT_TIMEOUT` | `45` | CKEditor iframe wait (seconds) |

### Semantic Deduplication

| Variable | Default | Description |
|----------|---------|-------------|
| `SEMANTIC_DEDUP_ENABLED` | `1` | Enable/disable baseline filter |
| `SEMANTIC_DEDUP_BASELINE_RSS` | `https://www.khabarvarzeshi.com/rss` | Reference RSS feed |
| `SEMANTIC_DEDUP_THRESHOLD` | `0.80` | Cosine similarity rejection threshold |
| `SEMANTIC_DEDUP_DIMENSIONS` | `768` | Embedding vector dimensions |
| `SEMANTIC_DEDUP_LOOKBACK_HOURS` | `24` | Baseline corpus time window |
| `SEMANTIC_DEDUP_FAIL_OPEN` | `1` | Continue ingestion on embedding errors |
| `SEMANTIC_DEDUP_REQUEST_INTERVAL` | `1.2` | Inter-request pacing (seconds) |
| `SEMANTIC_DEDUP_BATCH_SIZE` | `8` | Embedding batch size |
| `SEMANTIC_DEDUP_BATCH_PAUSE` | `2.0` | Pause between batches (seconds) |
| `SEMANTIC_DEDUP_MAX_RETRIES` | `5` | Retry count on HTTP 429 |
| `SEMANTIC_DEDUP_RETRY_BASE_DELAY` | `12` | Exponential backoff base (seconds) |
| `SEMANTIC_DEDUP_RETRY_MAX_DELAY` | `60` | Maximum retry delay (seconds) |

### News Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `POOL_LOOKBACK_HOURS` | `1` | RSS collection window per cycle |
| `POOL_DEDUP_THRESHOLD` | `0.88` | Cross-source same-story threshold |

---

## Management Commands

| Command | Description |
|---------|-------------|
| `python manage.py run_bot` | Start aiogram editorial bot (long polling). Auto-restarts after 10s on network errors. |
| `python manage.py run_worker` | Hourly `fetch_news` scheduler + Telegram admin notifications for new pending articles. |
| `python manage.py fetch_news` | One-shot ingestion: pool collection → dedup → semantic filter → scrape → rewrite → persist as `pending`. |
| `python manage.py remove_duplicate_articles` | Remove duplicate rows by normalized URL (keeps oldest). |
| `python manage.py remove_duplicate_articles --dry-run` | Report duplicates without deleting. |
| `python manage.py runserver` | Django development server for `/admin` panel. |

---

## Telegram Editorial Bot

Built with **aiogram 3**, **FSM** (`MemoryStorage`), and **HTML parse mode**.

### Access Control

- Admin-only handlers gated by `AdminFilter` against `ALLOWED_ADMIN_IDS`.
- Non-admin users receive an access-denied message on restricted actions.

### Commands & Menu

| Input | Access | Action |
|-------|--------|--------|
| `/start`, `/help` | All | Welcome message; admin review instructions |
| **Review Latest News** button / `/check_pending` | Admin | Display up to 20 newest `pending` articles |
| `/cancel` | Admin | Abort in-progress text edit or link attachment FSM state |

### Inline Actions (per article)

| Button | Action |
|--------|--------|
| Approve & Send | Post `telegram_text` (+ image if available) to public channel; set `status=published` |
| Reject | Set `status=rejected`; remove inline keyboard |
| Edit Text | Enter FSM; edit post body only (link + footer preserved) |
| Add Link | Attach HTML link: «در سایت خبرورزشی بخوانید» |
| Publish to Site | Background Selenium job → newsroom CMS (requires `site_title`, `site_lead`, `site_body`) |

Button labels update reactively: `Sent to channel`, `Published on site`, `Publishing…`.

### Telegram Message Composition

`telegram_text` structure (managed by `core/bot/text_compose.py`):

1. **Body** — editable main post text
2. **Optional link** — `<a href="…">در سایت خبرورزشی بخوانید</a>`
3. **Footer** — `@KhabarVarzeshi`

Telegram limits enforced: photo caption ≤ 1024 chars, text message ≤ 4096 chars. Photo send failures fall back to text-only delivery.

---

## Semantic Deduplication

Module: `core/semantic_dedup/`

```
Incoming RSS candidate
        │
        ▼
build_embedding_document(title + description)
        │
        ▼
Gemini embed_content (768-dim, L2-normalized)
        │
        ▼
cosine_similarity vs BaselineArticleEmbedding corpus
        │
        ├── ≥ threshold → reject (duplicate)
        └── < threshold → proceed to scrape + rewrite
```

- Baseline corpus loaded from Khabar Varzeshi RSS and cached in `BaselineArticleEmbedding`.
- `EmbeddingService` implements free-tier rate pacing (~100 req/min) with exponential backoff on HTTP 429.
- Pool-level dedup (`core/news_pool/dedupe.py`) uses the same embedding infrastructure with a stricter threshold (`0.88`).

---

## Newsroom Publishing

Module: `core/newsroom/`

Triggered from the Telegram bot via `asyncio.create_task` to avoid blocking the event loop:

1. Validate `site_title`, `site_lead`, `site_body`.
2. Download hero image (if `image_url` present).
3. Selenium WebDriver (Chrome + webdriver-manager) logs into the JSF-based newsroom dashboard.
4. Populate headline, lead, and CKEditor body fields; upload image.
5. Update the review message with success or `SitePublishError` details.

Site-published state is tracked in-process (resets on bot restart). Channel publish state persists in `NewsArticle.status`.

---

## Data Models

### `RssSource`

| Field | Type | Description |
|-------|------|-------------|
| `name` | CharField | Human-readable source name |
| `url` | URLField (unique) | RSS feed URL |
| `category` | CharField | Optional category label |
| `priority` | PositiveIntegerField | Lower value = higher priority in cross-source dedup (default: 100) |
| `is_active` | BooleanField | Only active sources are polled |

### `NewsArticle`

| Field | Type | Description |
|-------|------|-------------|
| `source` | FK → RssSource | Originating feed |
| `original_title` | CharField | Source headline |
| `original_url` | URLField (unique) | Normalized canonical URL |
| `image_url` | URLField | Hero image |
| `site_title` | CharField | Rewritten Persian headline |
| `site_lead` | TextField | Rewritten lead |
| `site_body` | TextField | Rewritten HTML body |
| `telegram_text` | TextField | Channel post text |
| `status` | CharField | `pending` \| `published` \| `rejected` |
| `created_at` | DateTimeField | Ingestion timestamp |

### `BaselineArticleEmbedding`

| Field | Type | Description |
|-------|------|-------------|
| `guid` | CharField (unique) | RSS item GUID |
| `url` | URLField | Article URL |
| `title` | CharField | Baseline headline |
| `description` | TextField | RSS description snippet |
| `pub_date` | DateTimeField | Publication timestamp |
| `embedding_model` | CharField | Model identifier used |
| `embedding` | JSONField | L2-normalized vector (768 floats) |
| `updated_at` | DateTimeField | Cache refresh timestamp |

---

## Project Structure

```
khabar_varzeshi/
├── manage.py
├── requirements.txt
├── .env.example
├── khabar_varzeshi/              # Django project settings
│   ├── settings.py               # SQLite default; INSTALLED_APPS: core
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
└── core/
    ├── models.py                 # RssSource, NewsArticle, BaselineArticleEmbedding
    ├── admin.py                  # Django Admin registrations
    ├── article_scraper.py        # Selenium + BeautifulSoup article extraction
    ├── url_utils.py              # URL normalization
    ├── arvan_ai/                 # Arvan Cloud AI chat client
    │   ├── chat.py               # OpenAI-compatible completions
    │   ├── config.py
    │   └── http.py
    ├── bot/                      # Telegram editorial bot (aiogram 3)
    │   ├── app.py                # Dispatcher factory + polling
    │   ├── config.py
    │   ├── auth.py               # AdminFilter
    │   ├── keyboards.py
    │   ├── services.py           # DB queries + message send/edit
    │   ├── site_publish.py       # Async site publish orchestration
    │   ├── states.py             # FSM state definitions
    │   ├── text_compose.py       # telegram_text parse/compose
    │   ├── review_panels.py
    │   └── handlers/
    │       ├── common.py         # /start, /help, /cancel
    │       ├── check_pending.py
    │       ├── review.py         # approve / reject / edit / link / site
    │       └── fsm.py            # FSM text/link handlers
    ├── news_pool/                # Short-term in-memory pool
    │   ├── collect.py            # RSS aggregation
    │   ├── dedupe.py             # Priority-aware clustering
    │   ├── candidates.py
    │   └── config.py
    ├── newsroom/                 # Selenium CMS automation
    │   ├── automation.py         # Login, form fill, CKEditor, image upload
    │   ├── publisher.py
    │   ├── config.py
    │   └── exceptions.py
    ├── semantic_dedup/           # Baseline semantic filter
    │   ├── filter.py
    │   ├── baseline.py           # RSS corpus loader + cache
    │   ├── embeddings.py         # Gemini EmbeddingService
    │   ├── vectors.py            # Cosine similarity, L2 normalize
    │   ├── text.py               # Document construction
    │   └── config.py
    └── management/commands/
        ├── run_bot.py
        ├── run_worker.py
        ├── fetch_news.py
        └── remove_duplicate_articles.py
```

---

## Operational Notes

### IPv4 Enforcement

`fetch_news`, `run_worker`, and `run_bot` patch `socket.getaddrinfo` and `urllib3` to force **IPv4-only** outbound connections, preventing IPv6 timeout issues on certain networks.

### Process Model

| Component | Always Running? | Trigger |
|-----------|-----------------|---------|
| `run_bot` | Yes (business hours / 24×7) | Manual start |
| `run_worker` | Yes | Internal `schedule` — every 1 hour |
| `fetch_news` | No | Called by worker or manually |

The **Review Latest News** button does **not** re-run ingestion; it only displays existing `pending` rows.

### Database

Default: **SQLite** (`db.sqlite3`). For production workloads, configure PostgreSQL or another Django-supported backend in `khabar_varzeshi/settings.py`.

### Security

- Store all secrets in `.env` (gitignored).
- Restrict `ALLOWED_ADMIN_IDS` to trusted editorial staff.
- Rotate `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, and `ARVAN_AI_API_KEY` on compromise.
- Do not commit `db.sqlite3` or credential files.

### Chrome / Selenium

Site publishing and article scraping require **Google Chrome** or **Chromium**. `webdriver-manager` auto-downloads a matching ChromeDriver.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Bot fails to start | `TELEGRAM_BOT_TOKEN`, `ALLOWED_ADMIN_IDS`, channel ID in `.env` |
| `fetch_news` errors | `ARVAN_AI_*` credentials, `GEMINI_API_KEY`, active `RssSource` records |
| No new articles | Is `run_worker` running? Valid RSS URLs? Semantic dedup rejecting all candidates? |
| Channel publish fails | Bot is channel admin? Correct `TELEGRAM_PUBLIC_CHANNEL_ID`? |
| Site publish fails | `NEWSROOM_*` credentials, network access, Chrome/Selenium availability |
| Stale keyboard buttons | Send `/start` to refresh the reply keyboard |
| Embedding rate limits | Increase `SEMANTIC_DEDUP_REQUEST_INTERVAL`; verify Gemini quota |

---

## License

Internal editorial automation project for **Khabar Varzeshi**. Deployment and usage subject to editorial team policy.
