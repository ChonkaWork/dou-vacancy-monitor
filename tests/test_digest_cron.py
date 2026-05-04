from pathlib import Path
import sys
from datetime import datetime, timedelta


sys.path.append(str(Path(__file__).resolve().parents[1]))

import parse_dou_java as pdj


def test_get_items_since_digest_returns_all_on_first_run(tmp_path):
    db_path = tmp_path / "test_jobs.db"
    items = [
        {"url": "https://example.com/1", "title": "Job 1", "company": "Co1", "date": "today", "summary": ""},
        {"url": "https://example.com/2", "title": "Job 2", "company": "Co2", "date": "today", "summary": ""},
    ]
    pdj.save_new_items(db_path, items)

    result = pdj.get_items_since_digest(db_path)

    assert len(result) == 2


def test_get_items_since_digest_returns_only_new(tmp_path):
    db_path = tmp_path / "test_jobs.db"
    items = [
        {"url": "https://example.com/1", "title": "Job 1", "company": "Co1", "date": "today", "summary": ""},
    ]
    pdj.save_new_items(db_path, items)

    pdj.record_digest_run(db_path, 1)

    import time
    time.sleep(1.1)

    items2 = [
        {"url": "https://example.com/2", "title": "Job 2", "company": "Co2", "date": "today", "summary": ""},
    ]
    pdj.save_new_items(db_path, items2)

    result = pdj.get_items_since_digest(db_path)

    assert len(result) == 1
    assert result[0]["url"] == "https://example.com/2"


def test_record_digest_run_tracks_items(tmp_path):
    db_path = tmp_path / "test_jobs.db"
    run_id = pdj.record_digest_run(db_path, 5)

    assert run_id >= 1
