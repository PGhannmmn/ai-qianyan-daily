"""Repository-local static rendering and local builds.

render_site() is pure and powers CLI --dry-run. generate_site() writes a
local directory only after safety screening. No deployment, publication
record writer or dependency on another publisher workspace is present.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape
from urllib.parse import urlsplit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "state"))
from .safety import contains_injection, contains_sensitive, public_base_url
from .storage import read_drafts, valid_date
from .simp_editorial import validate_saved_draft

BRAND_NAME = "AI 前沿情报局"
CUSTOM_DOMAIN = "orbisignalmedia.duckdns.org"
AI_DISCLOSURE_SHORT = "本文由 AI 辅助撰写"
AI_DISCLOSURE_FULL = (
    "［AI 生成内容标识］本站内容由 AI 辅助撰写，经编辑流程校验；"
    "事实均引自文内所列来源，区域可用性说明以来源原文为准。"
)

def _h(text: str) -> str:
    return html.escape(text, quote=True)


def _para(text: str) -> str:
    """Draft text -> escaped HTML paragraphs."""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return "".join(f"<p>{_h(p)}</p>" for p in parts)


def _slug(date: str, index: int) -> str:
    return f"{date}-{index + 1}"


def load_articles(dates: List[str]) -> List[Dict[str, Any]]:
    """Load source metadata solely from repository-local validated drafts."""
    return articles_from_drafts({date: read_drafts(STATE_DIR, date)["drafts"] for date in dates})


def articles_from_drafts(by_date: dict) -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    for date, drafts in by_date.items():
        valid_date(date)
        for i, d in enumerate(drafts):
            validate_saved_draft(d)
            title = d["text"].split("\n")[0].strip("［］[]")[:60] or f"{BRAND_NAME}快讯"
            articles.append({
                "slug": _slug(date, i),
                "date": date,
                "title": title,
                "text": d["text"],
                "source": d["source"],
                "url": d["url"],
                "published_at": d["published_at"],
                "source_name": d.get("source_name", d["source"]),
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
            f"<pubDate>{format_datetime(datetime.fromisoformat(a['date']).replace(tzinfo=timezone.utc), usegmt=True)}</pubDate>"
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


def check_rendered_safety(files: Dict[str, str]) -> List[str]:
    issues = []
    for name, content in files.items():
        if contains_sensitive(content) or contains_injection(content):
            issues.append("unsafe_rendered_content")
        if name.endswith(".html") and "AI 生成内容标识" not in content:
            issues.append("missing_disclosure")
    return sorted(set(issues))


def check_output_safety(out_dir: str) -> List[str]:
    files = {}
    for root, _, names in os.walk(out_dir):
        for name in names:
            try:
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    files[name] = fh.read()
            except (OSError, UnicodeError):
                return ["unreadable_output"]
    return check_rendered_safety(files)


def render_site(articles: List[Dict[str, Any]], base_url: str) -> Dict[str, str]:
    """Pure in-memory renderer; no filesystem writes, records or deployment."""
    base_url = public_base_url(base_url)
    pages = ["/", "/archives.html", "/about.html"]
    files = {
        "index.html": render_index(articles, base_url),
        "archives.html": render_archives(articles, base_url),
        "about.html": render_about(base_url),
    }
    for article in articles:
        rel = f"articles/{article['slug']}.html"
        files[rel] = render_article(article, base_url)
        pages.append("/" + rel)
    files["sitemap.xml"] = render_sitemap(pages, base_url)
    files["feed.xml"] = render_rss(articles, base_url)
    files["robots.txt"] = render_robots(base_url)
    # Preserve main's custom-domain artifact for explicit local builds.
    # Dry-run previews this only in memory, including landing-only previews.
    if urlsplit(base_url).hostname == CUSTOM_DOMAIN:
        files["CNAME"] = CUSTOM_DOMAIN + "\n"
    return files


def generate_site(dates: List[str], out_dir: str, base_url: str,
                  dry_run: bool = True) -> Dict[str, Any]:
    """Local build only. CLI --dry-run uses render_site and never calls this."""
    if not dry_run:
        raise RuntimeError("deployment_disabled")
    articles = load_articles(dates)
    files = render_site(articles, base_url)
    safety = check_rendered_safety(files)
    if safety:
        return {"articles": len(articles), "files": [], "safety_issues": safety,
                "network_calls": 0, "deployed": False}
    os.makedirs(os.path.join(out_dir, "articles"), exist_ok=True)
    for rel, content in files.items():
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    return {"articles": len(articles), "files": sorted(files),
            "safety_issues": [], "network_calls": 0, "deployed": False}


def deploy(out_dir: str) -> None:
    raise RuntimeError("deployment_disabled")
