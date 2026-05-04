#!/usr/bin/env python3
"""Single-run vacancy checker — for GitHub Actions / cron deployment."""

import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_dou_java import URL as DOU_URL
from parse_dou_java import refresh_cache as refresh_dou, ensure_db
from parse_djinni import refresh_djinni

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
        return set(KNOWN_FILE.read_text().strip().splitlines())
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_db(conn)
        rows = conn.execute("SELECT url FROM vacancies").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def save_known(urls: set[str]) -> None:
    KNOWN_FILE.write_text("\n".join(sorted(urls)))


def format_alert(new_items: list[dict]) -> str:
    by_source: dict[str, list[dict]] = {}
    for item in new_items:
        by_source.setdefault(item.get("source", "dou"), []).append(item)

    lines = [f"🔥 <b>New Java vacancies!</b> ({len(new_items)})\n"]
    for source, items in by_source.items():
        label = "DJINNI" if source == "djinni" else "DOU"
        lines.append(f"📌 <b>{label}</b> ({len(items)}):")
        for item in items:
            lines.append(f"• <b>{item['title']}</b> — {item['company']}")
            lines.append(item["url"])
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    known = load_known()
    print(f"Known: {len(known)}")

    all_new = []

    try:
        r = refresh_dou(DOU_URL, DB_PATH)
        for i in r.get("new_items", []):
            i["source"] = "dou"
        all_new.extend(r.get("new_items", []))
        print(f"DOU new: {len(r.get('new_items', []))}")
    except Exception as e:
        print(f"DOU error: {e}")

    try:
        r = refresh_djinni()
        for i in r.get("new_items", []):
            i["source"] = "djinni"
        all_new.extend(r.get("new_items", []))
        print(f"Djinni new: {len(r.get('new_items', []))}")
    except Exception as e:
        print(f"Djinni error: {e}")

    new_urls = {i["url"] for i in all_new} - known
    actual_new = [i for i in all_new if i["url"] in new_urls]

    if actual_new:
        alert = format_alert(actual_new)
        print(f"Sending {len(actual_new)} alerts...")
        tg_send(alert)
        known |= {i["url"] for i in actual_new}
    else:
        known |= {i["url"] for i in all_new}
        print("No new vacancies")

    save_known(known)
    print(f"Known now: {len(known)}")


if __name__ == "__main__":
    main()
