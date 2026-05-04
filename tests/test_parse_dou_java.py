from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

import parse_dou_java as pdj


SAMPLE_HTML = """
<html><body>
  <h1>114 вакансій в категорії Java</h1>
  <ul class="lt">
    <li class="l-vacancy __hot">
      <div class="date">30 квітня</div>
      <div class="title">
        <a class="vt" href="https://example.com/hot">Hot Job</a>
        <strong>в <a class="company" href="#">HotCo</a></strong>
      </div>
      <div class="sh-info">Hot summary</div>
    </li>
    <li class="l-vacancy">
      <div class="date">1 травня</div>
      <div class="title">
        <a class="vt" href="https://example.com/job-1">Senior Java Developer</a>
        <strong>в <a class="company" href="#">DNA325</a></strong>
      </div>
      <div class="sh-info">Spring Boot and Kafka experience.</div>
    </li>
    <li class="l-vacancy">
      <div class="date">30 квітня</div>
      <div class="title">
        <a class="vt" href="https://example.com/job-2">Java Developer</a>
        <strong>в <a class="company" href="#">Digis</a></strong>
      </div>
      <div class="sh-info">REST API and SQL.</div>
    </li>
  </ul>
</body></html>
"""


def test_parser_extracts_total_count_and_excludes_hot():
    parser = pdj.DouVacancyParser()
    parser.feed(SAMPLE_HTML)

    assert parser.total_count == 114
    assert len(parser.items) == 2
    assert parser.items[0]["title"] == "Senior Java Developer"
    assert parser.items[0]["company"] == "DNA325"
    assert parser.items[0]["summary"] == "Spring Boot and Kafka experience."
    assert parser.items[0]["url"] == "https://example.com/job-1"


def test_parse_page_respects_limit(monkeypatch):
    monkeypatch.setattr(pdj, "fetch_html", lambda url: SAMPLE_HTML)

    result = pdj.parse_page("https://dummy.local", limit=1)

    assert result["source_url"] == "https://dummy.local"
    assert result["total_vacancies_in_header"] == 114
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Senior Java Developer"


def test_save_new_items_deduplicates(tmp_path):
    db_path = tmp_path / "test_jobs.db"
    items = [
        {
            "date": "1 травня",
            "title": "Senior Java Developer",
            "company": "DNA325",
            "summary": "summary",
            "url": "https://example.com/job-1",
        },
        {
            "date": "30 квітня",
            "title": "Java Developer",
            "company": "Digis",
            "summary": "summary",
            "url": "https://example.com/job-2",
        },
    ]

    first_run = pdj.save_new_items(db_path, items)
    second_run = pdj.save_new_items(db_path, items)

    assert len(first_run) == 2
    assert len(second_run) == 0
