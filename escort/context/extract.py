from __future__ import annotations

import io
from pathlib import Path


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".md") or name.endswith(".txt"):
        return data.decode("utf-8", errors="replace")
    if name.endswith(".pdf"):
        return _pdf(data)
    if name.endswith(".docx"):
        return _docx(data)
    raise ValueError(f"unsupported file type: {filename}")


def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as err:
        raise ValueError("PDF support requires pypdf") from err
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return ""
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as err:
        raise ValueError("DOCX support requires python-docx") from err
    document = Document(io.BytesIO(data))
    return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())


def sniff_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".pdf", ".docx"}:
        return suffix.lstrip(".")
    return "txt"
