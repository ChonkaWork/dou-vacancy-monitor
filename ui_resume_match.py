import re

import streamlit as st

from parse_dou_java import URL, parse_page


SKILL_KEYWORDS = [
    "java",
    "spring",
    "spring boot",
    "kafka",
    "sql",
    "postgres",
    "mysql",
    "rest",
    "docker",
    "kubernetes",
    "aws",
    "gcp",
    "microservices",
    "junit",
    "hibernate",
]


def extract_text_from_uploaded_file(uploaded_file) -> str:
    file_name = uploaded_file.name.lower()
    raw = uploaded_file.read()

    if file_name.endswith((".txt", ".md", ".csv", ".json", ".yaml", ".yml")):
        return raw.decode("utf-8", errors="ignore")

    # Minimal fallback for unknown formats: best-effort decode.
    return raw.decode("utf-8", errors="ignore")


def extract_skills(text: str) -> list[str]:
    low = text.lower()
    found = [skill for skill in SKILL_KEYWORDS if skill in low]
    return sorted(set(found))


def score_job(job: dict[str, str], resume_skills: list[str]) -> tuple[int, list[str]]:
    haystack = " ".join(
        [
            job.get("title", ""),
            job.get("company", ""),
            job.get("summary", ""),
        ]
    ).lower()
    matched = [s for s in resume_skills if s in haystack]
    score = int((len(matched) / max(len(resume_skills), 1)) * 100)
    return score, matched


def main() -> None:
    st.set_page_config(page_title="DOU Resume Matcher", layout="wide")
    st.title("DOU Java Tracker - Resume Matcher")
    st.caption("Choose your resume file and compare it with the latest non-hot vacancies.")

    uploaded = st.file_uploader(
        "Select resume file",
        type=["txt", "md", "csv", "json", "yaml", "yml"],
        accept_multiple_files=False,
    )

    if not uploaded:
        st.info("Upload your resume file to continue.")
        return

    st.success(f"Selected file: {uploaded.name}")

    with st.expander("Pipeline progress", expanded=True):
        st.progress(100, text="1/3 Resume loaded")

    resume_text = extract_text_from_uploaded_file(uploaded)
    resume_skills = extract_skills(resume_text)
    st.write("Detected resume skills:", ", ".join(resume_skills) if resume_skills else "none")

    with st.expander("Pipeline progress", expanded=True):
        st.progress(100, text="2/3 Skills extracted")

    result = parse_page(URL, limit=10)
    jobs = result["items"]
    ranked = []
    for job in jobs:
        score, matched = score_job(job, resume_skills)
        ranked.append((score, matched, job))

    ranked.sort(key=lambda x: x[0], reverse=True)

    with st.expander("Pipeline progress", expanded=True):
        st.progress(100, text="3/3 Jobs matched")

    st.subheader("Top matches")
    if not ranked:
        st.warning("No jobs parsed.")
        return

    for score, matched, job in ranked[:5]:
        st.markdown(f"### {job['title']} - {job['company']}")
        st.write(f"Score: **{score}%**")
        st.write(f"Date: {job['date']}")
        st.write(f"Matched skills: {', '.join(matched) if matched else 'none'}")
        st.write(job["url"])
        summary = re.sub(r"\s+", " ", job.get("summary", "")).strip()
        if summary:
            st.caption(summary[:240] + ("..." if len(summary) > 240 else ""))
        st.divider()

    st.caption(f"Source: {URL}")
    st.caption(f"Total vacancies in header: {result.get('total_vacancies_in_header')}")


if __name__ == "__main__":
    main()
