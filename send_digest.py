import os
import urllib.parse
import urllib.request
from pathlib import Path

from parse_dou_java import DEFAULT_DB_PATH, URL, parse_page, save_new_items


def build_message(total: int | None, new_items: list[dict[str, str]]) -> str:
    if not new_items:
        return (
            "DOU Java digest\n"
            f"Всього у категорії: {total}\n"
            "Нових вакансій у топ-5 (без hot) не знайдено."
        )

    lines = [
        "DOU Java digest",
        f"Всього у категорії: {total}",
        f"Нових вакансій: {len(new_items)}",
        "",
    ]
    for idx, item in enumerate(new_items, 1):
        lines.append(f"{idx}) {item['title']} — {item['company']} ({item['date']})")
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

    result = parse_page(URL, limit=5)
    new_items = save_new_items(Path(DEFAULT_DB_PATH), result["items"])
    message = build_message(result["total_vacancies_in_header"], new_items)
    send_telegram_message(token, chat_id, message)
    print("Digest sent to Telegram.")


if __name__ == "__main__":
    main()
