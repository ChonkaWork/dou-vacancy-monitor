import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


URL = "https://jobs.dou.ua/vacancies/?category=Java"
DEFAULT_DB_PATH = Path(__file__).with_name("jobs.db")


class DouVacancyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.total_count = None
        self._in_h1 = False
        self._h1_buf = []

        self._in_li = False
        self._current_hot = False
        self._current = {}
        self._capture = None

        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "h1":
            self._in_h1 = True
            self._h1_buf = []

        if tag == "li":
            li_class = attrs.get("class", "")
            if "l-vacancy" in li_class.split():
                self._in_li = True
                self._current_hot = "__hot" in li_class.split()
                self._current = {"title": "", "company": "", "url": "", "date": "", "summary": ""}

        if not self._in_li:
            return

        if tag == "div" and attrs.get("class") == "date":
            self._capture = "date"
        elif tag == "a" and attrs.get("class") == "vt":
            self._capture = "title"
            self._current["url"] = attrs.get("href", "")
        elif tag == "a" and attrs.get("class") == "company":
            self._capture = "company"
        elif tag == "div" and attrs.get("class") == "sh-info":
            self._capture = "summary"

    def handle_endtag(self, tag):
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            text = " ".join(self._h1_buf).strip()
            m = re.search(r"(\d+)\s+ваканс", text, re.IGNORECASE)
            if m:
                self.total_count = int(m.group(1))

        if self._in_li and tag == "li":
            if not self._current_hot and self._current.get("title"):
                self.items.append(
                    {
                        "date": self._clean(self._current.get("date", "")),
                        "title": self._clean(self._current.get("title", "")),
                        "company": self._clean(self._current.get("company", "")),
                        "summary": self._clean(self._current.get("summary", "")),
                        "url": self._current.get("url", ""),
                    }
                )
            self._in_li = False
            self._current_hot = False
            self._current = {}
            self._capture = None

        if self._capture and tag in {"div", "a"}:
            self._capture = None

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        if self._in_h1:
            self._h1_buf.append(text)

        if self._in_li and self._capture:
            prev = self._current.get(self._capture, "")
            self._current[self._capture] = f"{prev} {text}".strip()

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", unescape(text)).strip()


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


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
        CREATE TABLE IF NOT EXISTS snapshot_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshot_items (
            run_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            posted_date TEXT NOT NULL,
            summary TEXT NOT NULL,
            PRIMARY KEY (run_id, position),
            FOREIGN KEY (run_id) REFERENCES snapshot_runs(id)
        )
        """
    )
    conn.commit()


def save_new_items(db_path: Path, items: list[dict[str, str]]) -> list[dict[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        new_items: list[dict[str, str]] = []
        for item in items:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO vacancies (url, title, company, posted_date)
                VALUES (?, ?, ?, ?)
                """,
                (item["url"], item["title"], item["company"], item["date"]),
            )
            if cur.rowcount == 1:
                new_items.append(item)
        conn.commit()
        return new_items
    finally:
        conn.close()


def parse_page(url: str, limit: int | None) -> dict[str, Any]:
    html = fetch_html(url)
    parser = DouVacancyParser()
    parser.feed(html)
    items = parser.items if limit is None else parser.items[:limit]
    return {
        "source_url": url,
        "total_vacancies_in_header": parser.total_count,
        "items": items,
    }


def save_snapshot(db_path: Path, items: list[dict[str, str]]) -> int:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        cur = conn.execute("INSERT INTO snapshot_runs DEFAULT VALUES")
        run_id = int(cur.lastrowid)
        rows = [
            (
                run_id,
                idx,
                item["url"],
                item["title"],
                item["company"],
                item["date"],
                item.get("summary", ""),
            )
            for idx, item in enumerate(items, 1)
        ]
        conn.executemany(
            """
            INSERT INTO snapshot_items (run_id, position, url, title, company, posted_date, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def refresh_cache(url: str, db_path: Path) -> dict[str, Any]:
    result = parse_page(url, limit=None)
    items = result["items"]
    new_items = save_new_items(db_path, items)
    run_id = save_snapshot(db_path, items)
    return {
        "run_id": run_id,
        "total_vacancies_in_header": result["total_vacancies_in_header"],
        "items_count": len(items),
        "new_items_count": len(new_items),
        "new_items": new_items,
    }


def get_latest_snapshot_time(db_path: Path) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        row = conn.execute("SELECT MAX(run_at) FROM snapshot_runs").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_latest_items(db_path: Path, limit: int = 5) -> list[dict[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        row = conn.execute("SELECT id FROM snapshot_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return []
        run_id = row[0]
        rows = conn.execute(
            """
            SELECT posted_date, title, company, summary, url
            FROM snapshot_items
            WHERE run_id = ?
            ORDER BY position ASC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        return [
            {
                "date": r[0],
                "title": r[1],
                "company": r[2],
                "summary": r[3],
                "url": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_new_items_since(db_path: Path, hours: int, limit: int = 5) -> list[dict[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        threshold = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            """
            SELECT posted_date, title, company, url, first_seen_at
            FROM vacancies
            WHERE first_seen_at >= ?
            ORDER BY first_seen_at DESC
            LIMIT ?
            """,
            (threshold, limit),
        ).fetchall()
        return [
            {
                "date": r[0],
                "title": r[1],
                "company": r[2],
                "url": r[3],
                "first_seen_at": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_last_new_item_time(db_path: Path) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        row = conn.execute("SELECT MAX(first_seen_at) FROM vacancies").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_last_digest_time(db_path: Path) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_digest_runs_table(conn)
        row = conn.execute("SELECT MAX(sent_at) FROM digest_runs").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def record_digest_run(db_path: Path, items_sent: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_digest_runs_table(conn)
        cur = conn.execute("INSERT INTO digest_runs (items_sent) VALUES (?)", (items_sent,))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_items_since_digest(db_path: Path, limit: int = 50) -> list[dict[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_digest_runs_table(conn)
        row = conn.execute("SELECT MAX(sent_at) FROM digest_runs").fetchone()
        threshold = row[0] if row and row[0] else None
        if threshold is None:
            threshold = "2000-01-01 00:00:00"

        rows = conn.execute(
            """
            SELECT url, title, company, posted_date, first_seen_at
            FROM vacancies
            WHERE first_seen_at > ?
            ORDER BY first_seen_at DESC
            LIMIT ?
            """,
            (threshold, limit),
        ).fetchall()
        return [
            {
                "url": r[0],
                "title": r[1],
                "company": r[2],
                "date": r[3],
                "summary": "",
                "first_seen_at": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def ensure_digest_runs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS digest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            items_sent INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()


def ensure_cv_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cv_storage (
            user_id INTEGER PRIMARY KEY,
            cv_text TEXT NOT NULL,
            skills_json TEXT NOT NULL DEFAULT '[]',
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vacancy_analyses (
            user_id INTEGER NOT NULL,
            vacancy_url TEXT NOT NULL,
            match_score INTEGER NOT NULL,
            matched_skills_json TEXT NOT NULL DEFAULT '[]',
            analysis_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, vacancy_url),
            FOREIGN KEY (user_id) REFERENCES cv_storage(user_id)
        )
        """
    )
    conn.commit()


def save_cv(db_path: Path, user_id: int, cv_text: str, skills: list[str]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_cv_tables(conn)
        conn.execute(
            """
            INSERT INTO cv_storage (user_id, cv_text, skills_json)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                cv_text = excluded.cv_text,
                skills_json = excluded.skills_json,
                uploaded_at = CURRENT_TIMESTAMP
            """,
            (user_id, cv_text, json.dumps(skills)),
        )
        conn.commit()
    finally:
        conn.close()


def get_cv(db_path: Path, user_id: int) -> tuple[str, list[str]] | None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_cv_tables(conn)
        row = conn.execute(
            "SELECT cv_text, skills_json FROM cv_storage WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        return row[0], json.loads(row[1])
    finally:
        conn.close()


def clear_cv(db_path: Path, user_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_cv_tables(conn)
        conn.execute("DELETE FROM cv_storage WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM vacancy_analyses WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def get_cached_analysis(db_path: Path, user_id: int, vacancy_url: str) -> tuple[int, list[str], str] | None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_cv_tables(conn)
        row = conn.execute(
            "SELECT match_score, matched_skills_json, analysis_text FROM vacancy_analyses WHERE user_id = ? AND vacancy_url = ?",
            (user_id, vacancy_url),
        ).fetchone()
        if not row:
            return None
        return row[0], json.loads(row[1]), row[2]
    finally:
        conn.close()


def save_analysis(db_path: Path, user_id: int, vacancy_url: str, match_score: int, matched_skills: list[str], analysis_text: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        ensure_cv_tables(conn)
        conn.execute(
            """
            INSERT INTO vacancy_analyses (user_id, vacancy_url, match_score, matched_skills_json, analysis_text)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, vacancy_url) DO UPDATE SET
                match_score = excluded.match_score,
                matched_skills_json = excluded.matched_skills_json,
                analysis_text = excluded.analysis_text,
                created_at = CURRENT_TIMESTAMP
            """,
            (user_id, vacancy_url, match_score, json.dumps(matched_skills), analysis_text),
        )
        conn.commit()
    finally:
        conn.close()


def print_human(total_count: int | None, items: list[dict[str, str]]) -> None:
    print(f"Total vacancies in header: {total_count}")
    print(f"Last {len(items)} added (excluding __hot):")
    for idx, item in enumerate(items, 1):
        print(f"{idx}. [{item['date']}] {item['title']} — {item['company']}")
        print(f"   {item['url']}")


def main():
    argp = argparse.ArgumentParser()
    argp.add_argument("--url", default=URL, help="DOU category URL")
    argp.add_argument("--limit", type=int, default=5, help="How many latest non-hot items to return")
    argp.add_argument("--json", action="store_true", help="Print JSON result")
    argp.add_argument("--refresh-cache", action="store_true", help="Refresh full snapshot cache")
    argp.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite db path for dedupe/new-item tracking",
    )
    args = argp.parse_args()

    if args.refresh_cache:
        cache_result = refresh_cache(args.url, Path(args.db))
        print(json.dumps(cache_result, ensure_ascii=False, indent=2))
        return

    result = parse_page(args.url, args.limit)
    items = result["items"]
    new_items = save_new_items(Path(args.db), items)

    result["new_items"] = new_items
    result["new_items_count"] = len(new_items)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print_human(result["total_vacancies_in_header"], items)
    print(f"New since last run (within this top {args.limit} set): {len(new_items)}")


if __name__ == "__main__":
    main()
