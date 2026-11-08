"""Stage 3: Chunk. Structure-aware, with overlap."""

from __future__ import annotations

import re

from .models import Chunk, Document


# Regex matching section headings and clause numbers (e.g. "Section 5.4", "Clause 1.21", "## Title")
HEADING_PATTERN = re.compile(
    r"(?m)^(?:#{1,4}\s+|(?:\d+\.[\d\.]*\s+)|(?:Section\s+\d+[\.\d]*)|(?:Clause\s+\d+[\.\d]*)|(?:Article\s+\d+))\s*(.*)$",
    re.IGNORECASE,
)


def _split_into_paragraphs(text: str) -> list[str]:
    """Split on double newlines to maintain coherent paragraph structure."""
    paras = text.split("\n\n")
    return [p.strip() for p in paras if p.strip()]


def _split_into_sentences(text: str) -> list[str]:
    """Split text on sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Split on sections, then paragraphs, then sentences, keeping each chunk under
    `size` tokens, with `overlap` tokens shared between neighbours. Every chunk keeps
    the section heading it sits under, its page, doc_version and superseded."""
    chunks: list[Chunk] = []
    chunk_counter = 1

    current_section = "General"

    for p in doc.pages:
        paragraphs = _split_into_paragraphs(p.text)
        current_chunk_words: list[str] = []

        for para in paragraphs:
            # Check if paragraph begins with or is a section heading
            heading_match = HEADING_PATTERN.match(para)
            if heading_match:
                current_section = para.split("\n")[0][:80].strip()

            words = para.split()
            # If paragraph itself exceeds target size, split on sentences
            if len(words) > size:
                sentences = _split_into_sentences(para)
                for sentence in sentences:
                    s_words = sentence.split()
                    if len(current_chunk_words) + len(s_words) > size and current_chunk_words:
                        chunk_text = " ".join(current_chunk_words)
                        chunk_id = f"{doc.source}-p{p.page}-{chunk_counter:04d}"
                        chunks.append(
                            Chunk(
                                id=chunk_id,
                                text=chunk_text,
                                source=doc.source,
                                section=current_section,
                                page=p.page,
                                doc_version=doc.doc_version,
                                superseded=doc.superseded,
                            )
                        )
                        chunk_counter += 1
                        # Retain overlap from end of chunk
                        current_chunk_words = current_chunk_words[-overlap:] if overlap > 0 else []

                    current_chunk_words.extend(s_words)
            else:
                if len(current_chunk_words) + len(words) > size and current_chunk_words:
                    chunk_text = " ".join(current_chunk_words)
                    chunk_id = f"{doc.source}-p{p.page}-{chunk_counter:04d}"
                    chunks.append(
                        Chunk(
                            id=chunk_id,
                            text=chunk_text,
                            source=doc.source,
                            section=current_section,
                            page=p.page,
                            doc_version=doc.doc_version,
                            superseded=doc.superseded,
                        )
                    )
                    chunk_counter += 1
                    current_chunk_words = current_chunk_words[-overlap:] if overlap > 0 else []

                current_chunk_words.extend(words)

        if current_chunk_words:
            chunk_text = " ".join(current_chunk_words)
            chunk_id = f"{doc.source}-p{p.page}-{chunk_counter:04d}"
            chunks.append(
                Chunk(
                    id=chunk_id,
                    text=chunk_text,
                    source=doc.source,
                    section=current_section,
                    page=p.page,
                    doc_version=doc.doc_version,
                    superseded=doc.superseded,
                )
            )
            chunk_counter += 1

    return chunks
