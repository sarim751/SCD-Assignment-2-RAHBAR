"""Stage 1: Extract. PDF, HTML, or Markdown to text, page by page."""

from __future__ import annotations

import re
from pathlib import Path

import pypdf
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes

from .models import Document, Page


def _extract_pdf(file_path: Path, source_name: str) -> list[Page]:
    pages: list[Page] = []
    file_bytes = file_path.read_bytes()

    try:
        reader = pypdf.PdfReader(file_path)
        for idx, p in enumerate(reader.pages, start=1):
            text = p.extract_text() or ""
            if len(text.strip()) > 30:
                pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
            else:
                # Page has no text layer; perform OCR fallback for scanned PDFs
                try:
                    images = convert_from_bytes(file_bytes, first_page=idx, last_page=idx)
                    if images:
                        ocr_text = pytesseract.image_to_string(images[0], config="--psm 6")
                        pages.append(Page(source=source_name, page=idx, text=ocr_text, ocr=True))
                    else:
                        pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
                except Exception:
                    pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
        return pages
    except Exception:
        # Fallback to pdfplumber
        try:
            with pdfplumber.open(file_path) as pdf:
                for idx, p in enumerate(pdf.pages, start=1):
                    text = p.extract_text() or ""
                    pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
        except Exception:
            pass

    return pages


def _extract_html(file_path: Path, source_name: str) -> list[Page]:
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    clean_text = re.sub(r"<script.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    clean_text = re.sub(r"<style.*?</style>", "", clean_text, flags=re.DOTALL | re.IGNORECASE)
    clean_text = re.sub(r"<[^>]+>", " ", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    return [Page(source=source_name, page=1, text=clean_text, ocr=False)]


def _extract_markdown(file_path: Path, source_name: str) -> list[Page]:
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    body_lines = []
    in_frontmatter = False
    for line in lines:
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if not in_frontmatter:
            body_lines.append(line)
    return [Page(source=source_name, page=1, text="\n".join(body_lines), ocr=False)]


def extract(file_path: str, doc_version: str, superseded: bool) -> Document:
    path = Path(file_path)
    source_name = path.name

    if path.suffix.lower() == ".pdf":
        pages = _extract_pdf(path, source_name)
    elif path.suffix.lower() in [".html", ".htm"]:
        pages = _extract_html(path, source_name)
    elif path.suffix.lower() in [".md", ".markdown", ".txt"]:
        pages = _extract_markdown(path, source_name)
    else:
        pages = [Page(source=source_name, page=1, text=path.read_text(encoding="utf-8", errors="replace"), ocr=False)]

    return Document(
        source=source_name,
        doc_version=doc_version,
        superseded=superseded,
        pages=pages,
    )
