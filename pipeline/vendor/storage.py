"""Read and append validated drafts without replacing prior evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
from uuid import uuid4

from .safety import SafetyError, source_url
from .simp_editorial import validate_saved_draft

MAX_STATE_BYTES = 2 * 1024 * 1024


class StateError(RuntimeError):
    pass


def valid_date(value: str) -> str:
    try:
        if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise StateError("invalid_date") from None
    return value


def read_drafts(state_dir, day: str) -> dict:
    path = Path(state_dir) / f"simp_drafts_{valid_date(day)}.json"
    try:
        if Path(state_dir).is_symlink() or path.is_symlink():
            raise StateError("state_symlink_rejected")
        if not path.exists():
            return {"drafts": [], "assembled": True, "tiers": []}
        if path.stat().st_size > MAX_STATE_BYTES:
            raise StateError("state_too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("assembled") is not True:
            raise StateError("invalid_state")
        drafts = data.get("drafts")
        if not isinstance(drafts, list) or len(drafts) > 500:
            raise StateError("invalid_state")
        tiers = data.get("tiers", [])
        if not isinstance(tiers, list) or (tiers and len(tiers) != len(drafts)):
            raise StateError("invalid_state")
        for draft in drafts:
            validate_saved_draft(draft)
        return data
    except StateError:
        raise
    except Exception:
        raise StateError("invalid_state") from None


def merge_drafts(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged = deepcopy(existing)
    seen = set()
    for draft in existing:
        validate_saved_draft(draft)
        seen.add(source_url(draft["url"]))
    for draft in incoming:
        validate_saved_draft(draft)
        url = source_url(draft["url"])
        if url not in seen:
            merged.append(deepcopy(draft))
            seen.add(url)
    return merged


def atomic_json(path, data) -> None:
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise StateError("state_symlink_rejected")
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        raise StateError("state_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp-" + uuid4().hex)
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        raise StateError("state_write_failed") from None
    finally:
        if tmp.exists():
            tmp.unlink()


def append_drafts(state_dir, day: str, incoming: list[dict]) -> bool:
    existing = read_drafts(state_dir, day)
    merged = merge_drafts(existing["drafts"], incoming)
    if merged == existing["drafts"]:
        return False  # Zero new validated drafts: do not touch even the mtime.
    tiers = existing.get("tiers") or [d.get("tier", "original") for d in existing["drafts"]]
    updated = dict(existing, drafts=merged,
                   tiers=tiers + [d.get("tier", "original") for d in merged[len(existing["drafts"]):]])
    atomic_json(Path(state_dir) / f"simp_drafts_{valid_date(day)}.json", updated)
    return True
