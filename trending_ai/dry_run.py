"""Manual Trending AI DRY_RUN: existing research/dedup + evidence-backed stories.

No Threads client, publishing, scheduling or production-state mutation.
Exit 0 = evidence-reviewed Cantonese draft; 1 = source failure;
2 = unexpected failure; 3 = no qualified story (inconclusive).
Legacy English fallback is no longer selected by this editorial workflow.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trending_ai.github_trending.cantonese import generate_editorial_story
from trending_ai.github_trending.formatter_v2 import TrendingRepo, format_story, utf16_len
from trending_ai.github_trending.runner import run as trending_run

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("trending_ai.dry_run")


def _categorize_error(exc: BaseException) -> str:
    """Only sanitized categories; never log provider response or raw exceptions."""
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "auth_error"
        if exc.code == 429:
            return "rate_limit_error"
        return f"http_error_{exc.code}"
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return "network_error"
    name = type(exc).__name__.lower()
    if "json" in name or "decode" in name:
        return "parse_error"
    if "validation" in name or "value" in name:
        return "validation_error"
    if "redirect" in str(exc).lower():
        return "redirect_rejected"
    return "unknown_error"


def editorial_draft(draft: dict, recent_hooks=()) -> dict:
    """Qualify before runner reserves identity; failure advances selection."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ev = draft["evidence"]
    story = generate_editorial_story(ev, recent_hooks)
    if not story:
        raise ValueError("no evidence-reviewed story")
    trend = ev["trend"]
    if trend["period"] != "daily":
        raise ValueError("daily metadata required")
    observed = datetime.fromisoformat(trend["observed_at"].replace("Z", "+00:00"))
    owner, name = ev["name"].split("/")
    repo = TrendingRepo(
        owner=owner, name=name, repo_id=ev["repo_id"],
        trending_date=str(observed.astimezone(ZoneInfo("America/Toronto")).date()),
        trending_rank=trend["rank"], daily_star_gain=trend["period_stars"],
        language=ev["language"], license=ev["license"],
        official_description=ev["description"],
    )
    text = format_story(repo, story)
    return dict(draft, text=text, chars_utf16=utf16_len(text),
                editorial_story=story, tier="evidence-reviewed-cantonese-story",
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def main() -> int:
    import argparse
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="daily", choices=["daily"])
    parser.add_argument("--recent-hooks", help="explicit read-only JSON list of recent approved hooks")
    args = parser.parse_args()
    try:
        hooks = []
        if args.recent_hooks:
            from pathlib import Path
            raw = Path(args.recent_hooks).read_bytes()
            if len(raw) > 10_000:
                raise ValueError("hook export too large")
            hooks = json.loads(raw.decode("utf-8"))
            if not isinstance(hooks, list):
                raise ValueError("invalid hook export")
        with tempfile.TemporaryDirectory() as tmp:
            result = trending_run(
                history_path=os.path.join(tmp, "hist.sqlite3"), period=args.period,
                locale="zh-TW", limit=1, editor=editorial_draft, recent_hooks=hooks,
            )
    except Exception as exc:
        log.error("research failed: %s", _categorize_error(exc))
        return 1
    if result.get("outcome") != "drafts-ready" or not result.get("drafts"):
        print(json.dumps({"outcome": "no-new-qualified-projects", "conclusive": False,
                          "publication_performed": False,
                          "rejected": result.get("rejected", [])}, ensure_ascii=False, indent=2))
        return 3
    md = result["drafts"][0]
    ev = md["evidence"]
    story = md["editorial_story"]
    output = {
        "outcome": "draft-ready", "repo": ev["name"], "repo_id": ev["repo_id"],
        "post": md["text"], "char_count": len(md["text"]),
        "utf16_char_count": md["chars_utf16"],
        "used_llm_summary": True, "used_english_fallback": False,
        "editorial_review_passed": True, "requires_review": True,
        "publication_performed": False, "dedup_key": md["dedup_key"],
        "source_links": md["source_links"],
        "source_evidence": {"readme_url": ev["readme_url"],
                            "readme_sha256": ev["readme_sha256"],
                            "citations": story["citations"]},
        "editorial_story": {key: story[key] for key in ("hook", "problem", "solution", "why")},
        "review": story["review"], "rejected": result.get("rejected", []),
        "hook_history_count": len(hooks),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
