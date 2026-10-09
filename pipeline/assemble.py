"""Pure extractive brief assembly for the repository-local editorial gates.

Original-language output must pass the Chinese-content gate before use.
Translator failures log only a fixed event, never source/exception text.
This module performs no network requests, writes or deployment.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Protocol

log = logging.getLogger("simp_publisher.assemble")

MAX_CHARS = 500
MAX_KEY_POINTS = 3

AI_DISCLOSURE_ASSEMBLED = (
    "［AI 生成内容标识］本摘要由程序自动摘编，未经人工撰写；"
    "事实以所列来源原文为准。"
)
REGIONAL_NOTE = "区域说明：中国大陆可用性以来源原文为准，本站不作额外断言。"


class Translator(Protocol):
    tier: str  # "mt" (translated) or "original" (source language kept)
    def translate(self, text: str, target: str = "zh-CN") -> str: ...


class NullTranslator:
    """Original-language tier: returns text unchanged."""

    tier = "original"

    def translate(self, text: str, target: str = "zh-CN") -> str:
        return text


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def assemble_brief(pack: Dict[str, Any], date: str,
                   translator: Optional[Translator] = None) -> Dict[str, Any]:
    """Assemble one brief from a fact-pack dict. Pure + deterministic."""
    tr = translator or NullTranslator()
    try:
        title_zh = _truncate(tr.translate(pack["title"]), 60)
        kps = [_truncate(tr.translate(kp), 120)
               for kp in pack["key_points"][:MAX_KEY_POINTS]]
        if not title_zh or not any(kps):
            raise RuntimeError("empty translation output")
        tier = tr.tier
    except Exception:  # fail-safe -> original-language tier
        log.warning("translation_failed")
        tier = "original"
        title_zh = _truncate(pack["title"], 60)
        kps = [_truncate(kp, 140) for kp in pack["key_points"][:MAX_KEY_POINTS]]

    title_label = "标题" if tier == "mt" else "原文标题"
    kp_label = "" if tier == "mt" else "（原文要点）"

    lines = [
        f"［AI 快讯］{title_zh}",
        "",
        f"{title_label}：{title_zh}{kp_label}",
    ]
    for kp in kps:
        lines.append(f"· {kp}{kp_label}")
    lines += [
        "",
        f"来源：{pack['source_name']}",
        f"原文链接：{pack['url']}",
        f"原文发布：{(pack.get('published_at') or '')[:10]}",
        "",
        AI_DISCLOSURE_ASSEMBLED,
        REGIONAL_NOTE,
    ]
    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        # drop trailing key points until within cap (disclosure preserved)
        while len(kps) > 1:
            kps.pop()
            lines = ([f"［AI 快讯］{title_zh}", "",
                      f"{title_label}：{title_zh}{kp_label}"]
                     + [f"· {kp}{kp_label}" for kp in kps]
                     + ["", f"来源：{pack['source_name']}",
                        f"原文链接：{pack['url']}",
                        f"原文发布：{(pack.get('published_at') or '')[:10]}",
                        "", AI_DISCLOSURE_ASSEMBLED, REGIONAL_NOTE])
            text = "\n".join(lines)
            if len(text) <= MAX_CHARS:
                break
    return {
        "text": text,
        "source": pack["source_name"],
        "url": pack["url"],
        "date": date,
        "tier": tier,
        "chars": len(text),
    }


CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
URL_RE = re.compile(r"https?://[^\s)）】》\"']+")
MIN_CJK_RATIO = 0.4


def cjk_ratio(text: str) -> float:
    # URLs are metadata, not editorial content: exclude them so genuine
    # Chinese tech articles (with long source URLs) are not penalized.
    text = URL_RE.sub(" ", text)
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if CJK_RE.match(c)) / len(chars)


def is_genuine_simplified_editorial(text: str,
                                    min_ratio: float = MIN_CJK_RATIO) -> bool:
    """Strict gate: editorial Chinese content must be predominantly CJK.

    English source paragraphs — even inside Chinese scaffolding — must NOT
    pass as Simplified Chinese editorial content. On failure the article is
    SKIPPED, never published as English-disguised-as-Chinese.
    """
    return cjk_ratio(text) >= min_ratio


def brief_fact_sentences(brief_text: str) -> List[str]:
    """Factual sentences = non-template lines (for the extractiveness test)."""
    template_markers = ("［AI 快讯］", "来源：", "原文链接：", "原文发布：",
                        "［AI 生成内容标识］", "区域说明：")
    out = []
    for line in brief_text.split("\n"):
        s = line.strip().lstrip("· ").strip()
        if not s or s.startswith(template_markers):
            continue
        # title line "标题：..." / "原文标题：..." — the part after ： is a fact
        if "：" in s and s.split("：", 1)[0] in ("标题", "原文标题"):
            s = s.split("：", 1)[1].rstrip("（原文要点）")
        out.append(s)
    return out
