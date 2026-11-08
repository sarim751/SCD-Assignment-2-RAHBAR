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

    # Attempt extraction via pdfplumber first for high fidelity table & text layout
    try:
        with pdfplumber.open(file_path) as pdf:
            for idx, p in enumerate(pdf.pages, start=1):
                text = p.extract_text(layout=True) or ""
                # Also extract any structured tables and append as Markdown
                tables = p.extract_tables()
                if tables:
                    table_md = []
                    for table in tables:
                        for row in table:
                            clean_row = [str(cell).replace("\n", " ").strip() if cell else "" for cell in row]
                            table_md.append("| " + " | ".join(clean_row) + " |")
                    if table_md:
                        text += "\n\n" + "\n".join(table_md)

                if len(text.strip()) > 30:
                    pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
                else:
                    # Page has no text layer; perform OCR fallback
                    try:
                        images = convert_from_bytes(file_bytes, first_page=idx, last_page=idx)
                        if images:
                            ocr_text = pytesseract.image_to_string(images[0], config="--psm 6")
                            pages.append(Page(source=source_name, page=idx, text=ocr_text, ocr=True))
                        else:
                            pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
                    except Exception:
                        pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
    except Exception:
        # Fallback to pypdf reader
        reader = pypdf.PdfReader(file_path)
        for idx, p in enumerate(reader.pages, start=1):
            text = p.extract_text() or ""
            pages.append(Page(source=source_name, page=idx, text=text, ocr=False))

    return pages


def _extract_html(file_path: Path, source_name: str) -> list[Page]:
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    # Strip script and style blocks
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.S | re.I)
    # Extract tables into markdown representation
    tables = re.findall(r"<table[^>]*>(.*?)</table>", cleaned, flags=re.S | re.I)
    md_tables = []
    for tbl in tables:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S | re.I)
        for r in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S | re.I)
            clean_cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if clean_cells:
                md_tables.append("| " + " | ".join(clean_cells) + " |")

    # Clean tags and whitespace
    text = re.sub(r"<[^>]+>", " ", cleaned)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text).strip()
    if md_tables:
        text += "\n\n" + "\n".join(md_tables)

    return [Page(source=source_name, page=1, text=text, ocr=False)]


def _extract_text(file_path: Path, source_name: str) -> list[Page]:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    return [Page(source=source_name, page=1, text=text, ocr=False)]


def extract(path: str, doc_version: str, superseded: bool) -> Document:
    """Return the document's text page by page."""
    p = Path(path)
    source_name = p.name
    suffix = p.suffix.lower()

    if suffix == ".pdf":
        pages = _extract_pdf(p, source_name)
    elif suffix in {".html", ".htm"}:
        pages = _extract_html(p, source_name)
    else:
        pages = _extract_text(p, source_name)

    return Document(
        source=source_name,
        doc_version=doc_version,
        superseded=superseded,
        pages=pages,
    )
