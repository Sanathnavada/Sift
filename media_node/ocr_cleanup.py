"""Deterministic OCR text cleanup helpers.

The OCR model is good at reading words, but social-media graphics often come
back as one long all-caps string with words glued together.  This module keeps
cleanup local, predictable, and non-LLM based.
"""
from __future__ import annotations

import re
import textwrap
from functools import lru_cache

# Focused rules for common social/image OCR glue patterns.  Keep these rules
# conservative: they only add spacing/line breaks and fix very common OCR joins.
_PHRASE_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in [
        (r"\bSWIPETOWATCH\b", "SWIPE TO WATCH"),
        (r"\bSWIPE\s*TO\s*WATCH\b", "SWIPE TO WATCH"),
        (r"\bTAPTO(?:READ|WATCH|SEE)\b", "TAP TO WATCH"),
        (r"\bREADMORE\b", "READ MORE"),
        (r"\bWATCHNOW\b", "WATCH NOW"),
        (r"\bLINKINBIO\b", "LINK IN BIO"),
        (r"\bBEFOREIN\b", "BEFORE IN"),
        (r"\bHASNEVER\b", "HAS NEVER"),
        (r"\bHAVENEVER\b", "HAVE NEVER"),
        (r"\bTHATHAS\b", "THAT HAS"),
        (r"\bTHATHAVE\b", "THAT HAVE"),
        (r"\bPREDICTSSOMETHING\b", "PREDICTS SOMETHING"),
        (r"\bSAYSSOMETHING\b", "SAYS SOMETHING"),
        (r"\bSHOWSSOMETHING\b", "SHOWS SOMETHING"),
        (r"\bECONOMICHISTORY\b", "ECONOMIC HISTORY"),
        (r"\bEVOLVINGAI\b", "EVOLVING AI"),
        (r"\bANTHROPICEVOLVING\b", "ANTHROPIC EVOLVING"),
        (r"\bOPENAI\b", "OPENAI"),
        # Common OCR confusion around a small "AI" label before a proper name.
        (r"\bA[J1I]DARIO\b", "AI DARIO"),
    ]
)

# Small built-in vocabulary for deterministic segmentation of long uppercase
# glued OCR tokens.  It intentionally favors common words in headlines, social
# cards, AI/business/tech posts, and CTA text.  Unknown words are left alone.
_SEGMENT_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "before", "between",
    "big", "by", "can", "case", "change", "changes", "company", "could",
    "daily", "data", "day", "did", "do", "does", "economic", "economy",
    "evolving", "first", "for", "from", "future", "has", "have", "history",
    "how", "if", "in", "into", "is", "it", "its", "make", "market", "may",
    "more", "never", "new", "next", "not", "now", "of", "on", "one", "only",
    "or", "our", "over", "post", "predicts", "read", "reel", "said", "says",
    "see", "shows", "something", "story", "swipe", "that", "the", "their",
    "this", "to", "today", "top", "up", "watch", "what", "when", "why", "will",
    "with", "world", "you", "your",
    # Tech / AI / finance / business terms commonly seen in posts.
    "ai", "anthropic", "artificial", "automation", "business", "chatgpt",
    "claude", "crypto", "dario", "deepmind", "finance", "gdp", "google",
    "inflation", "intelligence", "llm", "meta", "microsoft", "model", "models",
    "openai", "rate", "rates", "recession", "stock", "stocks", "tech",
    "technology", "tesla", "trade", "trump", "video",
}

# Prefer longer words during segmentation so THAT+HAS is chosen over tiny chunks.
_WORDS_BY_LENGTH = sorted(_SEGMENT_WORDS, key=len, reverse=True)


def _is_mostly_upper_text(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 12:
        return False
    uppercase = sum(1 for ch in letters if ch.upper() == ch)
    return uppercase / len(letters) >= 0.72


def _apply_phrase_rules(text: str) -> str:
    cleaned = text
    for pattern, replacement in _PHRASE_RULES:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


@lru_cache(maxsize=2048)
def _segment_upper_token(token: str) -> str:
    """Segment one long uppercase token when the segmentation is confident.

    Returns the original token if too much of it would remain unknown.
    """
    raw = token.strip()
    if len(raw) < 10 or not raw.isalpha() or not raw.upper() == raw:
        return raw

    lower = raw.lower()
    n = len(lower)
    # dp[i] = (score, parts, unknown_chars)
    dp: list[tuple[int, list[str], int] | None] = [None] * (n + 1)
    dp[0] = (0, [], 0)

    for i in range(n):
        state = dp[i]
        if state is None:
            continue
        score, parts, unknown_chars = state

        for word in _WORDS_BY_LENGTH:
            if lower.startswith(word, i):
                next_i = i + len(word)
                # Reward longer known words; penalize too many tiny words.
                word_score = score + 8 + len(word) * 2
                if len(word) <= 2:
                    word_score -= 2
                candidate = (word_score, parts + [word.upper()], unknown_chars)
                if dp[next_i] is None or candidate[0] > dp[next_i][0]:
                    dp[next_i] = candidate

        # Unknown character fallback so the DP can continue, but it is expensive.
        next_i = i + 1
        candidate = (score - 7, parts + [raw[i]], unknown_chars + 1)
        if dp[next_i] is None or candidate[0] > dp[next_i][0]:
            dp[next_i] = candidate

    final = dp[n]
    if final is None:
        return raw
    score, parts, unknown_chars = final
    known_chars = n - unknown_chars
    known_ratio = known_chars / n

    # Avoid damaging names, codes, or already-meaningful text.
    if known_ratio < 0.78 or len(parts) < 2 or score <= 0:
        return raw

    # Rejoin consecutive unknown single letters so names/codes are not exploded.
    merged: list[str] = []
    buffer = ""
    for part in parts:
        if len(part) == 1 and part.lower() not in _SEGMENT_WORDS:
            buffer += part
            continue
        if buffer:
            merged.append(buffer)
            buffer = ""
        merged.append(part)
    if buffer:
        merged.append(buffer)

    return " ".join(merged)


def _segment_glued_uppercase_words(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return _segment_upper_token(match.group(0))

    return re.sub(r"\b[A-Z]{10,}\b", replace, text)


def _normalize_spacing(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", cleaned)
    return cleaned.strip()


def _format_uppercase_card_text(text: str, width: int = 34) -> str:
    """Make all-caps social card OCR readable without changing meaning."""
    if not _is_mostly_upper_text(text):
        return text

    # Keep CTAs readable and separate when they appear at the tail of a graphic.
    text = re.sub(r"\s+(SWIPE TO WATCH|WATCH NOW|READ MORE|LINK IN BIO)\b", r"\n\1", text, flags=re.IGNORECASE)

    formatted_lines: list[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= width:
            formatted_lines.append(paragraph)
            continue
        formatted_lines.extend(
            textwrap.wrap(
                paragraph,
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(formatted_lines).strip()


def clean_ocr_text(text: str) -> str:
    """Return deterministic, display-ready OCR text.

    This function does not summarize, rewrite, or call an LLM.  It only fixes
    common OCR spacing/line-break issues so combined.txt is easier to read.
    """
    if not text:
        return ""

    cleaned = _normalize_spacing(str(text))
    if not cleaned:
        return ""

    cleaned = _apply_phrase_rules(cleaned)
    cleaned = _segment_glued_uppercase_words(cleaned)
    cleaned = _normalize_spacing(cleaned)
    cleaned = _format_uppercase_card_text(cleaned)
    return _normalize_spacing(cleaned)
