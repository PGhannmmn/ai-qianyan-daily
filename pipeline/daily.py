#!/usr/bin/env python3
"""Repository-contained research, QA and local site preview/build.

--dry-run validates and renders in memory. It never writes drafts, site
files, run records, caches or bytecode, and contains no deployment path.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

if __package__:
    from .assemble import NullTranslator, assemble_brief
    from .vendor import sitegen
    from .vendor.safety import SafetyError, checked_text, source_url, public_base_url
    from .vendor.storage import StateError, append_drafts, atomic_json, merge_drafts, read_drafts, valid_date
else:
    from assemble import NullTranslator, assemble_brief
    from vendor import sitegen
    from vendor.safety import SafetyError, checked_text, source_url, public_base_url
    from vendor.storage import StateError, append_drafts, atomic_json, merge_drafts, read_drafts, valid_date

log = logging.getLogger("daily")
AI_DISCLOSURE = "［AI 生成内容标识］本摘要由 AI 生成，经程序校验，未经人工撰写；事实以所列来源原文为准。"
REGIONAL_NOTE = "区域说明：中国大陆可用性以官方渠道为准。"


def _load_full_pipeline():
    # No home-directory/other-project fallback: the checkout is sufficient.
    if __package__:
        from .vendor.simp_editorial import qa_draft_simp, qa_fact_pack
        from .vendor import research as R
    else:
        from vendor.simp_editorial import qa_draft_simp, qa_fact_pack
        from vendor import research as R
    return qa_draft_simp, qa_fact_pack, R


def _domain_ok(url: str) -> bool:
    try:
        source_url(url)
        return True
    except SafetyError:
        return False


def _default_feeds(R):
    return [
        R.FeedSource("Google Blog AI", "official", "https://blog.google/rss/"),
        R.FeedSource("Google DeepMind", "official", "https://blog.google/technology/ai/rss/"),
    ]


def rss_fetch_default(source, _R=None):
    return (_R or _load_full_pipeline()[2]).fetch_rss(source)


def _all_draft_dates(current_date: str) -> list[str]:
    valid_date(current_date)
    days = {current_date}
    for path in Path(sitegen.STATE_DIR).glob("simp_drafts_*.json"):
        day = path.name.removeprefix("simp_drafts_").removesuffix(".json")
        valid_date(day)
        days.add(day)
    return sorted(days)


def _history(current_date: str) -> dict:
    return {day: read_drafts(sitegen.STATE_DIR, day)["drafts"]
            for day in _all_draft_dates(current_date)}


def _published_urls() -> set[str]:
    urls = set()
    for path in Path(sitegen.STATE_DIR).glob("simp_drafts_*.json"):
        day = path.name.removeprefix("simp_drafts_").removesuffix(".json")
        for draft in read_drafts(sitegen.STATE_DIR, day)["drafts"]:
            urls.add(source_url(draft["url"]))
    return urls


def _llm_brief(llm, pack, date: str):
    try:
        text = checked_text(llm.summarize(asdict(pack)), 280,
                            secrets=(getattr(llm, "api_token", ""), getattr(llm, "account_id", "")))
        # Attribution/disclosure are trusted local scaffolding, never model data.
        if "http://" in text or "https://" in text:
            return None
        text = ("［AI 快讯］" + text.removeprefix("［AI 快讯］") + "\n\n"
                f"来源：{pack.source_name}\n原文链接：{pack.url}\n"
                f"原文发布：{pack.published_at[:10]}\n\n{AI_DISCLOSURE}\n{REGIONAL_NOTE}")
        return {"text": text, "source": pack.source_name, "url": pack.url,
                "date": date, "tier": "llm", "chars": len(text)}
    except Exception:
        log.warning("llm_generation_failed")
        return None


def _drafts_from_briefs(briefs: list) -> list[dict]:
    return [{
        "text": b["text"], "source": pack.source_name, "url": pack.url,
        "published_at": pack.published_at, "source_name": pack.source_name,
        "tier": b["tier"],
        "provenance": {"article_sha256": pack.article_sha256,
                       "key_points": list(pack.key_points), "source_kind": pack.source_kind},
    } for b, pack in briefs]


def _write_briefs_as_drafts(date: str, briefs: list) -> bool:
    return append_drafts(sitegen.STATE_DIR, date, _drafts_from_briefs(briefs))


def _summary(date: str, dry_run: bool) -> dict:
    try:
        safe_date = valid_date(date)
    except StateError:
        safe_date = None
    return {"date": safe_date, "dry_run": dry_run, "mode": "digest",
            "status": "failed", "zero_article_reason": None,
            "fact_packs": 0, "briefs": 0, "tiers": [], "skipped": 0,
            "draft_rejections": 0,
            "llm_attempts": 0, "llm_successes": 0,
            "site_files": 0, "historical_articles": 0,
            "safety_issues": [], "errors": [], "deployed": False}


def _finalize_summary(summary: dict) -> dict:
    """Only validated results or explicit editorial emptiness can succeed."""
    summary["errors"] = sorted(set(summary["errors"]))
    if summary["errors"] or summary["safety_issues"]:
        summary["status"] = "failed"
        summary["zero_article_reason"] = None
    elif summary["mode"] == "landing_only":
        summary["status"] = "landing_only"
        summary["zero_article_reason"] = "landing_only"
    elif summary["briefs"]:
        summary["status"] = "validated"
        summary["zero_article_reason"] = None
    else:
        summary["status"] = "no_articles"
        summary["zero_article_reason"] = summary["zero_article_reason"] or (
            "editorial_filtered" if summary["draft_rejections"] else "no_eligible_sources")
    return summary


def run_pipeline(date: str, out_dir: str, base_url: str,
                 fetch=None, translator=None, landing_only: bool = False,
                 use_llm: bool = False, dry_run: bool = False,
                 article_fetch=None, llm_factory=None, now=None) -> dict:
    summary = _summary(date, dry_run)
    try:
        valid_date(date)
        public_base_url(base_url)
        history = _history(date)
        if landing_only:
            # Never remove existing articles when validating a landing-only run.
            summary["mode"] = "landing_only"
            files = sitegen.render_site(sitegen.articles_from_drafts(history), base_url)
            summary["safety_issues"] = sitegen.check_rendered_safety(files)
            summary["site_files"] = len(files)
            summary["historical_articles"] = sum(len(v) for v in history.values())
            _finalize_summary(summary)
            if not dry_run and not summary["safety_issues"]:
                sitegen.generate_site(sorted(history), out_dir, base_url)
                atomic_json(Path(sitegen.STATE_DIR) / "runs" / f"{date}.json", summary)
            return _finalize_summary(summary)

        qa_draft_simp, qa_fact_pack, R = _load_full_pipeline()
        llm = None
        if use_llm:
            if llm_factory is None:
                if __package__:
                    from .llm import WorkersAILLM
                else:
                    from llm import WorkersAILLM
                llm_factory = WorkersAILLM
            try:
                llm = llm_factory()
            except Exception:
                summary["errors"] = ["llm_unavailable"]
                log.warning("llm_unavailable")
                return _finalize_summary(summary)

        fetch = fetch or R.fetch_rss
        article_fetch = article_fetch or R.fetch_article_text
        seen_urls = {source_url(d["url"]) for drafts in history.values() for d in drafts}
        attempted = set()
        briefs = []
        max_new = max(0, 2 - len(history[date]))
        if not max_new:
            summary["zero_article_reason"] = "daily_limit_reached"
        for source in (_default_feeds(R) if max_new else []):
            try:
                items = fetch(source)
            except Exception:
                log.warning("research_feed_failed")
                summary["errors"].append("research_feed_failed")
                continue
            for item in items[:R.MAX_ITEMS]:
                if len(briefs) >= max_new:
                    break
                try:
                    url = source_url(item.link)
                    if url in seen_urls or url in attempted:
                        summary["skipped"] += 1
                        continue
                    attempted.add(url)
                    if not R.is_ai_related(item.title, item.summary) or not R.within_window(item.published, now=now):
                        summary["skipped"] += 1
                        continue
                except SafetyError:
                    log.warning("source_item_rejected")
                    summary["skipped"] += 1
                    continue
                try:
                    article_text = article_fetch(url)
                except R.ResearchError as exc:
                    if str(exc) == "article_rejected":
                        log.warning("source_item_rejected")
                        summary["skipped"] += 1
                    else:
                        log.warning("research_article_failed")
                        summary["errors"].append("research_article_failed")
                    continue
                except Exception:
                    log.warning("research_article_failed")
                    summary["errors"].append("research_article_failed")
                    continue
                try:
                    pack = R.build_fact_pack(item, article_text)
                    if qa_fact_pack(pack):
                        summary["skipped"] += 1
                        continue
                except (SafetyError, R.ResearchError):
                    # Untrusted fetch/parse failures do not become log text.
                    log.warning("source_item_rejected")
                    summary["skipped"] += 1
                    continue
                summary["fact_packs"] += 1
                if llm is not None:
                    summary["llm_attempts"] += 1
                    brief = _llm_brief(llm, pack, date)
                    if brief is None:
                        summary["errors"].append("llm_generation_failed")
                        continue
                    summary["llm_successes"] += 1
                else:
                    brief = assemble_brief(asdict(pack), date, translator=translator or NullTranslator())
                    # Replace the legacy translator regional boilerplate locally.
                    brief["text"] = brief["text"].replace(
                        "区域说明：中国大陆可用性以来源原文为准，本站不作额外断言。", REGIONAL_NOTE)
                    brief["chars"] = len(brief["text"])
                if brief["tier"] not in {"llm", "original", "mt"} or qa_draft_simp(brief["text"], brief["source"], pack):
                    summary["skipped"] += 1
                    summary["draft_rejections"] += 1
                    if llm is not None:
                        summary["errors"].append("llm_draft_rejected")
                    log.warning("draft_rejected")
                    continue
                briefs.append((brief, pack))
                seen_urls.add(pack.url)

        summary["briefs"] = len(briefs)
        summary["tiers"] = [b["tier"] for b, _ in briefs]
        preview = dict(history)
        preview[date] = merge_drafts(history[date], _drafts_from_briefs(briefs))
        articles = sitegen.articles_from_drafts(preview)
        files = sitegen.render_site(articles, base_url)
        summary["site_files"] = len(files)
        summary["historical_articles"] = sum(len(v) for v in history.values())
        summary["safety_issues"] = sitegen.check_rendered_safety(files)
        _finalize_summary(summary)
        log.info("pipeline_checked")
        if dry_run or summary["errors"] or summary["safety_issues"]:
            return _finalize_summary(summary)
        _write_briefs_as_drafts(date, briefs)
        result = sitegen.generate_site(_all_draft_dates(date), out_dir, base_url)
        if result["safety_issues"]:
            summary["safety_issues"] = result["safety_issues"]
            return _finalize_summary(summary)
        atomic_json(Path(sitegen.STATE_DIR) / "runs" / f"{date}.json", summary)
        return _finalize_summary(summary)
    except (StateError, SafetyError):
        summary["errors"] = ["validation_or_state_failed"]
        log.warning("validation_or_state_failed")
    except Exception:
        summary["errors"] = ["pipeline_failed"]
        log.warning("pipeline_failed")
    return _finalize_summary(summary)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "argument_error\n")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s", force=True)
    ap = _SafeParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", default="site")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--landing-only", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="read-only research, QA and in-memory site validation")
    args = ap.parse_args(argv)
    summary = run_pipeline(args.date, args.out, args.base_url,
                           landing_only=args.landing_only, use_llm=args.llm, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 1 if summary["status"] == "failed" or summary["errors"] or summary["safety_issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
