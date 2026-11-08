"""Chroma retriever implementation with embedding pin check and superseded filtering."""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

import httpx

from ..config import Settings
from ..schemas import Passage

log = logging.getLogger("rahbar.retrieval.chroma")


def _synthetic_embedding(text: str, dim: int = 768) -> list[float]:
    """Deterministic hash vector for offline tests/fallback."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    vals = [(b / 255.0) - 0.5 for b in h]
    reps = math.ceil(dim / len(vals))
    vec = (vals * reps)[:dim]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class ChromaRetriever:
    name = "chroma"

    def __init__(self, settings: Settings) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.settings = settings
        self.client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def embed_query(self, text: str) -> list[float]:
        """Embed using the pinned embedding model."""
        url = f"{self.settings.ollama_url.rstrip('/')}/api/embed"
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.post(url, json={"model": self.settings.embed_model, "input": [text]})
                if res.status_code == 200:
                    data = res.json()
                    embeds = data.get("embeddings") or []
                    if embeds:
                        return embeds[0]
        except Exception:
            pass

        dim = 768 if "nomic" in self.settings.embed_model else 384
        return _synthetic_embedding(text, dim=dim)

    def retrieve(self, question: str, k: int, allow_superseded: bool = False) -> list[Passage]:
        """Query top-k passages, filtering out superseded policies unless requested."""
        collection = self.client.get_collection(name=self.settings.collection)

        # Embedding pin guardrail check
        coll_meta = collection.metadata or {}
        stored_model = coll_meta.get("embed_model") or coll_meta.get("embedding_model")
        if stored_model and stored_model != self.settings.embed_model:
            log.error(
                "embedding_model mismatch guardrail triggered",
                extra={"expected": self.settings.embed_model, "collection": stored_model},
            )
            raise ValueError(
                f"embedding_model mismatch: API configured for {self.settings.embed_model} "
                f"does not match collection metadata {stored_model}"
            )

        query_vec = self.embed_query(question)
        stored_dim = coll_meta.get("dimension")
        if stored_dim and stored_dim != len(query_vec):
            raise ValueError(
                f"dimension mismatch: query vector dimension {len(query_vec)} "
                f"does not match collection dimension {stored_dim}"
            )

        # Filter out superseded documents to fix failure mode E
        where_filter: dict[str, Any] | None = None
        if not allow_superseded:
            where_filter = {"superseded": {"$ne": True}}

        results = collection.query(
            query_embeddings=[query_vec],
            n_results=k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        passages: list[Passage] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for chunk_id, doc, meta, dist in zip(ids, docs, metas, distances):
            # Chroma returns cosine distance; similarity = 1 - distance
            score = round(max(0.0, 1.0 - float(dist)), 4)
            passages.append(
                Passage(
                    chunk_id=str(chunk_id),
                    text=str(doc),
                    source=str(meta.get("source", "unknown")),
                    section=str(meta.get("section", "General")),
                    page=meta.get("page"),
                    score=score,
                )
            )

        return passages
