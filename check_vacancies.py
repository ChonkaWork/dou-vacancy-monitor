#!/usr/bin/env python3
"""Single-run vacancy checker — Snapshot Diff logic."""

import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_dou_java import URL as DOU_URL
from parse_dou_java import parse_page, save_new_items, ensure_db
from parse_djinni import parse_djinni, save_new_items_djinni

DB_PATH = Path(__file__).parent / "jobs.db"
KNOWN_FILE = Path(__file__).parent / ".known_urls"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    sys.exit(1)

API_BASE = "https://api.telegram.org/bot{token}/{method}"


def tg_send(text: str) -> None:
    url = API_BASE.format(token=TOKEN, method="sendMessage")
    payload = urllib.parse.urlencode(
        {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    ).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception as e:
        print(f"Telegram send failed: {e}")


def load_known() -> set[str]:
    if KNOWN_FILE.exists():
        content = KNOWN_FILE.read_text().strip()
        if content:
            return set(content.splitlines())
    return set()


def save_known(urls: set[str]) -> None:
    KNOWN_FILE.write_text("\n".join(sorted(urls)))


def format_report(new_items: list[dict], dou_items: list[dict], djinni_items: list[dict]) -> str:
    new_urls = {i["url"] for i in new_items}
    lines = ["<b>📋 Java Vacancies Update</b>\n"]

    # DOU
    lines.append("<b>🔵 DOU</b>")
    shown = 0
    for item in dou_items[:5]:
        marker = "🆕 " if item["url"] in new_urls else ""
        lines.append(f"{marker}{shown+1}. <b>{item['title']}</b> — {item['company']}")
        lines.append(f"   {item['url']}")
        shown += 1
    if not dou_items:
        lines.append("   _(none)_")
    lines.append("")

    # Djinni
    lines.append("<b>🟠 DJINNI</b>")
    shown = 0
    for item in djinni_items[:5]:
        marker = "🆕 " if item["url"] in new_urls else ""
        lines.append(f"{marker}{shown+1}. <b>{item['title']}</b> — {item['company']}")
        lines.append(f"   {item['url']}")
        shown += 1
    if not djinni_items:
        lines.append("   _(none)_")
    lines.append("")

    if new_items:
        lines.append(f"🔥 <b>New this check: {len(new_items)}</b>")
    return "\n".join(lines)


def main() -> None:
    import hashlib
    if "--daily-summary" in sys.argv:
        by_source = get_todays_items(DB_PATH)
        report = format_daily_summary(by_source)
        tg_send(report)
        total = sum(len(items) for items in by_source.values())
        print(f"Daily summary sent: {total} vacancies")
        return

    known = load_known()
    print(f"Known: {len(known)}")

    # 1. Scrape DOU
    try:
        res = parse_page(DOU_URL, None)
        dou_items = res.get("items", [])
        save_new_items(DB_PATH, dou_items)
        print(f"DOU: found {len(dou_items)} items.")
    except Exception as e:
        print(f"DOU error: {e}")
        dou_items = []

    # 2. Scrape Djinni
    try:
        res = parse_djinni()
        djinni_items = res.get("items", [])
        save_new_items_djinni(djinni_items)
        print(f"Djinni: found {len(djinni_items)} items.")
    except Exception as e:
        print(f"Djinni error: {e}")
        djinni_items = []

    # 3. Diff
    all_items = dou_items + djinni_items
    new_items = [i for i in all_items if i["url"] not in known]

    report = format_report(new_items, dou_items, djinni_items)

    # 4. Check for changes (Anti-Spam)
    report_hash = hashlib.md5(report.encode()).hexdigest()
    hash_file = Path(".last_report_hash")
    last_hash = hash_file.read_text().strip() if hash_file.exists() else ""

    if report_hash != last_hash:
        # Content changed or first run -> Send
        print(f"Report changed. Sending...")
        tg_send(report)
        hash_file.write_text(report_hash)
    else:
        print("No changes in top vacancies. Skipping.")

    if new_items:
        known.update(i["url"] for i in new_items)
        save_known(known)


def get_todays_items(db_path: Path) -> dict[str, list[dict]]:
    """Get all vacancies added today, grouped by source."""
    conn = sqlite3.connect(db_path)
    try:
        has_source = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vacancy_sources'"
        ).fetchone()
        if has_source:
            rows = conn.execute(
                """
                SELECT v.title, v.company, v.url, vs.source
                FROM vacancies v
                JOIN vacancy_sources vs ON v.url = vs.url
                WHERE DATE(v.first_seen_at) = DATE('now')
                ORDER BY v.first_seen_at ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT title, company, url, 'dou' FROM vacancies WHERE DATE(first_seen_at) = DATE('now') ORDER BY first_seen_at ASC"
            ).fetchall()
        by_source: dict[str, list[dict]] = {}
        for r in rows:
            by_source.setdefault(r[3], []).append({"title": r[0], "company": r[1], "url": r[2]})
        return by_source
    finally:
        conn.close()


def format_daily_summary(by_source: dict[str, list[dict]]) -> str:
    total = sum(len(items) for items in by_source.values())
    lines = [f"📊 <b>Daily Summary</b> ({total} vacancies today)\n"]
    source_icons = {"dou": "🔵", "djinni": "🟠"}
    for source, items in by_source.items():
        icon = source_icons.get(source, "📌")
        label = "DOU" if source == "dou" else "DJINNI"
        lines.append(f"{icon} <b>{label}</b> ({len(items)}):")
        for idx, item in enumerate(items, 1):
            lines.append(f"{idx}. <b>{item['title']}</b> — {item['company']}")
            lines.append(f"   {item['url']}")
        lines.append("")
    if not by_source:
        lines.append("_(No new vacancies today)_")
    return "\n".join(lines)


def main() -> None:
    if "--daily-summary" in sys.argv:
        by_source = get_todays_items(DB_PATH)
        report = format_daily_summary(by_source)
        tg_send(report)
        total = sum(len(items) for items in by_source.values())
        print(f"Daily summary sent: {total} vacancies")
        return

    known = load_known()
    print(f"Known: {len(known)}")

    # 1. Scrape DOU
    try:
        res = parse_page(DOU_URL, None)
        dou_items = res.get("items", [])
        save_new_items(DB_PATH, dou_items)
        print(f"DOU: found {len(dou_items)} items.")
        if len(dou_items) < 5:
            print("WARNING: Low item count, scraper might be blocked or page changed.")
    except Exception as e:
        print(f"DOU error: {e}")
        dou_items = []

    # 2. Scrape Djinni
    try:
        res = parse_djinni()
        djinni_items = res.get("items", [])
        save_new_items_djinni(djinni_items)
        print(f"Djinni: found {len(djinni_items)} items.")
    except Exception as e:
        print(f"Djinni error: {e}")
        djinni_items = []

    # 3. Snapshot Diff
    all_items = dou_items + djinni_items
    new_items = [i for i in all_items if i["url"] not in known]

    if new_items:
        print(f"Found {len(new_items)} new items.")
        report = format_report(new_items, dou_items, djinni_items)
        tg_send(report)
        known.update(i["url"] for i in new_items)
        save_known(known)
    else:
        print("No new items.")


if __name__ == "__main__":
    main()
