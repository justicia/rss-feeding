"""Fetch recent classical-music articles and build a static Chinese digest."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


HISTORY_PATH = Path("history.json")
OUTPUT_PATH = Path("index.html")
RECENT_DAYS = int(os.getenv("RECENT_DAYS", "7"))
MAX_PER_SOURCE = int(os.getenv("MAX_PER_SOURCE", "5"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "300"))
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    host: str
    path_hint: str = ""


SOURCES = (
    Source("BackstageClassical", "https://backstageclassical.com/", "backstageclassical.com"),
    Source("Slipped Disc", "https://slippedisc.com/", "slippedisc.com"),
    Source("Scherzo", "https://scherzo.es/noticias/criticas/", "scherzo.es"),
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ClassicalDaily/1.0; +https://github.com/)",
    "Accept-Language": "en-US,en;q=0.8,es;q=0.7,de;q=0.6",
})


def get_soup(url: str, attempts: int = 3) -> BeautifulSoup:
    last_error = None
    for attempt in range(attempts):
        try:
            response = SESSION.get(url, timeout=(10, 30))
            response.raise_for_status()
            return BeautifulSoup(response.content, "html.parser")
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"无法读取 {url}: {last_error}") from last_error


def canonical_url(base: str, href: str) -> str:
    parts = urlsplit(urljoin(base, href))
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            parsed = None
    if parsed is None:
        for fmt in ("%d/%m/%Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def published_at(soup: BeautifulSoup) -> datetime | None:
    selectors = (
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="date"]', "content"),
        ('meta[name="publish-date"]', "content"),
        ("time[datetime]", "datetime"),
    )
    for selector, attribute in selectors:
        node = soup.select_one(selector)
        if node:
            result = parse_date(node.get(attribute))
            if result:
                return result
    return None


def article_text(soup: BeautifulSoup) -> str:
    container = soup.select_one(
        "article, .entry-content, .post-content, .td-post-content, .article-content, main"
    ) or soup
    for node in container.select("script, style, nav, aside, footer, form, .sharedaddy, .advertisement"):
        node.decompose()
    parts = []
    for paragraph in container.select("p"):
        text = " ".join(paragraph.get_text(" ", strip=True).split())
        if len(text) >= 50:
            parts.append(text)
    return "\n".join(dict.fromkeys(parts))[:12000]


def candidate_links(source: Source, soup: BeautifulSoup) -> list[tuple[str, str]]:
    selectors = {
        "BackstageClassical": "article h2 a, article h3 a, h2 a, h3 a",
        "Slipped Disc": "article h2 a, article h3 a, .entry-title a, h2 a, h3 a",
        "Scherzo": ".cards-article__item a:has(h2.post-title)",
    }
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in soup.select(selectors[source.name]):
        title = " ".join(link.get_text(" ", strip=True).split())
        href = link.get("href")
        if not href or len(title) < 12:
            continue
        url = canonical_url(source.url, href)
        parts = urlsplit(url)
        if parts.hostname not in {source.host, f"www.{source.host}"}:
            continue
        if source.path_hint and source.path_hint not in parts.path:
            continue
        if any(piece in parts.path.lower() for piece in ("/author/", "/tag/", "/category/", "/page/")):
            continue
        if url not in seen:
            seen.add(url)
            found.append((title, url))
    return found


def summarize(client: OpenAI, title: str, text: str) -> str:
    response = client.responses.create(
        model=MODEL,
        input=(
            "你是一名严谨的古典音乐新闻编辑。根据下列文章写一段100至180字的简体中文摘要。"
            "只使用原文信息，准确保留人名、作品名、乐团和音乐节名称；新闻优先交代事件及意义，"
            "评论优先概括对演出、指挥、歌手、乐团或制作的评价。不要翻译标题，不要使用套话，"
            "只输出摘要正文。\n\n"
            f"标题：{title}\n\n正文：{text}"
        ),
    )
    return response.output_text.strip()


def load_history(path: Path = HISTORY_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"警告：无法读取历史文件，将从空历史开始：{exc}")
        return {}
    records = data.get("articles", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        return {}
    return {canonical_url(item.get("url", ""), ""): item for item in records if item.get("url")}


def save_history(articles: Iterable[dict], path: Path = HISTORY_PATH) -> None:
    ordered = sorted(articles, key=lambda item: item.get("published_at", ""), reverse=True)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "articles": ordered[:MAX_HISTORY]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_source(source: Source, history: dict[str, dict], client: OpenAI, now: datetime) -> list[dict]:
    print(f"\n抓取 {source.name}")
    homepage = get_soup(source.url)
    cutoff = now - timedelta(days=RECENT_DAYS)
    fresh: list[dict] = []
    for title, url in candidate_links(source, homepage):
        if len(fresh) >= MAX_PER_SOURCE:
            break
        if url in history:
            continue  # The key cost-saving rule: never fetch/summarize an archived URL again.
        try:
            soup = get_soup(url)
            published = published_at(soup)
            if published is None:
                print(f"  跳过（没有可靠日期）：{title}")
                continue
            if published < cutoff or published > now + timedelta(days=1):
                continue
            text = article_text(soup)
            if len(text) < 250:
                print(f"  跳过（正文过短）：{title}")
                continue
            print(f"  摘要：{title}")
            fresh.append({
                "source": source.name,
                "title": title,
                "summary": summarize(client, title, text),
                "url": url,
                "published_at": published.isoformat(),
            })
        except Exception as exc:  # One broken article must not abort the daily digest.
            print(f"  失败：{title} ({exc})")
    return fresh


def generate_html(articles: Iterable[dict], output: Path = OUTPUT_PATH) -> None:
    items = sorted(articles, key=lambda item: item.get("published_at", ""), reverse=True)
    cards = []
    for item in items:
        date = parse_date(item.get("published_at"))
        date_label = date.strftime("%Y-%m-%d") if date else "日期未知"
        cards.append(
            '<article class="card">'
            f'<div class="meta"><span>{escape(item["source"])}</span><time>{date_label}</time></div>'
            f'<h2><a href="{escape(item["url"], quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'{escape(item["title"])}</a></h2>'
            f'<p>{escape(item["summary"])}</p>'
            f'<a class="read" href="{escape(item["url"], quote=True)}" target="_blank" '
            'rel="noopener noreferrer">阅读原文 →</a></article>'
        )
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    empty = '<p class="empty">最近还没有收录新文章。</p>'
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="每日古典音乐新闻中文摘要"><title>My Classical Daily</title>
<style>
:root{{--ink:#182018;--muted:#667066;--paper:#f5f3ec;--card:#fff;--accent:#315c43;--line:#dedbd1}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.75}}
header,main,footer{{width:min(920px,calc(100% - 36px));margin:auto}} header{{padding:64px 0 34px;border-bottom:1px solid var(--line)}}
h1{{font-family:Georgia,"Noto Serif SC",serif;font-size:clamp(2.2rem,7vw,4.5rem);line-height:1;margin:0 0 16px;letter-spacing:-.04em}}
.intro,.meta,footer{{color:var(--muted)}} main{{display:grid;gap:18px;padding:32px 0 56px}} .card{{background:var(--card);padding:26px 28px;border:1px solid var(--line);border-radius:14px;box-shadow:0 4px 18px #2430240a}}
.meta{{display:flex;justify-content:space-between;gap:12px;font-size:.82rem;text-transform:uppercase;letter-spacing:.08em}} h2{{font-family:Georgia,"Noto Serif SC",serif;font-size:clamp(1.25rem,3vw,1.65rem);line-height:1.35;margin:12px 0}}
h2 a{{color:inherit;text-decoration:none}} h2 a:hover{{color:var(--accent)}} .card p{{margin:0 0 16px}} .read{{color:var(--accent);font-weight:650;text-decoration:none}} footer{{padding:22px 0 40px;border-top:1px solid var(--line);font-size:.85rem}} .empty{{text-align:center;padding:60px}}
@media(max-width:560px){{header{{padding-top:42px}}.card{{padding:21px}}}}
</style></head><body><header><h1>My Classical Daily</h1><p class="intro">来自欧洲古典音乐媒体的近期报道，以中文简要呈现。</p></header>
<main>{''.join(cards) if cards else empty}</main><footer>最后更新：{updated} · 共 {len(items)} 篇</footer></body></html>'''
    output.write_text(html, encoding="utf-8")


def main() -> None:
    now = datetime.now(timezone.utc)
    history = load_history()
    client = OpenAI()
    new_articles: list[dict] = []
    for source in SOURCES:
        try:
            new_articles.extend(fetch_source(source, history, client, now))
        except Exception as exc:
            print(f"来源失败但继续运行：{source.name} ({exc})")
    for item in new_articles:
        history[item["url"]] = item
    display_cutoff = now - timedelta(days=30)
    display = [item for item in history.values() if (parse_date(item.get("published_at")) or now) >= display_cutoff]
    save_history(history.values())
    generate_html(display)
    print(f"\n完成：新增 {len(new_articles)} 篇，页面展示 {len(display)} 篇。")


if __name__ == "__main__":
    main()
