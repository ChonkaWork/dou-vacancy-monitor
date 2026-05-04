import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from parse_dou_java import (
    DEFAULT_DB_PATH,
    URL,
    get_items_since_digest,
    get_last_digest_time,
    record_digest_run,
    refresh_cache,
)


DIGEST_INTERVAL_HOURS = 1
SEND_DIGEST_NOW = os.environ.get("DIGEST_FORCE", "false").lower() == "true"


def should_send_digest(db_path: Path) -> bool:
    if SEND_DIGEST_NOW:
        return True

    last = get_last_digest_time(db_path)
    if last is None:
        return True

    threshold = (datetime.utcnow() - timedelta(hours=DIGEST_INTERVAL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    return last < threshold


def build_digest_message(new_items: list[dict[str, str]]) -> str:
    if not new_items:
        return "DOU Java digest\nЗа останню годину нових вакансій не знайдено."

    lines = [
        "DOU Java digest",
        f"Нових вакансій: {len(new_items)}",
        "",
    ]
    for idx, item in enumerate(new_items, 1):
        lines.append(f"{idx}) {item['title']} \u2014 {item['company']} ({item['date']})")
        lines.append(item["url"])
    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if '"ok":true' not in body:
            raise RuntimeError(f"Telegram API error: {body}")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment.")

    db_path = Path(os.environ.get("DOU_TRACKER_DB", str(DEFAULT_DB_PATH)))

    refresh_cache(URL, db_path)

    if not should_send_digest(db_path):
        print("Digest interval not reached. Exiting.")
        sys.exit(0)

    new_items = get_items_since_digest(db_path)

    if not new_items:
        print("No new items to send.")
        record_digest_run(db_path, 0)
        sys.exit(0)

    message = build_digest_message(new_items)
    send_telegram_message(token, chat_id, message)
    record_digest_run(db_path, len(new_items))
    print(f"Digest sent: {len(new_items)} items.")


if __name__ == "__main__":
    main()
