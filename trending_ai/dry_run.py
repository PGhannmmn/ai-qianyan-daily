"""Isolated GitHub Trending AI DRY_RUN for GitHub Actions.

Does RESEARCH + CANTONESE SUMMARY + V2 FORMAT + VALIDATION only.

Explicitly does NOT:
- Call any Threads API (no credentials, no client code included).
- Write to any dedup database or history file.
- Publish, schedule, or mutate state.

Credentials: CF_API_TOKEN (secret) and CF_ACCOUNT_ID (variable) from
the GitHub Actions environment. Never printed or logged.

Exit codes:
0 = success (real Cantonese summary generated and validated);
1 = research failure;
2 = unexpected error;
3 = no qualified candidate (inconclusive);
4 = English fallback used (inconclusive — LLM unavailable or failed).
"""
from __future__ import annotations

import json
import logging
import os
import sys

# Repo-root relative imports (this file lives in trending_ai/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trending_ai.github_trending.cantonese import (  # noqa: E402
    generate_cantonese_summary,
)
from trending_ai.github_trending.formatter_v2 import (  # noqa: E402
    TrendingRepo,
    format_post,
)
from trending_ai.github_trending.runner import run as trending_run  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("trending_ai.dry_run")


def _categorize_error(exc: BaseException) -> str:
    """Map an exception to a sanitized error category.

    Never logs the raw exception message, which may contain URLs,
    credential fragments, or sensitive API response data.
    """
    import urllib.error
    name = type(exc).__name__
    msg = str(exc).lower()
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "auth_error"
        if exc.code == 429:
            return "rate_limit_error"
        return f"http_error_{exc.code}"
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return "network_error"
    if "json" in name.lower() or "decode" in name.lower():
        return "parse_error"
    if "validation" in name.lower() or "value" in name.lower():
        return "validation_error"
    if "redirect" in msg:
        return "redirect_rejected"
    return "unknown_error"


def main() -> int:
    import argparse
    import tempfile
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="daily",
                        choices=["daily", "weekly", "monthly"])
    args = parser.parse_args()

    # Research (public GitHub endpoints, no auth needed).
    # Use a temp history file; never persist.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = trending_run(
                history_path=os.path.join(tmp, "hist.sqlite3"),
                period=args.period,
                locale="zh-TW",
                limit=1,
            )
        except Exception as exc:
            log.error("research failed: %s", _categorize_error(exc))
            return 1

    if result.get("outcome") != "drafts-ready" or not result.get("drafts"):
        log.info("no qualified candidates")
        print(json.dumps({"outcome": "no-new-qualified-projects",
                          "publication_performed": False,
                          "conclusive": False}))
        return 3

    md = result["drafts"][0]
    ev = md["evidence"]
    trend = ev["trend"]

    # Cantonese summary via Workers AI (falls back to "" without creds).
    zh_summary = generate_cantonese_summary(
        ev.get("name", ""),
        ev.get("description", ""),
        ev.get("language", ""),
    )
    used_llm = bool(zh_summary)
    log.info("Cantonese summary: %s",
             "generated via Workers AI" if used_llm else "fallback (English)")

    # V2 format (deterministic fields only; never LLM-generated).
    owner, _, name = ev.get("name", "").partition("/")
    # Convert to America/Toronto date (not just truncating UTC).
    observed = trend.get("observed_at", "")
    try:
        dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        toronto_date = dt.astimezone(
            ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        toronto_date = observed[:10]  # fallback to raw truncation
    repo = TrendingRepo(
        owner=owner,
        name=name,
        repo_id=int(ev.get("repo_id", 0)),
        trending_date=toronto_date,
        trending_rank=int(trend.get("rank", 0)),
        daily_star_gain=int(trend.get("period_stars", 0)),
        language=str(ev.get("language") or ""),
        license=str(ev.get("license") or ""),
        official_description=str(ev.get("description") or ""),
        zh_summary=zh_summary,
    )
    try:
        post = format_post(repo)
    except ValueError as exc:
        log.error("format validation failed: %s", _categorize_error(exc))
        return 1

    # Output (no credentials, no sensitive data).
    if not used_llm:
        # English fallback: report inconclusive, do not treat as success.
        log.warning("English fallback used; marking inconclusive")
        output = {
            "outcome": "inconclusive-english-fallback",
            "repo": ev.get("name"),
            "post": post,
            "used_llm_summary": False,
            "used_english_fallback": True,
            "requires_review": True,
            "publication_performed": False,
            "conclusive": False,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 4

    output = {
        "outcome": "draft-ready",
        "repo": ev.get("name"),
        "repo_id": ev.get("repo_id"),
        "post": post,
        "char_count": len(post),
        "used_llm_summary": used_llm,
        "used_english_fallback": not used_llm,
        "requires_review": True,  # Always true; never auto-approved.
        "publication_performed": False,
        "dedup_key": repo.dedup_key,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
