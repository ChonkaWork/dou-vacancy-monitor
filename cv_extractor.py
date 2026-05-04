from pathlib import Path
from typing import BinaryIO


def extract_text_from_file(file_name: str, file_bytes: bytes) -> str:
    name = file_name.lower()

    if name.endswith((".txt", ".md", ".csv", ".json", ".yaml", ".yml")):
        return file_bytes.decode("utf-8", errors="ignore")

    if name.endswith(".pdf"):
        return _extract_pdf(file_bytes)

    if name.endswith(".docx"):
        return _extract_docx(file_bytes)

    return file_bytes.decode("utf-8", errors="ignore")


def _extract_pdf(file_bytes: bytes) -> str:
    import io
    import pdfplumber

    pages = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def _extract_docx(file_bytes: bytes) -> str:
    import io
    import docx

    doc = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
