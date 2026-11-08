from __future__ import annotations

from dataclasses import replace
import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.llm.hosted import HostedProvider
from app.providers.llm.ollama import OllamaProvider
from app.retrieval.chroma import ChromaRetriever, _synthetic_embedding
from app.schemas import Passage


def test_healthz_endpoint():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readyz_endpoint_scenarios():
    client = TestClient(app)
    if not hasattr(app.state, "settings") or app.state.settings is None:
        app.state.settings = Settings()

    # 1. Stub retriever is ready
    app.state.settings = replace(app.state.settings, retriever="stub")
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"

    # 2. Chroma empty error
    app.state.settings = replace(app.state.settings, retriever="chroma")
    mock_coll = MagicMock()
    mock_coll.count.return_value = 0
    mock_coll.metadata = {"embed_model": app.state.settings.embed_model}
    mock_client = MagicMock()
    mock_client.get_collection.return_value = mock_coll

    with patch("chromadb.HttpClient", return_value=mock_client):
        res = client.get("/readyz")
        assert res.status_code == 503
        assert "empty" in res.json()["error"]

    # 3. Chroma model pin mismatch
    mock_coll.count.return_value = 10
    mock_coll.metadata = {"embed_model": "different-model:latest"}
    with patch("chromadb.HttpClient", return_value=mock_client):
        res = client.get("/readyz")
        assert res.status_code == 503
        assert "mismatch" in res.json()["error"]

    # 4. Chroma ok and Redis ok
    mock_coll.metadata = {"embed_model": app.state.settings.embed_model}
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    with patch("chromadb.HttpClient", return_value=mock_client):
        with patch("redis.Redis", return_value=mock_redis):
            res = client.get("/readyz")
            assert res.status_code == 200
            assert res.json()["status"] == "ready"


def test_chroma_retriever():
    settings = Settings(
        retriever="chroma",
        embed_model="nomic-embed-text:latest",
        chroma_host="localhost",
        chroma_port=8000,
    )
    vec = _synthetic_embedding("test query", dim=768)
    assert len(vec) == 768

    with patch("chromadb.HttpClient"):
        retriever = ChromaRetriever(settings)
        # Mock embed_query fallback
        q_vec = retriever.embed_query("exam query")
        assert len(q_vec) == 768

        # Mock collection query
        mock_coll = MagicMock()
        mock_coll.metadata = {"embed_model": settings.embed_model}
        mock_coll.query.return_value = {
            "ids": [["id-1", "id-2"]],
            "documents": [["Passage 1 text", "Passage 2 text"]],
            "metadatas": [[
                {"source": "doc1.pdf", "section": "Sec 1", "page": 1, "doc_version": "2024", "superseded": False},
                {"source": "doc2.pdf", "section": "Sec 2", "page": 2, "doc_version": "2021", "superseded": True},
            ]],
            "distances": [[0.1, 0.2]],
        }
        retriever.client.get_collection.return_value = mock_coll

        passages = retriever.retrieve("exam query", k=2, allow_superseded=False)
        assert len(passages) == 2
        assert passages[0].source == "doc1.pdf"
        assert mock_coll.query.call_args.kwargs["where"] == {"superseded": {"$ne": True}}


def test_hosted_llm_provider():
    settings = Settings(llm_api_key="sk-test-key", hosted_base_url="https://api.hosted.test/v1")
    provider = HostedProvider(settings)

    # Empty passages returns low confidence refusal
    ans = provider.generate("what is the fee?", [])
    assert ans.answered_from_context is False
    assert ans.confidence == "low"

    passages = [
        Passage(
            chunk_id="p1",
            text="Fee is 5000 PKR per credit hour.",
            source="fee.pdf",
            section="Tuition",
            page=1,
            score=0.9,
        )
    ]

    # Valid response mock
    mock_payload = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "reasoning": "Context states 5000 PKR per credit hour",
                    "answer": "The fee is 5000 PKR per credit hour.",
                    "citations": [{"source": "fee.pdf", "section": "Tuition", "page": 1}],
                    "answered_from_context": True,
                    "confidence": "high",
                })
            }
        }]
    }
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = mock_payload

    with patch("httpx.Client.post", return_value=mock_res):
        ans = provider.generate("what is the fee?", passages)
        assert ans.answered_from_context is True
        assert len(ans.citations) == 1

    # Malformed JSON fallback
    mock_bad_res = MagicMock()
    mock_bad_res.status_code = 200
    mock_bad_res.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
    with patch("httpx.Client.post", return_value=mock_bad_res):
        fallback_ans = provider.generate("what is the fee?", passages)
        assert fallback_ans.answered_from_context is False


def test_ollama_provider_fallback():
    settings = Settings(ollama_url="http://localhost:11434", llm_model="llama3.2:3b")
    provider = OllamaProvider(settings)

    passages = [
        Passage(
            chunk_id="p1",
            text="Passing grade is D.",
            source="rules.pdf",
            section="Grading",
            page=4,
            score=0.85,
        )
    ]

    # Server error triggering fallback
    mock_res = MagicMock()
    mock_res.status_code = 500
    with patch("httpx.Client.post", return_value=mock_res):
        ans = provider.generate("what is the passing grade?", passages)
        assert ans.answered_from_context is False
        assert "unable to formulate" in ans.answer
