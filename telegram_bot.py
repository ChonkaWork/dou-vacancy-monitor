import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from parse_dou_java import (
    DEFAULT_DB_PATH,
    URL,
    clear_cv,
    ensure_db,
    ensure_cv_tables,
    get_cached_analysis,
    get_cv,
    get_latest_items,
    get_latest_snapshot_time,
    get_new_items_since,
    refresh_cache,
    save_analysis,
    save_cv,
)
from cv_extractor import extract_text_from_file
from llm_analyzer import analyze_job_match, extract_skills_from_cv


API_BASE = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT_SEC = 25
AUTO_REFRESH_MINUTES = 10
CV_DIR = Path(__file__).with_name("cv_files")


def tg_call(token: str, method: str, payload: dict | None = None) -> dict:
    url = API_BASE.format(token=token, method=method)
    data = None
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=35) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error in {method}: {body}")
    return result


def build_main_keyboard() -> str:
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "1 годину", "callback_data": "period:1"},
                {"text": "12 годин", "callback_data": "period:12"},
                {"text": "24 години", "callback_data": "period:24"},
            ],
            [{"text": "Оновити кеш", "callback_data": "refresh"}],
            [
                {"text": "Підібрати по CV", "callback_data": "match_cv"},
                {"text": "Мій CV", "callback_data": "my_cv"},
            ],
        ]
    }
    return json.dumps(keyboard, ensure_ascii=False)


def format_items(items: list[dict], title: str) -> str:
    lines = [title, ""]
    for idx, item in enumerate(items[:5], 1):
        lines.append(f"{idx}) {item['title']} \u2014 {item['company']} ({item['date']})")
        lines.append(item["url"])
    return "\n".join(lines)


def ensure_recent_snapshot(db_path: Path) -> None:
    latest = get_latest_snapshot_time(db_path)
    if latest is None:
        refresh_cache(URL, db_path)
        return
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT CASE WHEN DATETIME(?) <= DATETIME('now', ?) THEN 1 ELSE 0 END",
            (latest, f"-{AUTO_REFRESH_MINUTES} minutes"),
        ).fetchone()
        if row and row[0] == 1:
            refresh_cache(URL, db_path)
    finally:
        conn.close()


def make_period_response(db_path: Path, hours: int) -> str:
    new_items = get_new_items_since(db_path, hours=hours, limit=5)
    if new_items:
        return format_items(new_items, f"🔥 Нові вакансії за останні {hours} год:")

    header = f"📄 За останні {hours} год нових вакансій немає.\n\nОстанні 5 вакансій:"
    latest = get_latest_items(db_path, limit=5)
    if not latest:
        return "Немає вакансій у кеші. Спробуй /refresh."
    return format_items(latest, header)


def send_with_keyboard(token: str, chat_id: int, text: str) -> None:
    tg_call(
        token,
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": "true",
            "reply_markup": build_main_keyboard(),
        },
    )


def send_text(token: str, chat_id: int, text: str) -> None:
    tg_call(token, "sendMessage", {"chat_id": str(chat_id), "text": text})


def handle_document(token: str, db_path: Path, update: dict) -> None:
    message = update.get("message")
    if not message or "document" not in message:
        return

    chat_id = message["chat"]["id"]
    doc = message["document"]
    file_id = doc["file_id"]
    file_name = doc.get("file_name", "resume.txt")

    file_info = tg_call(token, "getFile", {"file_id": file_id})
    file_path_remote = file_info["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{token}/{file_path_remote}"
    req = urllib.request.Request(download_url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        file_bytes = resp.read()

    CV_DIR.mkdir(exist_ok=True)
    local_path = CV_DIR / f"{chat_id}_{file_name}"
    with open(local_path, "wb") as f:
        f.write(file_bytes)

    try:
        cv_text = extract_text_from_file(file_name, file_bytes)
    except Exception as exc:
        send_text(token, chat_id, f"❌ Не вдалося прочитати файл: {exc}")
        return

    if len(cv_text) < 50:
        send_text(token, chat_id, "❌ Файл занадто короткий або не містить тексту. Спробуй інший формат.")
        return

    send_text(token, chat_id, f"⏳ CV завантажено ({len(cv_text)} символів). Аналізую навички через Gemini...")

    try:
        skills = extract_skills_from_cv(cv_text)
        save_cv(db_path, chat_id, cv_text, skills)
        skills_text = ", ".join(skills) if skills else "не виявлено"
        send_with_keyboard(
            token, chat_id,
            f"✅ CV збережено!\n\n🎯 Виявлені навички:\n{skills_text}\n\nНатисни 'Підібрати по CV' щоб знайти підходящі вакансії.",
        )
    except RuntimeError as exc:
        save_cv(db_path, chat_id, cv_text, [])
        send_with_keyboard(
            token, chat_id,
            f"✅ CV збережено як текст.\n\n⚠️ Gemini аналіз не працює (перевір GOOGLE_API_KEY). Навички не витягнуто.\n\nНатисни 'Підібрати по CV' для базового порівняння.",
        )


def handle_cv_match(token: str, db_path: Path, chat_id: int) -> None:
    cv_data = get_cv(db_path, chat_id)
    if not cv_data:
        send_with_keyboard(
            token, chat_id,
            "📄 Спочатку надішли мені свій CV файлом (PDF, DOCX, TXT), а потім натисни цю кнопку.",
        )
        return

    cv_text, skills = cv_data

    send_text(token, chat_id, "🔍 Оновлюю кеш вакансій...")
    ensure_recent_snapshot(db_path)

    all_items = get_latest_items(db_path, limit=15)
    if not all_items:
        send_with_keyboard(token, chat_id, "❌ Вакансії не знайдено.")
        return

    send_text(token, chat_id, f"🧠 Аналізую {len(all_items)} вакансій через Gemini...")

    results = []
    for job in all_items:
        cached = get_cached_analysis(db_path, chat_id, job["url"])
        if cached:
            score, matched, summary = cached
        else:
            job_desc = job.get("summary", "")
            if not job_desc:
                score, matched, summary = 0, [], "Немає опису вакансії"
            else:
                try:
                    score, matched, summary = analyze_job_match(cv_text, job["title"], job_desc)
                    save_analysis(db_path, chat_id, job["url"], score, matched, summary)
                except Exception:
                    score, matched, summary = 0, [], "Помилка аналізу"
        results.append((score, matched, summary, job))

    results.sort(key=lambda x: x[0], reverse=True)

    lines = ["📊 Результати підбору вакансій:", ""]
    for score, matched, summary, job in results[:5]:
        if score >= 60:
            emoji = "🟢"
        elif score >= 30:
            emoji = "🟡"
        else:
            emoji = "🔴"
        lines.append(f"{emoji} *{score}%* \u2014 {job['title']} \u2014 {job['company']}")
        lines.append(f"   {job['url']}")
        if matched:
            lines.append(f"   ✅ {', '.join(matched[:5])}")
        lines.append("")

    if results[0][0] > 0:
        lines.append(f"📈 Найкращий збіг: {results[0][0]}%")

    send_with_keyboard(token, chat_id, "\n".join(lines))


def handle_update(token: str, db_path: Path, update: dict) -> None:
    message = update.get("message")
    callback = update.get("callback_query")

    if message and "document" in message:
        handle_document(token, db_path, update)
        return

    if message:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if text in {"/start", "/menu"}:
            ensure_recent_snapshot(db_path)
            send_with_keyboard(
                token, chat_id,
                "🚀 DOU Java Tracker готовий!\n\n📄 Надішли CV файлом (PDF/DOCX/TXT) для аналізу.\n\nВикористовуй кнопки нижче:",
            )
            return
        if text == "/refresh":
            refresh_cache(URL, db_path)
            send_with_keyboard(token, chat_id, "✅ Кеш оновлено.")
            return
        if text == "/cv":
            cv_data = get_cv(db_path, chat_id)
            if cv_data:
                _, skills = cv_data
                skills_text = ", ".join(skills) if skills else "не виявлено"
                send_with_keyboard(
                    token, chat_id,
                    f"📄 Твій CV збережено.\n\n🎯 Навички:\n{skills_text}\n\nНадішли новий файл щоб оновити.",
                )
            else:
                send_with_keyboard(
                    token, chat_id,
                    "📄 CV не знайдено. Надішли мені файл резюме.",
                )
            return
        if text == "/clear_cv":
            clear_cv(db_path, chat_id)
            send_with_keyboard(token, chat_id, "🗑 CV видалено.")
            return
        if text == "/help":
            send_with_keyboard(
                token, chat_id,
                "📖 Команди:\n"
                "/start \u2014 головне меню\n"
                "/refresh \u2014 оновити кеш вакансій\n"
                "/cv \u2014 показати збережений CV\n"
                "/clear_cv \u2014 видалити CV\n"
                "/help \u2014 ця довідка\n\n"
                "Також можеш просто надіслати CV файлом (PDF/DOCX/TXT).",
            )
            return

        ensure_recent_snapshot(db_path)
        send_with_keyboard(token, chat_id, make_period_response(db_path, 1))
        return

    if callback:
        data = callback.get("data", "")
        chat_id = callback["message"]["chat"]["id"]
        callback_id = callback["id"]
        tg_call(token, "answerCallbackQuery", {"callback_query_id": callback_id})

        if data == "refresh":
            refresh_cache(URL, db_path)
            send_with_keyboard(token, chat_id, "✅ Кеш оновлено.")
            return

        if data.startswith("period:"):
            hours = int(data.split(":", 1)[1])
            ensure_recent_snapshot(db_path)
            send_with_keyboard(token, chat_id, make_period_response(db_path, hours))
            return

        if data == "match_cv":
            handle_cv_match(token, db_path, chat_id)
            return

        if data == "my_cv":
            cv_data = get_cv(db_path, chat_id)
            if cv_data:
                _, skills = cv_data
                skills_text = ", ".join(skills) if skills else "не виявлено"
                send_with_keyboard(
                    token, chat_id,
                    f"📄 Твій CV:\n\n🎯 Навички:\n{skills_text}\n\nНадішли новий файл щоб оновити.",
                )
            else:
                send_with_keyboard(
                    token, chat_id,
                    "📄 CV не знайдено. Надішли резюме файлом (PDF/DOCX/TXT).",
                )
            return


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable.")

    db_path = Path(os.environ.get("DOU_TRACKER_DB", str(DEFAULT_DB_PATH)))

    refresh_cache(URL, db_path)

    offset = 0
    while True:
        updates = tg_call(
            token,
            "getUpdates",
            {
                "timeout": str(POLL_TIMEOUT_SEC),
                "offset": str(offset),
            },
        )["result"]
        for upd in updates:
            offset = max(offset, upd["update_id"] + 1)
            try:
                handle_update(token, db_path, upd)
            except Exception as exc:
                print(f"Update handling error: {exc}")
        time.sleep(0.3)


if __name__ == "__main__":
    main()
