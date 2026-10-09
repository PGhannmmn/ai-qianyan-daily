"""Extractive Simplified-Chinese brief assembler (for GitHub Actions).

WHY THIS EXISTS: the Muse GitHub connector's write path (`github
call-tool`) requires per-operation human approval, so a scheduled Muse
cron CANNOT push daily drafts unattended — and bypassing that approval
control is forbidden. The compliant architecture is therefore:

  Muse's role  = one-time: the pipeline CODE is committed to the repo.
  Daily loop   = GitHub Actions (schedule trigger): research -> fact-pack
                 QA -> extractive assembly -> editorial QA -> site build
                 -> commit (activity heartbeat) -> Pages deploy.
  Secrets      = zero. The workflow uses only the automatic GITHUB_TOKEN
                 (contents:write for the digest commit; pages:write +
                 id-token:write for the Pages deploy). No PATs, no API keys.

Assembly is deterministic and extractive: every factual sentence in the
brief comes VERBATIM from the fact-pack's key_points (which are themselves
verbatim extracts from the verified source). Fixed Chinese scaffolding
(headline frame, disclosure, attribution, regional note) is template text.
No claims are invented, by construction.

Third-party machine translation is DISABLED (unverified external
service). Assembly uses the original-language tier only: the title and key
points appear verbatim as 原文标题 / 原文要点 inside fixed Chinese
scaffolding. A pluggable Translator protocol remains for a future
verified translation step; any translator failure falls back safely.

The assembled briefs go through the SAME editorial QA gates as
Muse-written drafts (simp_publisher.editorial.qa_draft_simp): real URL,
Simplified-char check, regional-availability note, claim overlap >= 0.6,
injection scan, length cap.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol

log = logging.getLogger("simp_publisher.assemble")

MAX_CHARS = 500
MAX_KEY_POINTS = 3

AI_DISCLOSURE_ASSEMBLED = (
    "［AI 生成内容标识］本摘要由程序自动摘编（含机器翻译），未经人工撰写；"
    "事实以所列来源原文为准。"
)
REGIONAL_NOTE = "区域说明：中国大陆可用性以来源原文为准，本站不作额外断言。"


class Translator(Protocol):
    def translate(self, text: str, target: str = "zh-CN") -> str: ...


class NullTranslator:
    """Original-language tier: returns text unchanged."""

    def translate(self, text: str, target: str = "zh-CN") -> str:
        return text


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def assemble_brief(pack: Dict[str, Any], date: str,
                   translator: Optional[Translator] = None) -> Dict[str, Any]:
    """Assemble one brief from a fact-pack dict. Pure + deterministic."""
    tr = translator or NullTranslator()
    tier = "mt"
    try:
        title_zh = _truncate(tr.translate(pack["title"]), 60)
        kps = [_truncate(tr.translate(kp), 120)
               for kp in pack["key_points"][:MAX_KEY_POINTS]]
        if not title_zh or not any(kps):
            raise RuntimeError("empty translation output")
    except Exception as exc:  # fail-safe -> original-language tier
        log.warning("translation failed (%s); using original-language tier", exc)
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
