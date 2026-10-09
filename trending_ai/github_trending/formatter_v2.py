"""Pure formatting and local QA for GitHub Trending Threads drafts.

No Threads or GitHub network calls. No publication, state mutation or scheduling.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

MAX_LENGTH = 500
SOFT_LENGTH = 450


def utf16_len(text: str) -> int:
    """Conservative length in UTF-16 code units.

    The Threads API counts characters like JavaScript (UTF-16 code units),
    where astral-plane characters (emoji, etc.) count as 2. Python's len()
    counts code points (astral = 1), so it can UNDERCOUNT. Use this for
    validation to avoid exceeding the platform limit.
    """
    return len(text.encode("utf-16-le")) // 2
PUBLIC_FORBIDDEN = ("發佈前需人工審核", "發布前需人工審核", "DRY_RUN", "僅供內部", "待人工審核")
REPO_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class TrendingRepo:
    owner: str
    name: str
    repo_id: int
    trending_date: str
    trending_rank: int
    daily_star_gain: int
    language: str
    license: str
    official_description: str
    zh_summary: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "TrendingRepo":
        return cls(**data)

    def validate(self) -> None:
        for component in (self.owner, self.name):
            if not component or not REPO_PART.fullmatch(component) or component in (".", ".."):
                raise ValueError("Invalid GitHub owner/repo path")
        if not isinstance(self.repo_id, int) or isinstance(self.repo_id, bool) or self.repo_id <= 0:
            raise ValueError("Invalid repo_id")
        if not isinstance(self.trending_rank, int) or not 1 <= self.trending_rank <= 100:
            raise ValueError("Invalid trending_rank")
        if not isinstance(self.daily_star_gain, int) or self.daily_star_gain < 0:
            raise ValueError("Invalid daily_star_gain")
        if date.fromisoformat(self.trending_date).isoformat() != self.trending_date:
            raise ValueError("Use YYYY-MM-DD trending_date")
        for s in (self.language, self.license, self.official_description, self.zh_summary):
            if not isinstance(s, str) or any(x in s for x in ("\r", "\n", "\x00")):
                raise ValueError("Metadata must be single-line strings")
        if not self.official_description.strip():
            raise ValueError("Official description required")

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def dedup_key(self) -> str:
        return f"github:repo:{self.repo_id}"


def format_post(repo: TrendingRepo) -> str:
    """Create a publish-shaped *draft*, without any publish capability."""
    repo.validate()
    summary = repo.zh_summary.strip()
    if not summary:
        summary = f"官方簡介：{repo.official_description.strip()}"
    header = f"🔥 GitHub 今日熱門 #{repo.trending_rank}｜{repo.name}"
    # Each datum must be backed by the caller's source snapshot.
    tail = (
        f"⭐ Trending 日榜新增 +{repo.daily_star_gain:,} Stars\n"
        f"🛠 {repo.language or '未標示'}｜{repo.license or '未標示'}\n"
        f"📅 {repo.trending_date}（Toronto）\n"
        f"🔗 {repo.url}"
    )
    content = f"{header}\n\n{summary}\n\n{tail}"
    validate_post(content, repo)
    return content


def validate_post(content: str, repo: TrendingRepo) -> None:
    """Local structural validation only; cannot certify editorial truth or provenance."""
    # Conservative: validate with UTF-16 code units (platform counting).
    ulen = utf16_len(content)
    if ulen > MAX_LENGTH:
        raise ValueError(
            f"Post UTF-16 length {ulen} exceeds {MAX_LENGTH}")
    if any(token in content for token in PUBLIC_FORBIDDEN):
        raise ValueError("Internal instruction leaked into public-facing draft")
    if content.count(repo.url) != 1:
        raise ValueError("Expected exactly one canonical GitHub source link")
    parsed = urlparse(repo.url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise ValueError("Invalid source URL")
    if re.search(r"https?://", content.replace(repo.url, "")):
        raise ValueError("Unverified additional link")
    if any(ord(ch) < 32 and ch != "\n" for ch in content):
        raise ValueError("Unexpected control character")


def generate_json(data: dict) -> dict:
    repo = TrendingRepo.from_dict(data)
    post = format_post(repo)
    return {
        "post": post,
        "char_count": len(post),
        "utf16_char_count": utf16_len(post),
        "soft_limit_warning": utf16_len(post) > SOFT_LENGTH,
        "dedup_key": repo.dedup_key,
        "publication_performed": False,
        "source_link_format_checked": True,
        "source_live_verified": False,
        "editorial_claims_verified": False,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python formatter.py fixture.json", file=sys.stderr)
        return 2
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(generate_json(data), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
