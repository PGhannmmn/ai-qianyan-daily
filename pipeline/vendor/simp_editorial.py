"""Deterministic fail-closed editorial screening, with exact source evidence.

Lexical anchors do not prove a translation is factually correct. These gates
check evidence, obvious unsupported entities/numbers and injection signals;
human review is still necessary before authorizing public publication.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path
import re

from .safety import (MAX_ARTICLE, MAX_SUMMARY, SafetyError, checked_text,
                     contains_injection, contains_sensitive, data_fields, normalized, source_url)
from .research import parse_date


def _traditional_characters() -> frozenset[str]:
    rows = []
    for line in (Path(__file__).parent / "data" / "TSCharacters.txt").read_text(encoding="utf-8").splitlines():
        if "\t" in line and not line.startswith("#"):
            old, new = line.split("\t", 1)
            rows.append((old, new.split()))
    # Ambiguous/shared characters are not evidence of Traditional Chinese.
    simplified = {c for _, variants in rows for c in variants if len(c) == 1}
    return frozenset(old for old, _ in rows if len(old) == 1 and old not in simplified)


TRADITIONAL_ONLY = _traditional_characters()
URL_RE = re.compile(r"https?://[^\s)）】》\"']+")
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
TEMPLATE_PREFIXES = ("来源：", "原文链接：", "原文发布：", "［AI 生成内容标识］", "区域说明：")


def editorial_body(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.strip().startswith(TEMPLATE_PREFIXES):
            continue
        lines.append(line.replace("［AI 快讯］", ""))
    return URL_RE.sub("", "\n".join(lines))


def cjk_ratio(text: str) -> float:
    chars = [c for c in editorial_body(text) if not c.isspace()]
    return sum(bool(CJK_RE.match(c)) for c in chars) / len(chars) if chars else 0.0


def qa_fact_pack(pack) -> list[str]:
    try:
        fields = pack if isinstance(pack, dict) else vars(pack)
        data_fields(fields)
        raw = checked_text(fields.get("article_text"), MAX_ARTICLE)
        if fields.get("article_sha256") != sha256(raw.encode("utf-8")).hexdigest():
            return ["evidence_hash_mismatch"]
        if any(point not in raw for point in fields["key_points"]):
            return ["unverified_key_points"]
        if parse_date(fields.get("published_at")) is None:
            return ["missing_publication_date"]
        if fields.get("source_kind") not in {"official", "media"}:
            return ["invalid_source_kind"]
    except (SafetyError, TypeError, AttributeError):
        return ["invalid_fact_pack"]
    return []


def claim_overlap(body: str, evidence: str) -> float:
    """Coverage of draft lexical anchors in source evidence, not semantics."""
    normalized_body, normalized_source = normalized(body), normalized(evidence)
    latin = set(re.findall(r"\b[a-z][a-z0-9_.-]*\b", normalized_body)) - {"ai"}
    numbers = set(re.findall(r"\d+(?:\.\d+)?", normalized_body))
    anchors = latin | numbers
    source_has_cjk = bool(CJK_RE.search(evidence))
    if source_has_cjk:
        anchors.update(part[i:i + 2] for part in re.findall(r"[\u4e00-\u9fff]+", normalized_body)
                       for i in range(len(part) - 1))
    if not anchors:
        return 0.0
    return sum(anchor in normalized_source for anchor in anchors) / len(anchors)


def qa_draft_simp(text: str, source: str, pack) -> list[str]:
    if qa_fact_pack(pack):
        return ["invalid_fact_pack"]
    fields = pack if isinstance(pack, dict) else vars(pack)
    try:
        checked_text(text, MAX_SUMMARY)
    except SafetyError:
        return ["unsafe_or_invalid_draft"]
    if source != fields["source_name"]:
        return ["source_mismatch"]
    if fields["url"] not in text:
        return ["missing_source_url"]
    if any(url != fields["url"] for url in URL_RE.findall(text)):
        return ["unexpected_url"]
    body = editorial_body(text)
    if any(c in TRADITIONAL_ONLY for c in body):
        return ["traditional_characters"]
    if cjk_ratio(text) < 0.4:
        return ["insufficient_chinese_editorial"]
    if "［AI 生成内容标识］" not in text or "区域说明：中国大陆可用性以官方渠道为准。" not in text:
        return ["missing_disclosure_or_regional_note"]
    # Regional assertions must not be supplied by an untrusted model.
    if re.search(r"中国大陆|大陆地区|国内可用|在中国上线", body):
        return ["unverified_regional_claim"]
    evidence = fields["title"] + "\n" + "\n".join(fields["key_points"])
    source_numbers = set(re.findall(r"\d+(?:\.\d+)?", evidence))
    if not set(re.findall(r"\d+(?:\.\d+)?", body)).issubset(source_numbers):
        return ["unsupported_number"]
    # Require every Latin entity in generated text to occur in the evidence.
    entities = set(re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]*\b", body)) - {"AI"}
    source_entities = set(re.findall(r"\b[a-z][a-z0-9_.-]*\b", evidence.casefold()))
    if any(entity.casefold() not in source_entities for entity in entities):
        return ["unsupported_entity"]
    if claim_overlap(body, evidence) < 0.6:
        return ["insufficient_claim_overlap"]
    return []


def validate_saved_draft(draft: dict) -> None:
    """Legacy validated drafts keep their order and metadata; unsafe state fails."""
    if not isinstance(draft, dict):
        raise SafetyError("invalid_saved_draft")
    checked_text(draft.get("text"), MAX_SUMMARY)
    checked_text(draft.get("source"), 80)
    checked_text(draft.get("source_name", draft.get("source")), 80)
    source_url(draft.get("url"))
    published = draft.get("published_at")
    if parse_date(published) is None:
        try:
            if not isinstance(published, str) or len(published) != 10 or date.fromisoformat(published).isoformat() != published:
                raise ValueError
        except ValueError:
            raise SafetyError("invalid_saved_publication_date") from None
