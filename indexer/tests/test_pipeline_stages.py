from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.chunk import chunk
from pipeline.clean import clean, clean_page_text
from pipeline.config import get_settings
from pipeline.embed import _synthetic_embedding, embed
from pipeline.extract import extract
from pipeline.models import Chunk, Document, Page
from pipeline.store import store


def test_clean_page_text_removes_headers_and_footers():
    raw_text = (
        "Page 1 of 5\r\n"
        "FAST National University of Computer and Emerging Sciences\r\n"
        "Undergraduate Academic Rules\r\n"
        "Section 1.1 General Policy\xa0with space.\r\n"
        "\n\n\n\n"
        "Student Handbook\r\n"
        "Page 1\r\n"
    )
    cleaned = clean_page_text(raw_text)
    assert "Page 1 of 5" not in cleaned
    assert "FAST National University" not in cleaned
    assert "Student Handbook" not in cleaned
    assert "Section 1.1 General Policy with space." in cleaned
    assert "\n\n\n" not in cleaned


def test_clean_document_structure():
    doc = Document(
        source="test.pdf",
        doc_version="2024",
        superseded=False,
        pages=[
            Page(source="test.pdf", page=1, text="Page 1 of 5\nReal content here.", ocr=False),
            Page(source="test.pdf", page=2, text="Page 2\n", ocr=False),  # empty after clean
        ],
    )
    cleaned_doc = clean(doc)
    assert len(cleaned_doc.pages) == 1
    assert "Real content here." in cleaned_doc.pages[0].text
    assert cleaned_doc.source == "test.pdf"


def test_chunking_with_sections_and_overlap():
    text = (
        "## Section 1: Academic Standing\n\n"
        "Students must maintain a minimum CGPA of 2.0 to remain in good standing. "
        "A student whose CGPA falls below 2.0 will be placed on academic probation.\n\n"
        "Section 2: Grading System\n\n"
        "The grading scale ranges from A to F. Incomplete grades convert to F after one semester."
    )
    doc = Document(
        source="handbook.md",
        doc_version="2024",
        superseded=False,
        pages=[Page(source="handbook.md", page=1, text=text, ocr=False)],
    )

    chunks = chunk(doc, size=20, overlap=5)
    assert len(chunks) >= 2
    assert chunks[0].source == "handbook.md"
    assert chunks[0].doc_version == "2024"
    assert chunks[0].superseded is False
    assert any("Academic Standing" in c.section for c in chunks)
    meta = chunks[0].metadata()
    assert "source" in meta and "doc_version" in meta and "superseded" in meta


def test_synthetic_embedding_properties():
    vec_768 = _synthetic_embedding("FAST NUCES regulation", dim=768)
    assert len(vec_768) == 768
    norm = math.sqrt(sum(x * x for x in vec_768))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)

    vec_384 = _synthetic_embedding("FAST NUCES regulation", dim=384)
    assert len(vec_384) == 384


def test_embed_fallback_and_mock():
    settings = get_settings()
    texts = ["First passage text", "Second passage text"]
    # Offline fallback should generate 2 synthetic vectors
    embeddings = embed(texts, settings)
    assert len(embeddings) == 2
    assert len(embeddings[0]) == 768

    # When HTTP response 200 is mocked
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"embeddings": [[0.1] * 768, [0.2] * 768]}
    with patch("httpx.Client.post", return_value=mock_res):
        res = embed(texts, settings)
        assert len(res) == 2
        assert res[0][0] == 0.1


def test_extract_html_and_text(tmp_path: Path):
    html_file = tmp_path / "page.html"
    html_file.write_text(
        "<html><head><style>body {color: red;}</style></head>"
        "<body><h1>Rules</h1><p>Student rules</p>"
        "<table><tr><th>Course</th><th>Credit</th></tr><tr><td>CS101</td><td>3</td></tr></table>"
        "</body></html>",
        encoding="utf-8",
    )
    doc_html = extract(str(html_file), doc_version="2024", superseded=False)
    assert len(doc_html.pages) == 1
    assert "Rules" in doc_html.pages[0].text
    assert "| Course | Credit |" in doc_html.pages[0].text
    assert "body {" not in doc_html.pages[0].text

    txt_file = tmp_path / "notice.txt"
    txt_file.write_text("Notice regarding examinations 2024.", encoding="utf-8")
    doc_txt = extract(str(txt_file), doc_version="2024", superseded=True)
    assert len(doc_txt.pages) == 1
    assert "Notice regarding examinations" in doc_txt.pages[0].text
    assert doc_txt.superseded is True


def test_store_upsert_and_cache_flush():
    settings = get_settings()
    chunks = [
        Chunk(
            id="test-1",
            text="chunk text one",
            source="rules.pdf",
            section="Section 1",
            page=1,
            doc_version="2024",
            superseded=False,
        )
    ]
    vectors = [[0.05] * 768]

    mock_collection = MagicMock()
    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection

    with patch("pipeline.store.get_client", return_value=mock_client):
        with patch("redis.Redis") as mock_redis:
            count = store(chunks, vectors, settings)
            assert count == 1
            mock_client.get_or_create_collection.assert_called_once()
            mock_collection.upsert.assert_called_once()
            mock_redis.return_value.flushdb.assert_called_once()
