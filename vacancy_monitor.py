#!/usr/bin/env python3
"""Vacancy monitor — polls DOU + Djinni every 60s, sends Telegram alert on new vacancies."""

import os
import sqlite3
import sys
import time
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
POLL_SECONDS = 60
LAST_KNOWN_URLS_FILE = Path(__file__).parent / ".last_known_urls"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment.", file=sys.stderr)
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


def get_last_known_urls() -> set[str]:
    if LAST_KNOWN_URLS_FILE.exists():
        return set(LAST_KNOWN_URLS_FILE.read_text().strip().splitlines())
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_db(conn)
        rows = conn.execute("SELECT url FROM vacancies").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def save_known_urls(urls: set[str]) -> None:
    LAST_KNOWN_URLS_FILE.write_text("\n".join(sorted(urls)))


def format_alert(new_items: list[dict]) -> str:
    by_source: dict[str, list[dict]] = {}
    for item in new_items:
        source = item.get("source", "dou")
        by_source.setdefault(source, []).append(item)

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
    print(f"Starting vacancy monitor (poll every {POLL_SECONDS}s, sources: DOU + Djinni)...")
    known_urls = get_last_known_urls()
    print(f"Known vacancies: {len(known_urls)}")

    while True:
        try:
            all_new = []

            # DOU
            try:
                dou_result = refresh_dou(DOU_URL, DB_PATH)
                dou_new = dou_result.get("new_items", [])
                for item in dou_new:
                    item["source"] = "dou"
                all_new.extend(dou_new)
            except Exception as e:
                print(f"DOU poll error: {e}")

            # Djinni
            try:
                djinni_result = refresh_djinni()
                djinni_new = djinni_result.get("new_items", [])
                for item in djinni_new:
                    item["source"] = "djinni"
                all_new.extend(djinni_new)
            except Exception as e:
                print(f"Djinni poll error: {e}")

            if all_new:
                new_urls = {item["url"] for item in all_new} - known_urls
                actual_new = [item for item in all_new if item["url"] in new_urls]

                if actual_new:
                    alert = format_alert(actual_new)
                    print(f"[{datetime.now(timezone.utc).isoformat()}] Sending alert for {len(actual_new)} new vacancies")
                    tg_send(alert)
                    known_urls |= {item["url"] for item in actual_new}
                    save_known_urls(known_urls)
                else:
                    known_urls |= {item["url"] for item in all_new}
                    save_known_urls(known_urls)

        except urllib.error.HTTPError as e:
            print(f"HTTP error: {e.code} {e.reason}")
        except Exception as e:
            print(f"Poll error: {e}")

        print(f"[{datetime.now(timezone.utc).isoformat()}] Next poll in {POLL_SECONDS}s...")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
