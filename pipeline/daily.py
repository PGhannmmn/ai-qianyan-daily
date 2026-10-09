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
import re
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

from assemble import (NullTranslator, assemble_brief,  # noqa: E402
                      is_genuine_simplified_editorial)
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


def _llm_brief(llm, pack, date: str):
    """Generate one brief via Workers AI LLM. Returns None on ANY failure
    (fail-closed: the article is skipped)."""
    from llm import WorkersAIError  # noqa: E402
    try:
        text = llm.summarize(pack.__dict__).strip()
    except WorkersAIError as exc:
        log.warning("LLM summarize failed, skipping article: %s", exc)
        return None
    # Deterministic attribution: URL on its own line, not left to the model.
    if pack.url not in text:
        text = text.rstrip() + "\n原文链接：" + pack.url
    return {"text": text, "source": pack.source_name, "url": pack.url,
            "date": date, "tier": "llm", "chars": len(text)}


def _all_draft_dates(current_date: str) -> list:
    """All dates with draft files, sorted, current date included.
    Enables historical archive preservation across daily runs."""
    import glob as _glob
    pattern = os.path.join(sitegen.STATE_DIR, "simp_drafts_*.json")
    dates = set()
    for path in _glob.glob(pattern):
        m = re.search(r"simp_drafts_(\d{4}-\d{2}-\d{2})\.json$", path)
        if m:
            dates.add(m.group(1))
    dates.add(current_date)
    return sorted(dates)


def _published_urls() -> set:
    """URLs already published on previous days (cross-day dedup).

    Source of truth: the committed simp_drafts_*.json files. Every draft
    written by _write_briefs_as_drafts carries its pack's URL, so scanning
    them yields exactly what the site has published.
    """
    urls = set()
    import glob as _glob
    pattern = os.path.join(sitegen.STATE_DIR, "simp_drafts_*.json")
    for path in _glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        for d in data.get("drafts", []):
            if d.get("url"):
                urls.add(d["url"])
    return urls


def run_pipeline(date: str, out_dir: str, base_url: str,
                 fetch=None, translator=None,
                 landing_only: bool = False,
                 use_llm: bool = False,
                 dry_run: bool = False) -> dict:
    """Run the full daily pipeline. Returns a summary dict."""
    if landing_only:
        return _run_landing_only(date, out_dir, base_url)
    qa_draft_simp, qa_fact_pack, R = _load_full_pipeline()
    if fetch is None:
        fetch = lambda source: R.fetch_rss(source)
    llm = None
    if use_llm:
        from llm import WorkersAIError, WorkersAILLM  # noqa: E402
        try:
            llm = WorkersAILLM()
        except WorkersAIError as exc:
            # Fail closed: no credentials/API -> zero briefs, never English.
            log.error("LLM unavailable (%s); skipping all articles", exc)
            llm = None
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
    # briefs: list of (brief, pack) tuples. The pack travels WITH its brief,
    # so a rejected/skipped article can never shift its URL/title/date onto
    # another article (the old positional indexing had this bug).
    briefs = []
    seen_urls = _published_urls()
    if seen_urls:
        log.info("cross-day dedup: %d URLs already published", len(seen_urls))
    for pack in packs[:2]:
        if pack.url in seen_urls:
            log.info("skipping already-published URL: %s", pack.url)
            continue
        if llm is not None:
            b = _llm_brief(llm, pack, date)
            if b is None:
                continue  # fail-closed: skip, never English-as-Chinese
        else:
            if use_llm:
                continue  # LLM requested but unavailable: zero briefs
            b = assemble_brief(pack.__dict__, date, translator=translator)
        issues = qa_draft_simp(b["text"], b["source"], pack)
        if issues:
            log.warning("brief rejected: %s", issues)
            continue
        if not is_genuine_simplified_editorial(b["text"]):
            log.warning("brief rejected: not genuine Simplified Chinese "
                        "content (tier=%s); skipping, not publishing "
                        "English-as-Chinese", b["tier"])
            continue
        briefs.append((b, pack))
    log.info("briefs passing QA: %d", len(briefs))

    if dry_run:
        # Private DRY_RUN: report, write nothing.
        log.info("DRY_RUN: %d briefs would publish (writing nothing)",
                 len(briefs))
        for b, _ in briefs:
            log.info("DRY_RUN brief [%s/%d chars]: %.80s...",
                     b["tier"], b["chars"], b["text"][:80])
        return {"date": date, "briefs": len(briefs),
                "tiers": [b["tier"] for b, _ in briefs],
                "dry_run": True}

    # 5. site build from ALL historical briefs (new + previous days).
    # Previous days' draft files are committed to the repo, so the site
    # preserves archives, RSS, and sitemap across runs.
    _write_briefs_as_drafts(date, briefs)
    all_dates = _all_draft_dates(date)
    result = sitegen.generate_site(all_dates, out_dir, base_url, dry_run=True)

    # 6. ALWAYS write the run log (heartbeat, even with zero articles)
    summary = {
        "date": date,
        "fact_packs": len(packs),
        "briefs": len(briefs),
        "tiers": [b["tier"] for b, _ in briefs],
        "site_files": len(result["files"]),
        "safety_issues": result["safety_issues"],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.join("state", "runs"), exist_ok=True)
    with open(os.path.join("state", "runs", f"{date}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary


def _write_briefs_as_drafts(date: str, briefs: list) -> None:
    """Stage assembled briefs where sitegen expects validated drafts.

    briefs: list of (brief_dict, pack) tuples. Metadata (url, published_at,
    source_name) comes from the pack PAIRED with each brief — never from
    positional indexing into a separate pack list.
    """
    os.makedirs(sitegen.STATE_DIR, exist_ok=True)
    drafts = {"drafts": [{"text": b["text"], "source": b["source"],
                          "url": pack.url,
                          "published_at": pack.published_at or "",
                          "source_name": pack.source_name}
                         for b, pack in briefs],
              "assembled": True, "tiers": [b["tier"] for b, _ in briefs]}
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
    ap.add_argument("--llm", action="store_true",
                    help="use Cloudflare Workers AI for original Chinese "
                         "summaries (needs CF_API_TOKEN/CF_ACCOUNT_ID)")
    ap.add_argument("--dry-run", action="store_true",
                    help="private test: run research + LLM + QA, report what "
                         "would publish, but write NOTHING (no drafts, no "
                         "site, no commit)")
    args = ap.parse_args()
    # Third-party translation disabled: original-language tier only.
    summary = run_pipeline(args.date, args.out, args.base_url,
                           translator=NullTranslator(),
                           landing_only=args.landing_only,
                           use_llm=args.llm,
                           dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["safety_issues"] else 1


if __name__ == "__main__":
    sys.exit(main())
