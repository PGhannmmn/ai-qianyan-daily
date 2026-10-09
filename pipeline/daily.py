#!/usr/bin/env python3
"""Daily pipeline orchestrator (runs INSIDE GitHub Actions).

One process, no secrets, no approvals, zero cost:
  1. research   — fetch RSS feeds, build fact-packs (public feeds only)
  2. fact QA    — shared qa_fact_pack gates
  3. assemble   — extractive Simplified-Chinese briefs (assemble.py)
  4. draft QA   — shared simp qa_draft_simp gates
  5. site build — sitegen.generate_site (DRY_RUN-equivalent locally)
  6. run log   — ALWAYS writes state/runs/<date>.json, even with zero
                 articles. The workflow commits site/ + state/, so every
                 scheduled run produces repository activity — this is the
                 compliant heartbeat that keeps GitHub's 60-day scheduled-
                 workflow rule from triggering.

In the deployment repo this file lives at pipeline/daily.py with the
needed modules vendored under pipeline/vendor/. For the local feasibility
test it imports the existing packages directly (same code, same gates).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# Repo layout: pipeline/vendor/ holds vendored stdlib-only modules.
# Local dev: fall back to the workspace packages.
sys.path.insert(0, os.path.join(HERE, "vendor"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, os.path.expanduser(
    "~/workspace/orbisignal-media/threads-publisher/src"))

from assemble import NullTranslator, assemble_brief  # noqa: E402
try:
    import sitegen  # noqa: E402  (repo layout: pipeline/vendor/)
except ImportError:
    from simp_publisher import sitegen  # noqa: E402  (local dev layout)


def _load_full_pipeline():
    """Lazy-load research+editorial (only needed for production runs)."""
    try:
        from simp_publisher.editorial import (  # noqa: E402
            qa_draft_simp, qa_fact_pack)
        from threads_publisher.news import research as R  # noqa: E402
    except ImportError:
        from vendor.simp_editorial import (  # noqa: E402,F401
            qa_draft_simp, qa_fact_pack)
        from vendor.research import R  # noqa: E402,F401,E0611
    return qa_draft_simp, qa_fact_pack, R

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("daily")

# Security: only these domains may be fetched (RSS feeds + article text).
# Article URLs outside the allowlist are skipped and logged.
TRUSTED_DOMAINS = frozenset({
    "blog.google",
    "developers.googleblog.com",
    "techcrunch.com",
    "www.technologyreview.com",
    "www.theverge.com",
})


def _domain_ok(url: str) -> bool:
    import urllib.parse as _up
    host = (_up.urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS)

def _default_feeds(R):
    return [
        R.FeedSource(name="Google Blog AI", kind="official",
                     url="https://blog.google/rss/"),
        R.FeedSource(name="Google DeepMind", kind="official",
                     url="https://blog.google/technology/ai/rss/"),
    ]


def rss_fetch_default(source, _R=None):
    R = _R or _load_full_pipeline()[2]
    return R.fetch_rss(source)


def _run_landing_only(date: str, out_dir: str, base_url: str) -> dict:
    """Phase 4.3 first deployment: landing page only, no articles."""
    result = sitegen.generate_site([], out_dir, base_url, dry_run=True)
    summary = {
        "date": date,
        "mode": "landing_only",
        "fact_packs": 0,
        "briefs": 0,
        "site_files": len(result["files"]),
        "safety_issues": result["safety_issues"],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.join("state", "runs"), exist_ok=True)
    with open(os.path.join("state", "runs", f"{date}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary


def run_pipeline(date: str, out_dir: str, base_url: str,
                 fetch=None, translator=None,
                 landing_only: bool = False) -> dict:
    """Run the full daily pipeline. Returns a summary dict."""
    if landing_only:
        return _run_landing_only(date, out_dir, base_url)
    qa_draft_simp, qa_fact_pack, R = _load_full_pipeline()
    if fetch is None:
        fetch = lambda source: R.fetch_rss(source)
    # 1+2. research + fact-pack QA
    packs = []
    for source in _default_feeds(R):
        if not _domain_ok(source.url):
            log.warning("feed domain not trusted, skipping: %s", source.url)
            continue
        try:
            items = fetch(source)
        except Exception as exc:
            log.warning("feed %s failed: %s", source.name, exc)
            continue
        for item in items:
            if not _domain_ok(item.link):
                log.warning("article domain not trusted, skipping: %s",
                            item.link)
                continue
            if not R.is_ai_related(item.title, item.summary):
                continue
            if not R.within_window(item.published, hours=48):
                continue
            try:
                article_text = R.fetch_article_text(item.link)
            except Exception as exc:
                log.warning("article fetch failed %s: %s", item.link, exc)
                continue
            pack = R.build_fact_pack(item, article_text)
            if qa_fact_pack(pack):
                continue
            packs.append(pack)
    log.info("fact-packs passing QA: %d", len(packs))

    # 3+4. assemble + draft QA (max 2 briefs/day)
    briefs = []
    for pack in packs[:2]:
        b = assemble_brief(pack.__dict__, date, translator=translator)
        issues = qa_draft_simp(b["text"], b["source"], pack)
        if issues:
            log.warning("brief rejected: %s", issues)
            continue
        briefs.append(b)
    log.info("briefs passing QA: %d", len(briefs))

    # 5. site build from assembled briefs
    _write_briefs_as_drafts(date, briefs, packs)
    result = sitegen.generate_site([date], out_dir, base_url, dry_run=True)

    # 6. ALWAYS write the run log (heartbeat, even with zero articles)
    summary = {
        "date": date,
        "fact_packs": len(packs),
        "briefs": len(briefs),
        "tiers": [b["tier"] for b in briefs],
        "site_files": len(result["files"]),
        "safety_issues": result["safety_issues"],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.join("state", "runs"), exist_ok=True)
    with open(os.path.join("state", "runs", f"{date}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary


def _write_briefs_as_drafts(date: str, briefs: list, packs: list) -> None:
    """Stage assembled briefs where sitegen expects validated drafts."""
    os.makedirs(sitegen.STATE_DIR, exist_ok=True)
    drafts = {"drafts": [{"text": b["text"], "source": b["source"],
                          "fact_index": i, "url": b["url"],
                          "published_at": packs[i].published_at
                          if i < len(packs) else "",
                          "source_name": b["source"]}
                         for i, b in enumerate(briefs)],
              "assembled": True, "tiers": [b["tier"] for b in briefs]}
    with open(os.path.join(sitegen.STATE_DIR, f"simp_drafts_{date}.json"),
              "w", encoding="utf-8") as fh:
        json.dump(drafts, fh, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", default="site")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--landing-only", action="store_true",
                    help="build the informational landing page only "
                         "(Phase 4.3 first deployment; no articles)")
    args = ap.parse_args()
    # Third-party translation disabled: original-language tier only.
    summary = run_pipeline(args.date, args.out, args.base_url,
                           translator=NullTranslator(),
                           landing_only=args.landing_only)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["safety_issues"] else 1


if __name__ == "__main__":
    sys.exit(main())
