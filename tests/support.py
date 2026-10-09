from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import shutil
from uuid import uuid4
from unittest.mock import patch

from pipeline import daily
from pipeline.vendor import research as R

REPO = Path(__file__).resolve().parents[1]
SCRATCH = REPO / ".test-sandboxes"
NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
DAY = "2026-10-08"
BASE = "https://pghannmmn.github.io/ai-qianyan-daily/"
ACCOUNT = "a" * 32
TOKEN = "SYNTHETIC_OFFLINE_SECRET_123456789"
TITLE = "Google announces Gemini AI agents"
ARTICLE = "Google announced Gemini AI agents that use business context to help companies complete multistep tasks. Gemini supports enterprise knowledge retrieval and content creation."
SUMMARY = "Google 发布 Gemini 智能体，帮助企业理解业务上下文并执行多步骤工作。"


def item(index=0, **changes):
    return replace(R.FeedItem(TITLE, f"https://blog.google/ai/fixture-{index}",
                             "Gemini AI news", NOW, "Google Blog AI"), **changes)


def pack(index=0, **changes):
    return replace(R.build_fact_pack(item(index), ARTICLE), **changes)


class MockLLM:
    api_token = TOKEN
    account_id = ACCOUNT
    def __init__(self, text=SUMMARY):
        self.text = text
        self.calls = []
    def summarize(self, fields):
        self.calls.append(fields)
        return self.text


def brief(index=0):
    p = pack(index)
    return daily._llm_brief(MockLLM(), p, DAY), p


def saved(index=0):
    return daily._drafts_from_briefs([brief(index)])[0]


@contextmanager
def sandbox():
    SCRATCH.mkdir(exist_ok=True)
    path = SCRATCH / uuid4().hex
    path.mkdir()
    try:
        with patch.object(daily.sitegen, "STATE_DIR", str(path / "state")):
            yield path
    finally:
        # Resolve before removing only this test-created directory.
        if path.resolve().parent != SCRATCH.resolve():
            raise RuntimeError("invalid_test_cleanup_path")
        shutil.rmtree(path)


def snapshot(path):
    return {str(p.relative_to(path)): (sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in path.rglob("*") if p.is_file()}


def run(path, items=None, llm=None, **kwargs):
    options = dict(fetch=lambda source: list(items if items is not None else [item()]),
                   article_fetch=lambda url: ARTICLE, llm_factory=lambda: llm or MockLLM(),
                   use_llm=True, dry_run=True, now=NOW)
    options.update(kwargs)
    return daily.run_pipeline(DAY, str(path / "site"), BASE, **options)
