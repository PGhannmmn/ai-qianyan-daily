"""Private draft ledger; stable repo IDs and atomic reservations.

Drafts reserve a project indefinitely. Dry runs open an existing ledger read-only
and never create databases, folders, run logs, or publication markers.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .sources import SourceError, repo_name


class HistoryError(RuntimeError):
    pass


class History:
    def __init__(self, path):
        self.path = Path(path)

    def seen(self) -> tuple[set[int], set[str]]:
        if not self.path.exists():
            return set(), set()
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                rows = db.execute("SELECT repo_id, canonical_url FROM drafts").fetchall()
            return {r[0] for r in rows}, {r[1].lower() for r in rows}
        except (sqlite3.Error, OSError):
            # Never silently turn a corrupt ledger into an empty history.
            raise HistoryError("history unreadable; selection stopped") from None

    def stage(self, drafts: list[dict]) -> list[dict]:
        if not drafts:
            return []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE IF NOT EXISTS drafts (
                    repo_id INTEGER PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    observed_at TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status = 'needs_review'))""")
                staged = []
                for draft in drafts:
                    if draft.get("publication_allowed") is not False or draft.get("status") != "needs_review":
                        raise HistoryError("only private review drafts may be staged")
                    # Abort on errors except duplicate identity. Other threads
                    # racing this reservation cannot stage the same project.
                    cursor = db.execute("""INSERT INTO drafts
                        (repo_id, canonical_url, observed_at, payload, status)
                        VALUES (?, ?, ?, ?, 'needs_review')
                        ON CONFLICT DO NOTHING""", (
                        draft["repo_id"], draft["canonical_url"], draft["observed_at"],
                        json.dumps(draft, ensure_ascii=False)))
                    if cursor.rowcount:
                        staged.append(draft)
                return staged
        except (sqlite3.Error, OSError):
            raise HistoryError("private draft reservation failed") from None

    def recent_hooks(self) -> list[str]:
        """Read recent reviewed draft hooks through the existing ledger schema.

        No database creation, schema migration or production-state writes.
        """
        if not self.path.exists():
            return []
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                rows = db.execute("SELECT payload FROM drafts ORDER BY observed_at DESC LIMIT 12").fetchall()
            hooks = []
            for row in rows:
                value = json.loads(row[0])
                hook = value.get("editorial_story", {}).get("hook")
                if hook is not None:
                    if not isinstance(hook, str) or not 1 <= len(hook) <= 100:
                        raise ValueError
                    hooks.append(hook)
            return hooks
        except (sqlite3.Error, OSError, ValueError, AttributeError, TypeError):
            raise HistoryError("hook history unreadable; selection stopped") from None


def load_external_history(path) -> tuple[set[int], set[str]]:
    """Explicit bridge export; does not guess the unavailable Muse schema."""
    if path is None:
        return set(), set()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("published"), list):
            raise ValueError
        ids, urls = set(), set()
        for item in data["published"]:
            rid, url = item.get("repo_id"), item.get("canonical_url")
            if rid is None and url is None:
                raise ValueError
            if rid is not None:
                if type(rid) is not int or rid <= 0:
                    raise ValueError
                ids.add(rid)
            if url is not None:
                if not isinstance(url, str) or not url.startswith("https://github.com/"):
                    raise ValueError
                repo_name(url[len("https://github.com/"):])
                urls.add(url.lower())
        return ids, urls
    except (OSError, ValueError, AttributeError, TypeError, SourceError):
        raise HistoryError("external publication history invalid or unreadable") from None
