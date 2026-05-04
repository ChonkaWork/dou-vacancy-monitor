#!/usr/bin/env python3
"""Djinni.co Java vacancy scraper."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

DJINNI_URL = "https://djinni.co/jobs/?search_type=basic-search&primary_keyword=Java"
DB_PATH = Path(__file__).parent / "jobs.db"


def fetch_html(url: str) -> str:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


class DjinniVacancyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, str]] = []
        self._in_job_item = False
        self._current: dict[str, str] = {}
        self._capture: str | None = None
        self._in_header_link = False
        self._in_description = False

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # Job item container: <div id="job-item-XXX" class="job-item ...">
        if tag == "div" and attrs_dict.get("id", "").startswith("job-item-"):
            self._in_job_item = True
            self._current = {"title": "", "company": "", "url": "", "description": "", "date": ""}
            return

        if not self._in_job_item:
            return

        # Header link
        if tag == "a" and "job_item__header-link" in cls:
            self._in_header_link = True
            href = attrs_dict.get("href", "")
            if href and not href.startswith("http"):
                href = "https://djinni.co" + href
            self._current["url"] = href

        # Title: <h2 class="job-item__position ...">
        if tag == "h2" and "job-item__position" in cls:
            self._capture = "title"

        # Company: <span class="small text-gray-800 opacity-75 font-weight-500">
        if tag == "span" and "text-gray-800" in cls and "font-weight-500" in cls:
            self._capture = "company"

        # Description: <div id="job-description-XXX">
        if tag == "div" and attrs_dict.get("id", "").startswith("job-description-"):
            self._in_description = True
            self._capture = "description"

        # Truncated text or full text spans
        if self._in_description and tag == "span" and ("js-truncated-text" in cls or "js-original-text" in cls):
            self._capture = "description"

    def handle_endtag(self, tag: str):
        if self._in_job_item and tag == "div":
            # Job items are divs, but we need to be careful about nested divs
            # We'll finalize when we see the description end and no more captures
            pass

        if self._capture and tag in ("h2", "span", "a"):
            self._capture = None

    def handle_data(self, data: str):
        text = data.strip()
        if not text:
            return
        if self._in_job_item and self._capture:
            prev = self._current.get(self._capture, "")
            self._current[self._capture] = f"{prev} {text}".strip() if prev else text

    def finalize(self) -> list[dict[str, str]]:
        """Extract job items from the raw HTML using regex after initial parse."""
        pass

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_djinni_regex(html: str) -> list[dict[str, str]]:
    """Parse Djinni jobs using regex for reliability."""
    items = []
    # Find all job-item divs
    job_pattern = re.compile(
        r'<div\s+id="job-item-(\d+)"[^>]*class="[^"]*job-item[^"]*"[^>]*>'
        r'(.*?)'
        r'<div\s+id="job-description-\1"',
        re.DOTALL
    )

    for m in job_pattern.finditer(html):
        job_id = m.group(1)
        header_html = m.group(2)

        # Extract URL
        url_m = re.search(r'href="(/jobs/[^"]+?/)"', header_html)
        url = url_m.group(1) if url_m else ""
        if url and not url.startswith("http"):
            url = "https://djinni.co" + url

        # Extract title
        title_m = re.search(r'class="[^"]*job-item__position[^"]*"[^>]*>([^<]+)', header_html)
        title = title_m.group(1).strip() if title_m else ""

        # Extract company
        company_m = re.search(r'class="[^"]*text-gray-800[^"]*font-weight-500[^"]*"[^>]*>([^<]+)', header_html)
        company = company_m.group(1).strip() if company_m else ""

        if not title or not url:
            continue

        # Extract description
        desc_pattern = re.compile(
            rf'<div\s+id="job-description-{job_id}">(.*?)'
            r'<div\s+class="[^"]*job-item__tags',
            re.DOTALL
        )
        desc_m = desc_pattern.search(html, m.end())
        description = ""
        if desc_m:
            desc_html = desc_m.group(1)
            # Try to get full text (js-original-text) first, fallback to truncated
            full_m = re.search(r'class="[^"]*js-original-text[^"]*"[^>]*>(.*?)</span>', desc_html, re.DOTALL)
            if full_m:
                description = full_m.group(1)
            else:
                trunc_m = re.search(r'class="[^"]*js-truncated-text[^"]*"[^>]*>(.*?)</span>', desc_html, re.DOTALL)
                if trunc_m:
                    description = trunc_m.group(1)

        # Clean HTML from description
        description = re.sub(r'<[^>]+>', '\n', description)
        description = re.sub(r'\n{2,}', '\n', description).strip()

        items.append({
            "title": DjinniVacancyParser._clean(title),
            "company": DjinniVacancyParser._clean(company),
            "url": url,
            "description": DjinniVacancyParser._clean(description),
            "date": datetime.now(timezone.utc).strftime("%d %b %Y"),
        })

    return items


def parse_djinni(url: str = DJINNI_URL) -> dict[str, Any]:
    html = fetch_html(url)
    items = parse_djinni_regex(html)
    return {
        "source": "djinni",
        "source_url": url,
        "items_count": len(items),
        "items": items,
    }


def ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vacancies (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            posted_date TEXT NOT NULL,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vacancy_sources (
            url TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'dou'
        )
        """
    )
    conn.commit()


def save_new_items_djinni(items: list[dict[str, str]]) -> list[dict[str, str]]:
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_db(conn)
        new_items: list[dict[str, str]] = []
        for item in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO vacancies (url, title, company, posted_date) VALUES (?, ?, ?, ?)",
                (item["url"], item["title"], item["company"], item["date"]),
            )
            if cur.rowcount == 1:
                new_items.append(item)
                conn.execute("INSERT OR IGNORE INTO vacancy_sources (url, source) VALUES (?, ?)", (item["url"], "djinni"))
        conn.commit()
        return new_items
    finally:
        conn.close()


def refresh_djinni(url: str = DJINNI_URL) -> dict[str, Any]:
    result = parse_djinni(url)
    new_items = save_new_items_djinni(result["items"])
    return {
        "source": "djinni",
        "items_count": result["items_count"],
        "new_items_count": len(new_items),
        "new_items": new_items,
    }


def main():
    result = refresh_djinni()
    for idx, item in enumerate(result.get("new_items", [])[:5], 1):
        print(f"{idx}. [{item['date']}] {item['title']} — {item['company']}")
        print(f"   {item['url']}")
    print(f"\nTotal: {result['items_count']}, New: {result['new_items_count']}")


if __name__ == "__main__":
    main()
