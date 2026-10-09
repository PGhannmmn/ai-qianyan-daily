"""Grounded short introductions. English quotations remain explicit.

Chinese-only mode requires a supplied translator and human review. Built-in
bilingual mode quotes the official description inside Chinese editorial framing.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|"
    r"(?:reveal|print|exfiltrate)\s+(?:all\s+)?(?:secrets?|tokens?|credentials)|"
    r"<\|(?:system|assistant|im_start)\|>|"
    r"(?:忽略|无视|無視).{0,12}(?:指令|提示)|"
    r"(?:泄露|洩露|输出|輸出).{0,8}(?:密钥|密鑰|令牌)", re.I)
SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                    r"sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
URL = re.compile(r"https?://[^\s]+")
LABELS = {
    "zh-CN": ("[GitHub 热门项目]", "上榜", "日榜", "周榜", "月榜", "期间新增 Star",
              "官方简介", "语言", "许可证", "未明确", "来源", "原文摘录；发布前需人工审核。"),
    "zh-TW": ("[GitHub 熱門專案]", "上榜", "日榜", "週榜", "月榜", "期間新增 Star",
              "官方簡介", "語言", "授權", "未明確", "來源", "原文摘錄；發佈前需人工審核。"),
}


def unsafe_text(text: str) -> bool:
    return bool(INJECTION.search(text) or SECRET.search(text) or CONTROL.search(text))


def excerpt(text: str, limit: int) -> str:
    """A source prefix, never a fabricated fact or missing attribution."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    if limit < 30:
        raise ValueError("insufficient room for a meaningful introduction")
    cut = text[:limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def make_draft(project, now: datetime, locale="zh-CN", style="bilingual", translator=None) -> dict:
    from .sources import SourceError, timestamp
    if locale not in LABELS or style not in ("bilingual", "chinese"):
        raise ValueError("unsupported editorial style")
    observed = timestamp(project.trend.observed_at)
    if not timedelta(0) <= now - observed <= timedelta(hours=6):
        raise SourceError("Trending snapshot is stale or from the future")
    if project.trend.period_stars <= 0:
        raise SourceError("no positive Trending growth")
    l = LABELS[locale]
    period = {"daily": l[2], "weekly": l[3], "monthly": l[4]}[project.trend.period]
    license_id = project.license if project.license != "NOASSERTION" else l[9]
    info = f"{l[7]}: {project.language} | {l[8]}: {license_id}"
    header = f"{l[0]} {project.name}"
    try:
        local_date = str(observed.astimezone(ZoneInfo("America/Toronto")).date()) + " Toronto"
    except ZoneInfoNotFoundError:
        # Minimal Windows installations may lack tzdata; label UTC explicitly.
        local_date = str(observed.date()) + " UTC"
    signal = (f"{local_date} {l[1]} GitHub Trending {period} #{project.trend.rank}"
              f" · {l[5]} +{project.trend.period_stars:,}")
    footer = f"{l[10]}: {project.url}\n{l[11]}"
    evidence = project.description
    tier = "official-description-excerpt"
    if style == "chinese":
        if translator is None:
            raise SourceError("Chinese-only style requires a verified translator")
        # A translator receives evidence only, never environment or credentials.
        evidence = translator(project.description, locale)
        if not isinstance(evidence, str) or not evidence.strip() or unsafe_text(evidence):
            raise SourceError("translation failed quality checks")
        if URL.search(evidence):
            raise SourceError("translation introduced a link")
        chars = [c for c in evidence if not c.isspace()]
        if sum(bool(re.match(r"[\u3400-\u9fff]", c)) for c in chars) / len(chars) < .4:
            raise SourceError("translation lacks Chinese editorial content")
        if set(re.findall(r"\d+(?:\.\d+)?", evidence)) - set(re.findall(r"\d+(?:\.\d+)?", project.description)):
            raise SourceError("translation introduced unsupported numbers")
        tier = "translation-needs-human-review"
        disclosure = "[AI] 翻译草稿；需人工核对原文。" if locale == "zh-CN" else "[AI] 翻譯草稿；需人工核對原文。"
        footer = footer.rsplit("\n", 1)[0] + "\n" + disclosure
    prefix = f"{header}\n{signal}\n{l[6]}: 「"
    suffix = f"」\n{info}\n{footer}"
    # UTF-16 is conservative for Threads' length cap, including emoji.
    room = 500 - units(prefix + suffix)
    quote = excerpt(evidence, room)
    while units(quote) > room:
        quote = excerpt(evidence, len(quote) - 1)
    text = prefix + quote + suffix
    if unsafe_text(text) or units(text) > 500:
        raise SourceError("draft failed length or content quality checks")
    if style == "bilingual" and URL.search(quote):
        raise SourceError("official description contains an embedded link")
    facts = project.evidence()
    return {
        "schema_version": 1, "series": "github_trending", "repo_id": project.repo_id,
        "repo_name": project.name, "canonical_url": project.url,
        "dedup_key": f"github:repo:{project.repo_id}", "locale": locale,
        "style": style, "tier": tier, "text": text, "chars_utf16": units(text),
        "status": "needs_review", "publication_allowed": False,
        "observed_at": project.trend.observed_at, "evidence": facts,
        "source_links": [project.url, project.readme_url, project.trend.source_url],
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "evidence_sha256": hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
