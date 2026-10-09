"""Bounded, read-only access to official GitHub sources (stdlib only)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}\Z")
PERIODS = {"daily": "today", "weekly": "this week", "monthly": "this month"}


class SourceError(RuntimeError):
    """A sanitized, actionable source failure."""


class FetchError(SourceError):
    """An outage or rate limit must not masquerade as an empty good run."""


def repo_name(value: str) -> str:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise SourceError("invalid repository name")
    if any(part in (".", "..") for part in value.split("/")):
        raise SourceError("invalid repository name")
    return value


def timestamp(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError
        return dt.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError):
        raise SourceError("invalid source timestamp") from None


def safe_url(url: str) -> bool:
    try:
        p = urllib.parse.urlsplit(url)
        return (p.scheme == "https" and p.port in (None, 443)
                and p.username is None and p.password is None
                and p.hostname in ("github.com", "api.github.com")
                and not p.fragment
                and (p.path == "/trending" if p.hostname == "github.com"
                     else p.path.startswith("/repos/")))
    except ValueError:
        return False


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (not safe_url(newurl)
                or urllib.parse.urlsplit(req.full_url).hostname
                != urllib.parse.urlsplit(newurl).hostname):
            raise SourceError("unsafe source redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubHTTP:
    """No credentials; public GETs only. Refuse arbitrary URLs and redirects."""

    def __init__(self, opener=None, sleep=time.sleep):
        self.opener = opener or urllib.request.build_opener(SafeRedirect())
        self.sleep = sleep

    def get(self, url: str) -> bytes:
        if not safe_url(url):
            raise SourceError("unsafe source URL")
        req = urllib.request.Request(url, headers={
            "User-Agent": "AI-Qianyan-GitHub-Series/1.0",
            "Accept": "application/vnd.github+json" if "api.github.com" in url
                      else "text/html",
            "X-GitHub-Api-Version": "2022-11-28",
        }, method="GET")
        for attempt in range(3):
            try:
                with self.opener.open(req, timeout=20) as response:
                    if not safe_url(response.geturl()):
                        raise SourceError("unsafe source response URL")
                    body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise SourceError("source response too large")
                return body
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    # Never sleep for an unbounded server-supplied Retry-After.
                    self.sleep(attempt + 1)
                    continue
                error = SourceError if exc.code in (404, 410) else FetchError
                raise error(f"GitHub HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise FetchError("GitHub network unavailable") from None
        raise SourceError("GitHub retries exhausted")

    def json(self, url: str) -> dict:
        try:
            value = json.loads(self.get(url).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise SourceError("invalid GitHub JSON") from None
        if not isinstance(value, dict):
            raise SourceError("invalid GitHub object")
        return value


@dataclass(frozen=True)
class Trend:
    name: str
    rank: int
    period_stars: int
    period: str
    observed_at: str
    source_url: str


class TrendingParser(HTMLParser):
    """Read Box-row articles, heading repo links, and window-specific stars."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.heading = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "article" and "Box-row" in a.get("class", "").split():
            self.row = {"name": None, "text": []}
        if self.row is not None:
            if tag == "h2":
                self.heading = True
            if tag == "a" and self.heading:
                href = a.get("href", "")
                if href.startswith("/") and NAME_RE.fullmatch(href[1:]):
                    self.row["name"] = href[1:]

    def handle_data(self, data):
        if self.row is not None:
            self.row["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "h2":
            self.heading = False
        if tag == "article" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
            self.heading = False


def parse_trending(html: str, period: str, observed_at: str) -> list[Trend]:
    if period not in PERIODS:
        raise SourceError("invalid Trending period")
    timestamp(observed_at)
    parser = TrendingParser()
    parser.feed(html)
    if not parser.rows or len(parser.rows) > 100:
        raise SourceError("Trending page structure changed or unavailable")
    trends, seen = [], set()
    for rank, row in enumerate(parser.rows, 1):
        text = " ".join(" ".join(row["text"]).split())
        stars = re.search(r"([0-9][0-9,]*)\s+stars?\s+" + PERIODS[period], text)
        if not row["name"] or not stars:
            # Fail closed on an unexpected page, rather than invent rankings.
            raise SourceError("Trending row missing repo or period stars")
        name = repo_name(row["name"])
        if name.lower() not in seen:
            trends.append(Trend(name, rank, int(stars[1].replace(",", "")),
                                period, observed_at,
                                "https://github.com/trending?since=" + period))
            seen.add(name.lower())
    return trends


@dataclass(frozen=True)
class Project:
    repo_id: int
    name: str
    url: str
    description: str
    language: str
    license: str
    total_stars: int
    pushed_at: str
    readme_url: str
    readme_sha256: str
    trend: Trend

    def evidence(self) -> dict:
        return asdict(self)


class GitHubSource:
    def __init__(self, http=None):
        self.http = http or GitHubHTTP()

    def trending(self, period: str, now: datetime) -> list[Trend]:
        if period not in PERIODS:
            raise SourceError("invalid Trending period")
        url = "https://github.com/trending?since=" + period
        try:
            html = self.http.get(url).decode("utf-8")
        except UnicodeError:
            raise SourceError("invalid Trending HTML") from None
        return parse_trending(html, period, now.isoformat())

    def project(self, trend: Trend, now: datetime) -> Project:
        base = "https://api.github.com/repos/" + repo_name(trend.name)
        meta = self.http.json(base)
        if any(meta.get(k) is not False for k in ("private", "archived", "disabled", "fork")):
            raise SourceError("repository is not an active public original")
        name = repo_name(meta.get("full_name"))
        rid, stars = meta.get("id"), meta.get("stargazers_count")
        if (type(rid) is not int or rid <= 0 or type(stars) is not int or stars < 0
                or meta.get("html_url") != "https://github.com/" + name):
            raise SourceError("invalid repository metadata")
        pushed = timestamp(meta.get("pushed_at"))
        if not timedelta(0) <= now - pushed <= timedelta(days=90):
            raise SourceError("repository activity outside 90-day window")
        description = meta.get("description")
        if not isinstance(description, str) or not 20 <= len(description.strip()) <= 2000:
            raise SourceError("repository lacks a usable official description")
        # Canonical ID survives case changes, renamed repos, and API redirects.
        base = "https://api.github.com/repos/" + name
        readme = self.http.json(base + "/readme")
        readme_url = readme.get("html_url", "")
        p = urllib.parse.urlsplit(readme_url)
        if (p.scheme != "https" or p.netloc != "github.com" or p.query or p.fragment
                or not p.path.startswith("/" + name + "/blob/")
                or readme.get("encoding") != "base64"):
            raise SourceError("unverified README source")
        try:
            content = readme["content"]
            if not isinstance(content, str) or len(content) > 200_000:
                raise ValueError
            raw = base64.b64decode("".join(content.split()), validate=True)
            text = raw.decode("utf-8")
            if not 20 <= len(raw) <= 131_072:
                raise ValueError
        except (KeyError, ValueError, UnicodeError):
            raise SourceError("README missing or unsupported") from None
        # Repository prose is untrusted data; never execute install commands.
        from .editorial import unsafe_text
        if unsafe_text(description) or unsafe_text(text):
            raise SourceError("source contains instruction injection or secret-like data")
        language = meta.get("language") or "unspecified"
        license_info = meta.get("license") or {}
        license_id = license_info.get("spdx_id") or "NOASSERTION"
        if not isinstance(language, str) or not isinstance(license_id, str):
            raise SourceError("invalid repository language or license")
        return Project(rid, name, meta["html_url"], description.strip(), language,
                       license_id, stars, meta["pushed_at"], readme_url,
                       hashlib.sha256(raw).hexdigest(), trend)
