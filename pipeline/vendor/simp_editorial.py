#!/usr/bin/env python3
"""Vendored standalone Simplified-Chinese editorial QA for GitHub Actions.

No workspace packages required (stdlib only). Assembled from:
  - threads_publisher/news/editorial.py : qa_fact_pack, _anchor_tokens,
    claim_overlap_ratio, STOPWORDS, FRESHNESS_HOURS
  - threads_publisher/news/research.py  : parse_pubdate, scan_for_injection
    (vendored separately as vendor/research.py)
  - simp_publisher/editorial.py         : qa_draft_simp, TRADITIONAL_ONLY_CHARS,
    REGIONAL_NOTE_MARKERS, URL_RE, MAX_CHARS

Public surface (mirrors simp_publisher.editorial):
  - qa_fact_pack(pack) -> List[str]
  - qa_draft_simp(text, source, pack) -> List[str]

Keep in sync with the sources above when gates change.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List

try:
    from vendor.research import (FactPack, parse_pubdate,  # noqa: E402
                                 scan_for_injection)
except ImportError:  # vendor/ directly on sys.path (daily.py inserts it)
    from research import (FactPack, parse_pubdate,  # noqa: E402
                          scan_for_injection)

log = logging.getLogger("vendor.simp_editorial")

FRESHNESS_HOURS = 48
MIN_OVERLAP = 0.6  # fraction of draft's entity anchors found in source text
MAX_CHARS = 500    # Simplified-Chinese draft length cap

# Common words excluded from the overlap check (English + a small Chinese set).
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "from", "by", "at", "is", "are", "was", "were", "be", "been", "has",
    "have", "had", "will", "would", "can", "could", "should", "may", "might",
    "this", "that", "these", "those", "its", "their", "they", "them", "he",
    "she", "it", "we", "you", "i", "as", "not", "but", "if", "than", "then",
    "so", "such", "into", "over", "after", "before", "between", "new",
    "more", "most", "one", "two", "also", "said", "says", "according",
    "的", "了", "在", "是", "和", "與", "及", "或", "等", "將", "已", "有",
    "為", "對", "就", "而", "及", "其", "這", "那", "一個",
}

# Characters that exist only in Traditional Chinese. Their presence proves
# the draft is not genuine Simplified Chinese. (Shared/common characters
# such as 的是在不了 are intentionally absent from this list.)
TRADITIONAL_ONLY_CHARS = set(
    "們這個發點學麼來過還對讓認識機會產業務際係開關門問說話讀語傳統灣擇標籤"
    "麗龍龜鳳麵頭髮裡觀親鐘愛樂醫禮體藝劇時後"
)

# Phrases that count as an explicit regional-availability note for Mainland
# readers. The writing agent MUST include at least one when the story
# involves a foreign service or region-limited rollout.
REGIONAL_NOTE_MARKERS = [
    "中国大陆", "国内暂未", "暂未开放", "英文版先行", "英文版",
    "仅限", "美国", "海外", "需要注意", "官方渠道",
    "可用性", "限制",
]

URL_RE = re.compile(r"https?://[^\s)）】》\"']+")


def qa_fact_pack(pack: FactPack, freshness_hours: int = FRESHNESS_HOURS,
                 now: datetime | None = None) -> List[str]:
    """Return a list of blocking issues (empty = passes)."""
    issues: List[str] = []
    if not pack.url or not pack.url.startswith("http"):
        issues.append("missing or invalid source URL")
    if not pack.source_name:
        issues.append("missing source name")
    if not pack.key_points:
        issues.append("no verbatim key points extracted (article unreadable?)")
    if pack.injection_hits:
        issues.append(f"prompt-injection patterns in source: {pack.injection_hits}")
    published = parse_pubdate(pack.published_at or "")
    if published is None:
        issues.append("story date unknown — date checking is mandatory")
    else:
        now = now or datetime.now(timezone.utc)
        age_hours = (now - published).total_seconds() / 3600
        if age_hours < 0:
            issues.append("story date is in the future")
        elif age_hours > freshness_hours:
            issues.append(f"story is {age_hours:.1f}h old (limit {freshness_hours}h)")
    if pack.article_chars < 200:
        issues.append("article text too short to verify claims")
    return issues


def _anchor_tokens(text: str) -> List[str]:
    """Entity anchors that must survive translation verbatim.

    For Chinese drafts written from English sources, the reliable
    cross-lingual anchors are ASCII entity names (Google, Gemini, SynthID…).
    Chinese paraphrase is expected and must NOT count against the draft;
    numbers are excluded because unit conversions (billion -> 億) break
    naive matching — the agent guarantees numeric fidelity when writing.
    """
    tokens: List[str] = []
    for tok in re.findall(r"[A-Za-z]{4,}", text):
        low = tok.lower()
        if low not in STOPWORDS:
            tokens.append(low)
    # de-dupe, keep order
    seen, out = set(), []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def claim_overlap_ratio(draft_text: str, source_text: str) -> float:
    """Fraction of the draft's entity anchors also present in the source.

    Heuristic backstop against hallucination: a faithful rewrite must keep
    entity names verbatim. With fewer than 2 anchors there is no signal, so
    the check passes and the writing agent remains responsible.
    """
    tokens = _anchor_tokens(draft_text)
    if len(tokens) < 2:
        return 1.0
    src = source_text.lower()
    hits = sum(1 for tok in tokens if tok in src)
    return hits / len(tokens)


def qa_draft_simp(text: str, source: str, pack: FactPack) -> List[str]:
    """QA a Simplified Chinese draft against its fact-pack. Blocking issues."""
    issues: List[str] = []
    if not text or not text.strip():
        issues.append("draft is empty")
        return issues
    if len(text) > MAX_CHARS:
        issues.append(f"draft too long ({len(text)} > {MAX_CHARS} chars)")
    # source attribution names the verified source (or its domain)
    if pack.source_name.lower() not in source.lower():
        domain = re.sub(r"^https?://(www\.)?", "", pack.url).split("/")[0]
        if not domain or domain.lower() not in source.lower():
            issues.append("draft attribution does not name the verified source")
    # real working URL must be in the draft text
    urls = URL_RE.findall(text)
    if not urls:
        issues.append("draft must contain the real working source URL")
    elif not any(pack.url.split("?")[0] in u or u in pack.url for u in urls):
        # allow URL variants (tracking params stripped) but require same page
        base = re.sub(r"^https?://(www\.)?", "", pack.url).split("?")[0]
        if not any(base in re.sub(r"^https?://(www\.)?", "", u) for u in urls):
            issues.append("draft URL does not match the verified source URL")
    # genuine Simplified Chinese
    trad_hits = sorted({c for c in text if c in TRADITIONAL_ONLY_CHARS})
    if trad_hits:
        issues.append(f"contains Traditional-only characters: {''.join(trad_hits[:8])}")
    # anti-hallucination (shared anchor logic); URLs are verified
    # separately above, so exclude them from the anchor set
    source_text = pack.title + "\n" + "\n".join(pack.key_points)
    ratio = claim_overlap_ratio(URL_RE.sub(" ", text), source_text)
    log.info("simp claim overlap: %.2f (threshold %.2f)", ratio, MIN_OVERLAP)
    if ratio < MIN_OVERLAP:
        issues.append(f"low claim overlap {ratio:.2f} < {MIN_OVERLAP} — "
                      "draft may contain unverified claims; needs review")
    # injection scan on the draft itself (shared patterns)
    hits = scan_for_injection(text)
    if hits:
        issues.append(f"prompt-injection patterns in draft: {hits}")
    # regional-availability honesty for Mainland readers
    if not any(m in text for m in REGIONAL_NOTE_MARKERS):
        issues.append("missing regional-availability note for Mainland readers "
                      f"(include one of: {', '.join(REGIONAL_NOTE_MARKERS[:6])}…)")
    return issues
