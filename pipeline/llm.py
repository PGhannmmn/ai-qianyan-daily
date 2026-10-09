"""Cloudflare Workers AI LLM client for original Simplified Chinese summaries.

Design principles (Phase 4.4):
- Zero extra cost: Workers Free gives 10,000 Neurons/day; we use ~20/day.
- Fail CLOSED: on quota exhaustion, model unavailability, API error, or
  failed QA, the article is SKIPPED. Never fall back to English text
  disguised as Chinese.
- Grounded generation: the prompt carries ONLY the verified fact-pack;
  the model is instructed to use nothing else.
- Credentials: CF_API_TOKEN via env (GitHub Actions Secret). Never printed,
  never committed, never logged.

Endpoint: POST https://api.cloudflare.com/client/v4/accounts/{id}/ai/run/{model}
Auth: Authorization: Bearer <token>  (token needs Account/Workers AI/Read)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("simp_publisher.llm")

# Free-tier models confirmed available on Workers Free (Oct 2026).
# Chinese-capable, in preference order. Paid-only models (kimi-k2.6,
# glm-5.2, ...) are NEVER in this list.
FREE_TIER_MODELS = [
    "@cf/zai-org/glm-4.7-flash",      # Zhipu, strong Chinese
    "@cf/qwen/qwen1.5-14b-chat-awq",  # Alibaba, Chinese-optimized
    "@cf/meta/llama-3.1-8b-instruct-fast",
]

SYSTEM_PROMPT = (
    "你是 AI 前沿情报局的中文科技新闻编辑。你只根据用户提供的事实材料撰写简体中文新闻摘要。"
    "严格规则：不编造任何数字、日期、引言、产品名；不确定的内容整句舍弃；"
    "保留原文中的实体名称（如 Google、Gemini）；不超过 500 字；"
    "使用简体中文，不使用繁体字。"
)


def build_user_prompt(pack: dict) -> str:
    """Build the grounded prompt from a verified fact-pack ONLY."""
    lines = [
        "事实材料（仅可使用以下内容，不得添加材料之外的信息）：",
        f"标题：{pack['title']}",
        f"来源：{pack['source_name']}",
        f"原文发布：{(pack.get('published_at') or '')[:10]}",
        "要点：",
    ]
    for kp in pack["key_points"][:6]:
        lines.append(f"- {kp}")
    lines += [
        "",
        "要求：撰写一段简体中文新闻摘要（标题式开头，正文精炼）。"
        f"文末必须包含来源名称“{pack['source_name']}”和原文链接 {pack['url']}，"
        "以及一句中国大陆可用性说明（以材料为准，不确定则写“中国大陆可用性以官方渠道为准”）。",
    ]
    return "\n".join(lines)


class WorkersAIError(RuntimeError):
    """Raised on any Workers AI failure. Callers must SKIP the article."""


class WorkersAILLM:
    """LLM summarizer via Cloudflare Workers AI REST API."""

    tier = "llm"

    def __init__(self, model: str = None, timeout: int = 60,
                 _http=None):
        self.account_id = os.environ.get("CF_ACCOUNT_ID", "")
        self.api_token = os.environ.get("CF_API_TOKEN", "")
        if not self.account_id or not self.api_token:
            raise WorkersAIError("CF_ACCOUNT_ID / CF_API_TOKEN not set")
        self.model = model or FREE_TIER_MODELS[0]
        if self.model not in FREE_TIER_MODELS:
            raise WorkersAIError(f"model not on free-tier allowlist: {self.model}")
        self.timeout = timeout
        self._http = _http  # injectable for tests (no network)

    def summarize(self, pack: dict) -> str:
        """Generate a Chinese summary. Raises WorkersAIError on ANY failure."""
        url = (f"https://api.cloudflare.com/client/v4/accounts/"
               f"{self.account_id}/ai/run/{self.model}")
        body = json.dumps({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(pack)},
            ],
            "max_tokens": 600,
        }).encode("utf-8")
        # NOTE: Authorization header is added ONLY inside _do_request
        # (real path), so the token never appears in logs or tracebacks.
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            raw = self._do_request(req)
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise WorkersAIError(f"request failed: {exc}") from exc
        if not data.get("success"):
            raise WorkersAIError(f"API error: {data.get('errors')}")
        text = ((data.get("result") or {}).get("response") or "").strip()
        if not text:
            raise WorkersAIError("empty model response")
        return text

    def _do_request(self, req: urllib.request.Request) -> bytes:
        if self._http is not None:
            return self._http(req)
        # Real path: swap in the actual token (never logged).
        req.add_header("Authorization", f"Bearer {self.api_token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()
