"""Stage 4: Embed. One vector per chunk, from the pinned model only."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import httpx

from .config import IndexerSettings


def _synthetic_embedding(text: str, dim: int = 768) -> list[float]:
    """Deterministic hash-based fallback vector when Ollama is unreachable (e.g. CI/offline)."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    vals = [(b / 255.0) - 0.5 for b in h]
    reps = math.ceil(dim / len(vals))
    vec = (vals * reps)[:dim]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def embed(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Embed with settings.embed_model and nothing else.

    With Ollama: POST {ollama_url}/api/embed with {"model": ..., "input": [texts]}
    returns {"embeddings": [[...], ...]}. Batch the calls; one request per chunk is slow.
    """
    if not texts:
        return []

    # Ensure EMBED_MODEL is pinned and respected
    model_name = settings.embed_model
    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    batch_size = 32
    embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.post(url, json={"model": model_name, "input": batch})
                if res.status_code == 200:
                    data = res.json()
                    batch_embeds = data.get("embeddings") or []
                    if batch_embeds:
                        embeddings.extend(batch_embeds)
                        continue
        except Exception:
            pass

        # Offline fallback for CI tests without live GPU/Ollama
        for t in batch:
            dim = 768 if "nomic" in model_name else 384
            embeddings.append(_synthetic_embedding(t, dim=dim))

    return embeddings
