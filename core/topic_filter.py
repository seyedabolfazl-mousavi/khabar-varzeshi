"""Cheap pre-filter before the Gemini suitability score.

Drops only obvious non-industry noise so those items never spend an API call.
Ambiguous industrial items pass through to the scorer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicFilterResult:
    skip: bool
    reason: str = ""


_NON_INDUSTRY_RE = re.compile(
    r"(?i)\b("
    r"NFL|MLB|NHL|NBA|Premier\s+League|Champions\s+League|"
    r"World\s+Cup|Super\s+Bowl|Olympics?|"
    r"celebrity|red\s+carpet|horoscope|box\s+office"
    r")\b"
)

_INDUSTRY_EXCEPTION_RE = re.compile(
    r"(?i)\b("
    r"steel|mine|mining|oil|gas|lng|refiner|petrochemical|"
    r"power|grid|solar|wind|nuclear|batter|ev\b|electric\s+vehicle|"
    r"factory|manufactur|industrial|freight|shipping|port|"
    r"semiconductor|chip|tariff|commodity"
    r")\b"
)


def assess_topic(title: str, content_snippet: str = "") -> TopicFilterResult:
    text = f"{title or ''}\n{(content_snippet or '')[:800]}"
    if _NON_INDUSTRY_RE.search(text) and not _INDUSTRY_EXCEPTION_RE.search(text):
        return TopicFilterResult(
            skip=True,
            reason="خارج از حوزه صنعت (ورزش، سرگرمی یا مشابه)",
        )
    return TopicFilterResult(skip=False)
