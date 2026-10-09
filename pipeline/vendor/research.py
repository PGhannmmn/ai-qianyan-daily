"""Bounded public RSS/Atom research with no state writes or credentials."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
import re
import urllib.request
import xml.etree.ElementTree as ET

from .safety import MAX_ARTICLE, MAX_POINT, MAX_POINTS, SafetyError, checked_text, source_url

MAX_FEED_BYTES = 512 * 1024
MAX_ARTICLE_BYTES = 1024 * 1024
MAX_ITEMS = 50
TIMEOUT = 20


class ResearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedSource:
    name: str
    kind: str
    url: str


# Preserve upstream discovery configuration without expanding the orchestrator's
# two default feeds. "media" is the validated equivalent of upstream "press".
DEFAULT_FEEDS = (
    FeedSource(name="Google Blog", kind="official", url="https://blog.google/rss/"),
    FeedSource(name="TechCrunch AI", kind="media", url="https://techcrunch.com/category/artificial-intelligence/feed/"),
    FeedSource(name="MIT Technology Review", kind="media", url="https://www.technologyreview.com/feed/"),
    FeedSource(name="The Verge", kind="media", url="https://www.theverge.com/rss/index.xml"),
)


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str
    summary: str
    published: datetime | None
    source_name: str
    source_kind: str = "official"


@dataclass(frozen=True)
class FactPack:
    title: str
    url: str
    source_name: str
    published_at: str
    key_points: list[str]
    article_text: str
    article_sha256: str
    source_kind: str = "official"


class TrustedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = source_url(newurl)
        except SafetyError:
            raise ResearchError("redirect_rejected") from None
        return super().redirect_request(req, fp, code, msg, headers, target)


def _fetch_bytes(url: str, limit: int, *, _opener=None) -> bytes:
    try:
        url = source_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Qianyan-Daily/1.0"})
        opener = _opener or urllib.request.build_opener(TrustedRedirects())
        with opener.open(req, timeout=TIMEOUT) as resp:
            source_url(resp.geturl())
            raw = resp.read(limit + 1)
            if len(raw) > limit:
                raise ResearchError("response_too_large")
        return raw
    except ResearchError:
        raise
    except Exception:
        raise ResearchError("fetch_failed") from None


class _Text(HTMLParser):
    HIDDEN = {"script", "style", "noscript", "nav", "header", "footer"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "br", "article", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
        elif not self.hidden and tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(raw: str) -> str:
    parser = _Text()
    parser.feed(raw)
    return "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines()
                     if line.strip())


def parse_date(value: str) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    if date.tzinfo is None:
        return None
    return date.astimezone(timezone.utc)


def within_window(published: datetime | None, hours: int = 48, *, now=None) -> bool:
    if not isinstance(published, datetime) or published.tzinfo is None:
        return False
    age = (now or datetime.now(timezone.utc)) - published
    return -timedelta(minutes=5) <= age <= timedelta(hours=hours)


AI_RE = re.compile(
    r"\b(?:AI|artificial intelligence|Gemini|DeepMind|LLM|large language model|"
    r"machine learning|OpenAI|Anthropic|ChatGPT|GPT|neural|diffusion|transformer|"
    r"agent(?:ic)?|open[- ]?source model|foundation model|frontier model)\b|"
    r"人工智能|大模型|智能体", re.I)


def is_ai_related(title: str, summary: str) -> bool:
    return bool(AI_RE.search(title + " " + summary))


def parse_feed(raw: bytes, source: FeedSource) -> list[FeedItem]:
    if not isinstance(raw, bytes) or len(raw) > MAX_FEED_BYTES:
        raise ResearchError("invalid_feed")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise ResearchError("xml_entities_rejected")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ResearchError("invalid_feed") from None
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed"}:
        raise ResearchError("invalid_feed")
    items = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        fields = {}
        for child in node:
            key = child.tag.rsplit("}", 1)[-1]
            value = "".join(child.itertext())
            if key == "link" and child.get("href"):
                if child.get("rel", "alternate") != "alternate":
                    continue
                value = child.get("href")
            fields.setdefault(key, value)
        try:
            title = checked_text(html_text(fields.get("title", "")), 180)
            url = source_url(fields.get("link", ""))
            summary = html_text(fields.get("description", fields.get("summary", "")))
            if summary:
                checked_text(summary, MAX_ARTICLE)
            items.append(FeedItem(title, url, summary,
                                  parse_date(fields.get("pubDate", fields.get("published", fields.get("updated", "")))),
                                  source.name, source.kind))
        except SafetyError:
            continue
        if len(items) >= MAX_ITEMS:
            break
    return items


def fetch_rss(source: FeedSource) -> list[FeedItem]:
    return parse_feed(_fetch_bytes(source.url, MAX_FEED_BYTES), source)


def fetch_article_text(url: str) -> str:
    try:
        text = html_text(_fetch_bytes(url, MAX_ARTICLE_BYTES).decode("utf-8"))
        return checked_text(text, MAX_ARTICLE)
    except ResearchError:
        raise  # Preserve transport failure classification for the orchestrator.
    except (SafetyError, UnicodeError):
        raise ResearchError("article_rejected") from None
    except Exception:
        raise ResearchError("article_processing_failed") from None


def build_fact_pack(item: FeedItem, article_text: str) -> FactPack:
    article_text = checked_text(article_text, MAX_ARTICLE)
    title = checked_text(item.title, 180)
    checked_text(item.source_name, 80)
    if item.summary:
        checked_text(item.summary, MAX_ARTICLE)
    if not isinstance(item.published, datetime) or item.published.tzinfo is None:
        raise ResearchError("missing_publication_date")
    # Exact source sentences only; never substitute the RSS teaser as evidence.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+|\n+", article_text)
                 if 15 <= len(s.strip()) <= MAX_POINT]
    related = [s for s in sentences if AI_RE.search(s)]
    points = list(dict.fromkeys(related or sentences))[:MAX_POINTS]
    if not points:
        raise ResearchError("missing_source_evidence")
    return FactPack(title, source_url(item.link), item.source_name,
                    item.published.astimezone(timezone.utc).isoformat(), points,
                    article_text, sha256(article_text.encode("utf-8")).hexdigest(), item.source_kind)
