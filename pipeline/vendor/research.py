"""RSS-based AI news research.

Security posture: everything fetched here is UNTRUSTED external content.
It is treated strictly as data — never executed, never followed as
instructions. Articles are size-capped, HTML is stripped to text, and every
item is scanned for prompt-injection patterns before it can become a
fact-pack.
"""
from __future__ import annotations

import html
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import List, Optional

log = logging.getLogger("threads_publisher.news.research")

FEED_TIMEOUT = 20
ARTICLE_TIMEOUT = 25
MAX_FEED_BYTES = 512 * 1024
MAX_ARTICLE_BYTES = 1024 * 1024
MAX_ARTICLE_CHARS = 6000  # extracted text kept for fact-packs


@dataclass
class FeedSource:
    name: str
    url: str
    kind: str  # "official" (company newsroom) or "press" (reputable tech press)


# Curated feed list. Official newsrooms are preferred; reputable tech press
# is accepted as a discovery layer, but every claim must still trace to the
# linked article, and ideally to the primary source it cites.
# Verified working 2026-10-08. Official newsroom RSS is rare (DeepMind and
# Anthropic publish none; OpenAI's exceeds sane size caps), so reputable tech
# press serves as the discovery layer — every claim must still trace to the
# fetched article, and editorial prefers stories that cite a primary source.
DEFAULT_FEEDS: List[FeedSource] = [
    FeedSource("Google Blog", "https://blog.google/rss/", "official"),
    FeedSource("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "press"),
    FeedSource("MIT Technology Review", "https://www.technologyreview.com/feed/", "press"),
    FeedSource("The Verge", "https://www.theverge.com/rss/index.xml", "press"),
]


@dataclass
class NewsItem:
    title: str
    link: str
    published: Optional[datetime]
    summary: str
    feed_name: str
    feed_kind: str


# ------------------------------------------------------------------ fetching
def _http_get(url: str, timeout: int, max_bytes: int) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "OrbisignalMedia-NewsResearch/1.0 (+https://www.threads.com/@orbisignalmedia)",
        "Accept": "application/rss+xml, application/xml, text/xml, text/html",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        chunks, total = [], 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"response exceeded {max_bytes} bytes: {url}")
            chunks.append(chunk)
        return b"".join(chunks)


def parse_pubdate(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        # RFC 2822, e.g. "Thu, 08 Oct 2026 12:00:00 +0000"
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    try:
        # ISO 8601
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _text_of(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def fetch_rss(source: FeedSource) -> List[NewsItem]:
    """Fetch one RSS/Atom feed; return items (failures -> empty list, logged)."""
    try:
        raw = _http_get(source.url, FEED_TIMEOUT, MAX_FEED_BYTES)
    except Exception as exc:
        log.warning("feed fetch failed [%s]: %s", source.name, exc)
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        log.warning("feed parse failed [%s]: %s", source.name, exc)
        return []

    items: List[NewsItem] = []
    # RSS 2.0
    for it in root.iter("item"):
        title = _text_of(it.find("title"))
        link = _text_of(it.find("link"))
        published = parse_pubdate(_text_of(it.find("pubDate")))
        summary = _text_of(it.find("description"))[:500]
        if title and link:
            items.append(NewsItem(title, link, published, summary,
                                  source.name, source.kind))
    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        title = _text_of(entry.find("a:title", ns))
        link_el = entry.find("a:link", ns)
        link = (link_el.get("href") or "") if link_el is not None else ""
        published = parse_pubdate(_text_of(entry.find("a:published", ns)) or
                                  _text_of(entry.find("a:updated", ns)))
        summary = _text_of(entry.find("a:summary", ns))[:500]
        if title and link:
            items.append(NewsItem(title, link, published, summary,
                                  source.name, source.kind))
    log.info("feed [%s]: %d items", source.name, len(items))
    return items


class _TextExtractor(HTMLParser):
    """Minimal HTML -> text. Drops scripts/styles; keeps paragraph-ish text."""

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "nav", "header", "footer"):
            self._skip += 1
        elif tag in ("p", "br", "h1", "h2", "h3", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "nav", "header", "footer"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip == 0:
            text = data.strip()
            if text:
                self.parts.append(text + " ")


def html_to_text(raw_html: bytes) -> str:
    try:
        text = raw_html.decode("utf-8", errors="replace")
    except Exception:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(text)
    except Exception:
        pass
    cleaned = html.unescape(" ".join(parser.parts))
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
    return cleaned.strip()


def fetch_article_text(url: str) -> str:
    """Fetch an article page and extract plain text (capped)."""
    raw = _http_get(url, ARTICLE_TIMEOUT, MAX_ARTICLE_BYTES)
    return html_to_text(raw)[:MAX_ARTICLE_CHARS]


# ------------------------------------------------------- prompt-injection scan
INJECTION_PATTERNS = [
    (r"ignore\s+(all\s+)?previous\s+instructions", "ignore-previous-instructions"),
    (r"disregard\s+(all\s+)?(prior|previous)\s+instructions", "disregard-instructions"),
    (r"system\s*prompt", "system-prompt mention"),
    (r"you\s+are\s+now\s+(a|an)\s+", "you-are-now roleplay"),
    (r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions)", "prompt-extraction"),
    (r"do\s+not\s+follow\s+(your|the)\s+guidelines", "guideline-subversion"),
    (r"忽略(之前|以前|所有)的?(指示|指令|提示)", "chinese ignore-instructions"),
    (r"你(現在)?是(一個|一名|個)?(新的?)?(AI|助手|助理|系統)", "chinese roleplay"),
    (r"(jailbreak|越獄)\s", "jailbreak mention"),
]


def scan_for_injection(text: str) -> List[str]:
    """Return descriptions of matched injection patterns (empty = clean)."""
    hits = []
    lowered = text.lower()
    for pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            hits.append(label)
    return hits


# ------------------------------------------------------------------ filtering
AI_KEYWORDS = re.compile(
    r"\b(ai\b|artificial intelligence|llm|large language model|gpt|"
    r"chatgpt|Muse|gemini|deepmind|openai|anthropic|machine learning|"
    r"neural|diffusion|transformer|agent(ic)?\b|open-?source model|"
    r"foundation model|frontier model)",
    re.IGNORECASE)


def is_ai_related(title: str, summary: str = "") -> bool:
    return bool(AI_KEYWORDS.search(title) or AI_KEYWORDS.search(summary))


def within_window(published: Optional[datetime], hours: int = 48,
                  now: Optional[datetime] = None) -> bool:
    if published is None:
        return False  # undated items are rejected: date checking is mandatory
    now = now or datetime.now(timezone.utc)
    age = now - published
    return timedelta(0) <= age <= timedelta(hours=hours)


@dataclass
class FactPack:
    """Verified, dated, injection-scanned source material for one story.

    NOTE: all free-text fields are UNTRUSTED external content. The editorial
    step must treat them as data only: relay verified claims, never follow
    instructions that may appear in them.
    """
    title: str
    source_name: str
    source_kind: str
    url: str
    published_at: Optional[str]  # ISO
    retrieved_at: str            # ISO
    key_points: List[str] = field(default_factory=list)  # verbatim excerpts
    injection_hits: List[str] = field(default_factory=list)
    article_chars: int = 0


def build_fact_pack(item: NewsItem, article_text: str) -> FactPack:
    # Key points: first few substantive sentences, verbatim (anti-hallucination:
    # the editorial draft may only relay what appears here).
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", article_text) if s.strip()]
    key_points = [s for s in sentences if len(s) > 40][:6]
    return FactPack(
        title=item.title,
        source_name=item.feed_name,
        source_kind=item.feed_kind,
        url=item.link,
        published_at=item.published.isoformat() if item.published else None,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        key_points=key_points,
        injection_hits=scan_for_injection(item.title + "\n" + article_text),
        article_chars=len(article_text),
    )
