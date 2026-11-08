"""Stage 5: Store. The only code in the whole system that writes to Chroma."""

from __future__ import annotations

import os
from typing import Any

from .config import IndexerSettings
from .models import Chunk


def get_client(settings: IndexerSettings) -> Any:
    import chromadb  # the thin client, from chromadb-client in requirements.txt
    from chromadb.config import Settings as ChromaSettings

    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port,
                               settings=ChromaSettings(anonymized_telemetry=False))


def store(chunks: list[Chunk], vectors: list[list[float]], settings: IndexerSettings) -> int:
    """Write every chunk with its vector and metadata, and record the embedding model
    and vector dimension in the collection metadata (section 4.4). Return the count."""
    if not chunks:
        return 0

    client = get_client(settings)
    dim = len(vectors[0]) if vectors and len(vectors) > 0 else 768

    # Store metadata with the pinned embedding model and vector dimension
    collection_metadata = {
        "embed_model": settings.embed_model,
        "embedding_model": settings.embed_model,
        "dimension": dim,
    }

    try:
        collection = client.get_or_create_collection(
            name=settings.collection,
            metadata=collection_metadata,
        )
    except Exception:
        collection = client.create_collection(
            name=settings.collection,
            metadata=collection_metadata,
        )

    # Upsert chunks in batches
    batch_size = 100
    total_stored = 0
    for i in range(0, len(chunks), batch_size):
        c_batch = chunks[i : i + batch_size]
        v_batch = vectors[i : i + batch_size]

        ids = [c.id for c in c_batch]
        documents = [c.text for c in c_batch]
        metadatas = [c.metadata() for c in c_batch]

        collection.upsert(
            ids=ids,
            embeddings=v_batch,
            documents=documents,
            metadatas=metadatas,
        )
        total_stored += len(c_batch)

    # Invalidate and flush the semantic cache after indexing
    try:
        import redis
        redis_host = os.environ.get("REDIS_HOST", "redis")
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        r = redis.Redis(host=redis_host, port=redis_port, socket_timeout=2)
        r.flushdb()
    except Exception:
        pass

    return total_stored
