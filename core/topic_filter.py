"""Conservative topic pre-filter for Iranian football-first editorial scope.

Rejects only obvious low-value non-football noise before the LLM rewrite.
Ambiguous or exception-worthy items pass through for the editorial prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicFilterResult:
    skip: bool
    reason: str = ""


# Daily coverage of sports that are out of scope unless a clear exception applies.
_LOW_PRIORITY_SPORT_RE = re.compile(
    r"(?i)\b("
    r"NFL|MLB|NHL|NBA|"
    r"American\s+football|super\s*bowl|"
    r"baseball|cricket|ice\s*hockey|field\s*hockey|"
    r"NASCAR|Formula\s*1|F1\b|MotoGP|"
    r"PGA\b|LPGA|golf\b|"
    r"WTA\b|ATP\b|"
    r"UFC\b|MMA\b|"
    r"CFL\b|XFL\b|"
    r"la\s+vuelta|tour\s+de\s+france|giro\s+d['’]?italia|"
    r"cycling\s+stage|stage\s+\d+\s+of"
    r")\b"
)

# Keep items that may still matter for the Iranian sports audience.
_EXCEPTION_RE = re.compile(
    r"(?i)\b("
    r"Iran|Iranian|Persia|Persian|"
    r"Arsenal|Chelsea|Liverpool|Manchester\s+United|Manchester\s+City|"
    r"Real\s+Madrid|Barcelona|Bayern|Juventus|PSG|Inter\s+Milan|AC\s+Milan|"
    r"Messi|Ronaldo|Mbapp[eé]|Haaland|Yamal|"
    r"Premier\s+League|La\s+Liga|Serie\s+A|Bundesliga|Ligue\s+1|"
    r"Champions\s+League|Europa\s+League|World\s+Cup|Olympics?"
    r")\b"
)

# Celebrity / entertainment with no sports activity signal.
_NON_SPORT_CELEBRITY_RE = re.compile(
    r"(?i)\b("
    r"Nicole\s+Kidman|red\s+carpet|plastic\s+surgery|facelift|"
    r"Hollywood\s+actress|movie\s+premiere|Oscar\s+dress"
    r")\b"
)

_SPORT_ACTIVITY_RE = re.compile(
    r"(?i)\b("
    r"match|game|goal|transfer|contract|club|team|coach|manager|"
    r"league|tournament|championship|olympic|athlete|football|soccer|"
    r"injury|fixture|stadium|kit|sponsor"
    r")\b"
)


def assess_topic(title: str, content_snippet: str = "") -> TopicFilterResult:
    """Return whether this item should be skipped before LLM rewrite."""
    text = f"{title or ''}\n{(content_snippet or '')[:1200]}"

    if _NON_SPORT_CELEBRITY_RE.search(text) and not _SPORT_ACTIVITY_RE.search(text):
        return TopicFilterResult(
            skip=True,
            reason="غیرورزشی/سلبریتی بدون ارتباط ورزشی روشن",
        )

    if _LOW_PRIORITY_SPORT_RE.search(text) and not _EXCEPTION_RE.search(text):
        return TopicFilterResult(
            skip=True,
            reason="رشته کم‌اولویت یا پوشش روزمره خارج از محدوده مخاطب ایرانی",
        )

    return TopicFilterResult(skip=False)
