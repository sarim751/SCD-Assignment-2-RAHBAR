"""Stage 2: Clean. Remove artefacts without destroying structure."""

from __future__ import annotations

import re

from .models import Document, Page


def clean_page_text(text: str) -> str:
    # 1. Normalize line breaks and non-breaking spaces
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")

    # 2. Strip repeating page headers and page number lines
    text = re.sub(r"(?i)^[ \t]*page\s+\d+\s+(of\s+\d+)?\s*$", "", text, flags=re.M)
    text = re.sub(r"(?i)^[ \t]*fast\s+national\s+university.*$", "", text, flags=re.M)
    text = re.sub(r"(?i)^[ \t]*undergraduate\s+academic\s+rules.*$", "", text, flags=re.M)
    text = re.sub(r"(?i)^[ \t]*student\s+handbook.*$", "", text, flags=re.M)

    # 3. Clean trailing whitespace per line while preserving indentation and Markdown tables
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned_text = "\n".join(lines)

    # 4. Collapse three or more newlines into two
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)

    return cleaned_text.strip()


def clean(doc: Document) -> Document:
    """Strip headers and footers that repeat on every page; keep headings and
    section boundaries, because chunking and citations depend on them."""
    cleaned_pages: list[Page] = []
    for p in doc.pages:
        cleaned_text = clean_page_text(p.text)
        if cleaned_text:
            cleaned_pages.append(
                Page(
                    source=p.source,
                    page=p.page,
                    text=cleaned_text,
                    ocr=p.ocr,
                )
            )

    return Document(
        source=doc.source,
        doc_version=doc.doc_version,
        superseded=doc.superseded,
        pages=cleaned_pages,
    )
