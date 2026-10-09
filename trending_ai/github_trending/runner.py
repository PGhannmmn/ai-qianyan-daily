"""Research -> source QA -> editorial -> dedup -> optional PRIVATE staging."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .editorial import make_draft
from .history import History, HistoryError, load_external_history
from .sources import FetchError, GitHubSource, SourceError


def run(source=None, *, history_path="work/github-trending.sqlite3", now=None,
        period="daily", locale="zh-CN", style="bilingual", translator=None,
        limit=1, candidate_limit=8, stage=False, external_history=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone-aware observation required")
    now = now.astimezone(timezone.utc)
    if not 1 <= limit <= 3 or not limit <= candidate_limit <= 25:
        raise ValueError("invalid selection limits")
    source = source or GitHubSource()
    history = History(history_path)
    ids, urls = history.seen()
    external_ids, external_urls = load_external_history(external_history)
    ids |= external_ids
    urls |= external_urls
    # Get history BEFORE spending requests or risking an empty corrupt ledger.
    trends = source.trending(period, now)
    drafts, rejected = [], []
    for trend in trends[:candidate_limit]:
        if "https://github.com/" + trend.name.lower() in urls:
            rejected.append({"repo": trend.name, "reason": "already-seen"})
            continue
        try:
            project = source.project(trend, now)
            if project.repo_id in ids or project.url.lower() in urls:
                rejected.append({"repo": trend.name, "reason": "already-seen"})
                continue
            draft = make_draft(project, now, locale, style, translator)
        except FetchError:
            raise
        except (SourceError, ValueError):
            # Source text and arbitrary provider exceptions never enter logs.
            rejected.append({"repo": trend.name, "reason": "source-or-editorial-QA"})
            continue
        except Exception:
            rejected.append({"repo": trend.name, "reason": "provider-failed"})
            continue
        drafts.append(draft)
        ids.add(project.repo_id)
        urls.add(project.url.lower())
        if len(drafts) >= limit:
            break
    if stage:
        drafts = history.stage(drafts)
    outcome = "drafts-ready" if drafts else "no-new-qualified-projects"
    return {"schema_version": 1, "series": "github_trending", "dry_run": not stage,
            "mode": "private-stage" if stage else "dry-run", "period": period,
            "observed_at": now.isoformat(), "selected": len(drafts), "outcome": outcome,
            "drafts": drafts, "rejected": rejected, "publication_allowed": False,
            "external_history_loaded": external_history is not None,
            "threads_integration_verified": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Independent GitHub Trending private draft pipeline")
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="default: research and QA; zero writes")
    modes.add_argument("--stage", action="store_true", help="reserve private review drafts in local SQLite only")
    ap.add_argument("--period", choices=("daily", "weekly", "monthly"), default="daily")
    ap.add_argument("--locale", choices=("zh-CN", "zh-TW"), default="zh-CN")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--candidate-limit", type=int, default=8)
    ap.add_argument("--history", default="work/github-trending.sqlite3")
    ap.add_argument("--external-history", help="explicit schema-version-1 published-project export")
    ap.add_argument("--show-drafts", action="store_true", help="include prose on stdout; private local use only")
    args = ap.parse_args(argv)
    try:
        result = run(history_path=args.history, period=args.period, locale=args.locale,
                     limit=args.limit, candidate_limit=args.candidate_limit,
                     stage=args.stage, external_history=args.external_history)
    except (HistoryError, SourceError, ValueError):
        print(json.dumps({"series": "github_trending", "error": "research-or-history-failed",
                          "publication_allowed": False}))
        return 1
    if not args.show_drafts:
        # Public Actions logs never receive private draft prose by default.
        rejected_count = len(result["rejected"])
        result = {k: v for k, v in result.items() if k not in ("drafts", "rejected")}
        result["rejected_count"] = rejected_count
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
