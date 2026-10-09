"""Static-site generator for AI 前沿情报局 — DRY_RUN ONLY.

Builds a standalone Simplified Chinese AI news website from the brand's
*validated* drafts (state/simp_drafts_<date>.json, which already passed the
editorial QA pipeline) plus the shared verified fact-packs (original
publication dates, real source URLs).

Output (all static, no build step needed on the host):
  index.html            — homepage, latest articles
  archives.html         — full archive index
  articles/<date>-<n>.html — one page per article
  about.html            — brand info + AI disclosure policy
  sitemap.xml           — for search engines
  feed.xml              — RSS 2.0
  robots.txt

Every page carries:
  - the AI-generated-content disclosure (per 2025-09-01 labeling rules),
  - the verified source name + real working source URL,
  - the source's ORIGINAL publication date (from the fact-pack).

DRY_RUN: generate_site() builds into a local directory only. The deploy()
function exists for documentation but raises — no repository is created,
no website is deployed, nothing is published until explicitly authorized.

Publication records: record_site_publication() writes to the brand's OWN
state/site_publications.jsonl — fully independent from the Simplified
Threads account's records and from the Traditional publisher.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape

log = logging.getLogger("simp_publisher.sitegen")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # src/simp_publisher
STATE_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "state"))
SHARED_STATE = os.path.expanduser(
    "~/workspace/orbisignal-media/threads-publisher/state")

BRAND_NAME = "AI 前沿情报局"
AI_DISCLOSURE_SHORT = "本文由 AI 辅助撰写"
AI_DISCLOSURE_FULL = (
    "［AI 生成内容标识］本站内容由 AI 辅助撰写，经编辑流程校验；"
    "事实均引自文内所列来源，区域可用性说明以来源原文为准。"
)

SECRET_PATTERNS = re.compile(
    r"hsurr:|Bearer\s+[A-Za-z0-9_\-]{16,}|sk-ant-|xox[bap]-|"
    r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", re.IGNORECASE)


def _h(text: str) -> str:
    return html.escape(text, quote=True)


def _para(text: str) -> str:
    """Draft text -> escaped HTML paragraphs."""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return "".join(f"<p>{_h(p)}</p>" for p in parts)


def _slug(date: str, index: int) -> str:
    return f"{date}-{index + 1}"


def load_articles(dates: List[str]) -> List[Dict[str, Any]]:
    """Load validated drafts + fact-pack metadata for the given dates."""
    articles: List[Dict[str, Any]] = []
    for date in dates:
        drafts_path = os.path.join(STATE_DIR, f"simp_drafts_{date}.json")
        packs_path = os.path.join(SHARED_STATE, f"news_factpacks_{date}.json")
        if not os.path.exists(drafts_path):
            log.warning("no drafts for %s — skipping", date)
            continue
        with open(drafts_path, encoding="utf-8") as fh:
            drafts = json.load(fh)["drafts"]
        packs: List[Dict[str, Any]] = []
        if os.path.exists(packs_path):
            with open(packs_path, encoding="utf-8") as fh:
                packs = json.load(fh)["packs"]
        for i, d in enumerate(drafts):
            pack = packs[d.get("fact_index", 0)] if packs else {}
            title = d["text"].split("\n")[0].strip("［］[]")[:60] or f"{BRAND_NAME}快讯"
            articles.append({
                "slug": _slug(date, i),
                "date": date,
                "title": title,
                "text": d["text"],
                "source": d["source"],
                # draft-level metadata wins (standalone/Actions mode);
                # falls back to the shared fact-pack file (dual-brand mode)
                "url": d.get("url") or pack.get("url", ""),
                "published_at": d.get("published_at") or pack.get("published_at", ""),
                "source_name": d.get("source_name") or pack.get("source_name", d["source"]),
            })
    articles.sort(key=lambda a: (a["date"], a["slug"]), reverse=True)
    return articles


_PAGE_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | {brand}</title>
<meta name="description" content="{desc}">
</head>
<body>
<header><h1><a href="{base}/">{brand}</a></h1>
<nav><a href="{base}/">首页</a> | <a href="{base}/archives.html">归档</a> | <a href="{base}/about.html">关于</a> | <a href="{base}/feed.xml">RSS</a></nav></header>
<main>
{body}
</main>
<footer>
<hr>
<p>{disclosure}</p>
<p>{brand} · 内容由 AI 辅助生成 · 事实引自文内来源</p>
</footer>
</body>
</html>
"""


def _page(title: str, desc: str, body: str, base_url: str) -> str:
    return _PAGE_TMPL.format(
        title=_h(title), desc=_h(desc[:150]), body=body,
        base=_h(base_url.rstrip("/")), brand=_h(BRAND_NAME),
        disclosure=_h(AI_DISCLOSURE_FULL))


def render_index(articles: List[Dict[str, Any]], base_url: str) -> str:
    intro = (
        f"<p>{_h(BRAND_NAME)}是中文 AI 行业快讯存档站，"
        "每日摘编经编辑流程校验的 AI 新闻摘要。</p>"
        f"<p>{_h(AI_DISCLOSURE_FULL)}</p>"
    )
    items = []
    for a in articles[:20]:
        items.append(
            f'<article><h2><a href="{_h(base_url)}/articles/{a["slug"]}.html">'
            f'{_h(a["title"])}</a></h2>'
            f'<p><time>{_h(a["date"])}</time> · 来源：{_h(a["source_name"])}</p>'
            f'<p>{_h(AI_DISCLOSURE_SHORT)}</p></article>')
    if items:
        body = intro + "<h2>最新快讯</h2>\n" + "\n".join(items)
    else:
        body = intro + "<h2>最新快讯</h2><p>内容准备中，首批快讯即将发布。</p>"
    return _page("首页", f"{BRAND_NAME} AI 快讯", body, base_url)


def render_article(a: Dict[str, Any], base_url: str) -> str:
    src_link = (f'<a href="{_h(a["url"])}" rel="nofollow noopener">{_h(a["url"])}</a>'
                if a["url"] else "（来源链接缺失）")
    pub = a["published_at"][:10] if a["published_at"] else a["date"]
    body = (
        f"<article><h2>{_h(a['title'])}</h2>"
        f"<p><time datetime=\"{_h(a['date'])}\">本站发布：{_h(a['date'])}</time> ｜ "
        f"来源原文发布：{_h(pub)}</p>"
        f"<p><strong>{_h(AI_DISCLOSURE_SHORT)}</strong></p>"
        f"{_para(a['text'])}"
        f"<h3>来源</h3><p>{_h(a['source_name'])}</p><p>{src_link}</p>"
        f"</article>"
    )
    return _page(a["title"], a["title"], body, base_url)


def render_archives(articles: List[Dict[str, Any]], base_url: str) -> str:
    items = [
        f'<li><a href="{_h(base_url)}/articles/{a["slug"]}.html">{_h(a["title"])}</a> '
        f'（{_h(a["date"])}）</li>' for a in articles]
    body = "<h2>归档</h2><ul>\n" + "\n".join(items) + "\n</ul>" if items else "<p>暂无内容。</p>"
    return _page("归档", f"{BRAND_NAME}归档", body, base_url)


def render_about(base_url: str) -> str:
    body = (
        f"<h2>关于{BRAND_NAME}</h2>"
        f"<p>{_h(AI_DISCLOSURE_FULL)}</p>"
        "<p>本站为 AI 辅助生成的中文 AI 行业快讯存档。每条快讯均注明可验证的来源"
        "名称与原文链接，并标注来源原文的发布日期；区域可用性说明以来源原文为准，"
        "本站不暗示未证实的可用性，亦不建议绕过任何区域限制。</p>"
        "<h3>内容政策</h3><ul>"
        "<li>只发布通过编辑 QA 的原创摘要，不复制第三方全文。</li>"
        "<li>每条事实可在来源中找到依据；无法验证的内容整条舍弃。</li>"
        "<li>所有页面均带 AI 生成内容标识。</li></ul>"
    )
    return _page("关于", f"关于{BRAND_NAME}", body, base_url)


def render_sitemap(pages: List[str], base_url: str) -> str:
    base = base_url.rstrip("/")
    urls = "\n".join(
        f"  <url><loc>{xml_escape(base + p)}</loc></url>" for p in pages)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'{urls}\n</urlset>\n')


def render_rss(articles: List[Dict[str, Any]], base_url: str) -> str:
    base = base_url.rstrip("/")
    items = []
    for a in articles[:30]:
        items.append(
            f"  <item><title>{xml_escape(a['title'])}</title>"
            f"<link>{xml_escape(base)}/articles/{a['slug']}.html</link>"
            f"<guid>{xml_escape(base)}/articles/{a['slug']}.html</guid>"
            f"<pubDate>{xml_escape(a['date'])}</pubDate>"
            f"<description>{xml_escape(AI_DISCLOSURE_SHORT + '：' + a['text'][:200])}</description>"
            f"</item>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<rss version="2.0"><channel>'
            f"<title>{xml_escape(BRAND_NAME)}</title>"
            f"<link>{xml_escape(base)}/</link>"
            f"<description>{xml_escape(BRAND_NAME + ' AI 快讯存档')}</description>"
            f"<language>zh-CN</language>\n" + "\n".join(items) +
            "\n</channel></rss>\n")


def render_robots(base_url: str) -> str:
    return (f"User-agent: *\nAllow: /\n"
            f"Sitemap: {base_url.rstrip('/')}/sitemap.xml\n")


def check_output_safety(out_dir: str) -> List[str]:
    """Task 7: scan generated files for secrets / safety violations."""
    issues: List[str] = []
    for root, _, files in os.walk(out_dir):
        for fn in files:
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8") as fh:
                    content = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            if SECRET_PATTERNS.search(content):
                issues.append(f"{path}: possible secret material")
            if "AI 生成内容标识" not in content and fn.endswith(".html"):
                issues.append(f"{path}: missing AI disclosure")
    return issues


def record_site_publication(entries: List[Dict[str, Any]]) -> str:
    """Append website publication records to the brand's OWN log."""
    os.makedirs(STATE_DIR, exist_ok=True)
    log_path = os.path.join(STATE_DIR, "site_publications.jsonl")
    with open(log_path, "a", encoding="utf-8") as fh:
        for e in entries:
            rec = {"ts": datetime.now(timezone.utc).isoformat(),
                   "channel": "website", **e}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return log_path


def generate_site(dates: List[str], out_dir: str, base_url: str,
                  dry_run: bool = True) -> Dict[str, Any]:
    """Build the static site locally. DRY_RUN: no deploy, no records."""
    if not dry_run:
        raise RuntimeError(
            "Site deploy is not enabled: no repository exists, no website "
            "is deployed, and public publishing needs explicit user approval."
        )
    articles = load_articles(dates)
    os.makedirs(os.path.join(out_dir, "articles"), exist_ok=True)
    pages = ["/", "/archives.html", "/about.html"]
    files: Dict[str, str] = {
        "index.html": render_index(articles, base_url),
        "archives.html": render_archives(articles, base_url),
        "about.html": render_about(base_url),
    }
    for a in articles:
        rel = f"articles/{a['slug']}.html"
        files[rel] = render_article(a, base_url)
        pages.append("/" + rel)
    files["sitemap.xml"] = render_sitemap(pages, base_url)
    files["feed.xml"] = render_rss(articles, base_url)
    files["robots.txt"] = render_robots(base_url)
    for rel, content in files.items():
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(content)
    safety = check_output_safety(out_dir)
    log.info("site built: %d articles, %d files, safety issues: %d",
             len(articles), len(files), len(safety))
    return {"articles": len(articles), "files": sorted(files),
            "safety_issues": safety, "network_calls": 0,
            "deployed": False}


def deploy(out_dir: str) -> None:
    """NOT AUTHORIZED — documents the future deploy step only."""
    raise RuntimeError(
        "Deploy refused: creating the repository and publishing the site "
        "requires the user's explicit approval (one-time actions documented "
        "in SITE_PLAN.md)."
    )
