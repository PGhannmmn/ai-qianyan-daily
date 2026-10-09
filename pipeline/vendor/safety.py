"""Input boundaries shared by research, editorial, persistence and rendering.

Pattern checks are conservative screening, not proof of semantic truth or
complete prompt-injection detection. No untrusted text is a log message.
"""
from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRUSTED_DOMAINS = frozenset({
    "blog.google", "developers.googleblog.com", "techcrunch.com",
    "www.technologyreview.com", "www.theverge.com",
})
MAX_TITLE = 180
MAX_POINT = 450
MAX_POINTS = 6
MAX_ARTICLE = 30000
MAX_SUMMARY = 500


class SafetyError(ValueError):
    """A fixed error code, never the rejected input."""


def normalized(text: str) -> str:
    for _ in range(2):
        text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return re.sub(r"\s+", " ", text)


INJECTION_RE = re.compile(
    r"(?:ignore|disregard|override|forget)\b.{0,60}\b(?:instructions?|prompts?|rules?|system)\b|"
    r"(?:reveal|print|dump|send|exfiltrate|show|output)\b.{0,80}\b(?:secrets?|credentials?|api[_ -]?tokens?|api[_ -]?keys?|environment variables?|system prompt)\b|"
    r"(?:system|assistant|developer)\s*(?:message|prompt)?\s*:|"
    r"you\s+are\s+now\s+(?:a|an)\s+|"
    r"do\s+not\s+follow\s+(?:your|the)\s+guidelines|"
    r"reveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)|"
    r"<\|(?:im_start|im_end|system|assistant|endoftext)|\[/?inst\]|"
    r"(?:忽略|无视|覆盖|忘记|不必遵守).{0,30}(?:指令|提示词|规则|系统)|"
    r"(?:泄露|透露|输出|打印|发送|显示).{0,30}(?:密钥|秘密|凭证|令牌|环境变量|系统提示)|"
    r"(?:系统|助手|开发者)\s*(?:消息|指令|提示词)?\s*[:：]|"
    r"你(?:现在|現在)?是(?:一个|一個|一名|個)?(?:新的?)?(?:ai|助手|助理|系统|系統)",
    re.IGNORECASE,
)
SECRET_RE = re.compile(
    r"Bearer\s+\S+|(?:sk-(?:ant-|proj-)?|xox[baprs]-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{8,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:CF_API_TOKEN|API[_ -]?(?:TOKEN|KEY)|PASSWORD|SECRET)\s*[=:]\s*\S+|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)


def contains_injection(text: str) -> bool:
    return bool(INJECTION_RE.search(normalized(text)))


def contains_sensitive(text: str, secrets=()) -> bool:
    return bool(SECRET_RE.search(normalized(text))) or any(
        isinstance(s, str) and len(s) >= 8 and s in text for s in secrets
    )


def checked_text(value, limit: int, *, secrets=()) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SafetyError("invalid_text")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise SafetyError("control_characters")
    if contains_sensitive(value, secrets):
        raise SafetyError("sensitive_content")
    if contains_injection(value):
        raise SafetyError("instruction_content")
    return value.strip()


def source_url(value: str) -> str:
    """Canonicalize a public source URL; every redirect uses this gate."""
    if not isinstance(value, str) or len(value) > 2048 or not value:
        raise SafetyError("invalid_source_url")
    if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise SafetyError("invalid_source_url")
    try:
        p = urlsplit(value)
        if (p.scheme != "https" or p.hostname not in TRUSTED_DOMAINS
                or p.username is not None or p.password is not None
                or p.port not in (None, 443)):
            raise SafetyError("invalid_source_url")
    except (ValueError, AttributeError):
        raise SafetyError("invalid_source_url") from None
    query = parse_qsl(p.query, keep_blank_values=True)
    if any(re.search(r"token|password|secret|auth|api[_-]?key", k, re.I)
           for k, _ in query) or contains_sensitive(value):
        raise SafetyError("sensitive_source_url")
    query = sorted((k, v) for k, v in query
                   if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"})
    return urlunsplit(("https", p.hostname, p.path or "/", urlencode(query), ""))


def public_base_url(value: str) -> str:
    try:
        p = urlsplit(value)
        if (p.scheme != "https" or not p.hostname or p.username is not None
                or p.password is not None or p.port not in (None, 443)
                or p.query or p.fragment or any(c.isspace() for c in value)):
            raise SafetyError("invalid_base_url")
    except (ValueError, AttributeError, TypeError):
        raise SafetyError("invalid_base_url") from None
    return value.rstrip("/")


def data_fields(pack: dict, *, secrets=()) -> dict:
    if not isinstance(pack, dict):
        raise SafetyError("invalid_fact_pack")
    title = checked_text(pack.get("title"), MAX_TITLE, secrets=secrets)
    checked_text(pack.get("source_name"), 80, secrets=secrets)
    source_url(pack.get("url"))
    points = pack.get("key_points")
    if not isinstance(points, list) or not 1 <= len(points) <= MAX_POINTS:
        raise SafetyError("invalid_key_points")
    return {"title": title, "key_points": [
        checked_text(p, MAX_POINT, secrets=secrets) for p in points
    ]}
