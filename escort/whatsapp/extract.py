from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return data.decode("utf-8", errors="replace").strip()
    if suffix == ".docx":
        return _docx(data)
    if suffix == ".pdf":
        return _pdf(data)
    raise ValueError(f"unsupported document {suffix or filename}")


def _docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    text = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        pieces = re.findall(r"\((?:\\.|[^\\)])*\)", data.decode("latin-1", errors="ignore"))
        words = []
        for piece in pieces:
            inner = piece[1:-1].replace(r"\(", "(").replace(r"\)", ")").replace(r"\n", " ")
            if inner.strip():
                words.append(inner.strip())
        return "\n".join(words).strip()
