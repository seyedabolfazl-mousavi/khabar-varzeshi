"""Reject rewritten copy that leaks non-Persian scripts or breaks editorial rules."""

from __future__ import annotations

import re

# Chinese, Japanese, Korean, Cyrillic, Greek, Hebrew, Thai, Devanagari.
_FOREIGN_SCRIPT_RE = re.compile(
    "["
    "\u3040-\u30ff"
    "\u3400-\u9fff"
    "\uac00-\ud7af"
    "\u0400-\u04ff"
    "\u0370-\u03ff"
    "\u0590-\u05ff"
    "\u0e00-\u0e7f"
    "\u0900-\u097f"
    "\uff00-\uffef"
    "]"
)

_ACRONYM_RE = re.compile(
    r"\b(?:LNG|CBAM|ESG|PMI|OPEC|HSE|OEM|GDP|WTO|IEA|EIA|UN|EU|UK|US|AI|IoT|CO2|MW|GW|kWh|EV)\b",
    re.IGNORECASE,
)

_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
_ANY_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Passive Iran framing banned by the editorial desk.
_PASSIVE_IRAN_RE = re.compile(
    r"("
    r"اثر\s*مستقیم(?:ی)?[^۔.!?\n]{0,80}?"
    r"(?:دیده\s*نمی‌شود|وجود\s*ندارد|نیامده\s*است)"
    r"|ربطی\s*به\s*ایران\s*ندارد"
    r"|بر\s*ایران\s*اثر\s*ندارد"
    r"|ارتباطی\s*با\s*ایران\s*ندارد"
    r")"
)

_IRAN_SECTION_RE = re.compile(
    r"اهمیت\s*برای\s*ایران\s*</h2>\s*<p>(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)


def foreign_script_chars(text: str) -> list[str]:
    return _FOREIGN_SCRIPT_RE.findall(text or "")


def latin_letter_ratio(text: str) -> float:
    """Share of letters that are Latin, after dropping allowed acronyms."""
    cleaned = _ACRONYM_RE.sub(" ", text or "")
    letters = _ANY_LETTER_RE.findall(cleaned)
    if not letters:
        return 0.0
    latin = sum(1 for char in letters if _LATIN_LETTER_RE.fullmatch(char))
    return latin / len(letters)


def persian_problems(text: str, *, max_latin_ratio: float = 0.22) -> list[str]:
    """Return human-readable problems. Empty list means the text is acceptable."""
    problems: list[str] = []
    foreign = foreign_script_chars(text)
    if foreign:
        sample = "".join(dict.fromkeys(foreign))[:12]
        problems.append(f"نویسه بیگانه: {sample}")
    ratio = latin_letter_ratio(text)
    if ratio > max_latin_ratio:
        problems.append(f"متن بیش از حد لاتین است ({ratio:.0%})")
    return problems


def editorial_problems(site_body: str, *, joined_text: str = "") -> list[str]:
    """Catch passive Iran framing and a missing Iran section."""
    problems: list[str] = []
    body = site_body or ""
    haystack = "\n".join(part for part in (body, joined_text) if part)
    if "اهمیت برای ایران" not in body:
        problems.append("بخش اهمیت برای ایران نیست")
    else:
        match = _IRAN_SECTION_RE.search(body)
        if match is None or len(re.sub(r"\s+", "", match.group(1))) < 40:
            problems.append("تحلیل قیاسی اهمیت برای ایران ناقص است")
    if _PASSIVE_IRAN_RE.search(haystack):
        problems.append("جمله منفعل درباره ایران ممنوع است")
    return problems
