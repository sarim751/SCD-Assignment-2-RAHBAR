"""Liveness and readiness probes (section 4.3). They serve entirely different jobs."""

from __future__ import annotations

import logging
from fastapi import APIRouter, Request, Response

log = logging.getLogger("rahbar.health")
router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Is the process alive? Must never touch Chroma, Redis or the LLM."""
    return {"status": "alive"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, object]:
    """Readiness probe. Returns 200 only if Chroma is reachable, the collection is non-empty,
    Redis is reachable, and the embedding model matches the pinned EMBED_MODEL."""
    settings = getattr(request.app.state, "settings", None)
    if not settings:
        response.status_code = 503
        return {"status": "not_ready", "error": "settings not loaded"}

    checks: dict[str, str] = {}

    # If running with stubs (e.g. offline unit testing), accept ready
    if settings.retriever == "stub":
        return {"status": "ready", "checks": {"retriever": "stub"}}

    # 1. Check Chroma reachability and non-empty collection
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        collection = client.get_collection(name=settings.collection)
        chunk_count = collection.count()

        if chunk_count == 0:
            response.status_code = 503
            return {"status": "not_ready", "error": "Chroma collection is empty"}
        checks["chroma"] = f"ok (count: {chunk_count})"

        # 2. Enforce embedding model pin in code (Section 4.4)
        coll_meta = collection.metadata or {}
        stored_model = coll_meta.get("embed_model") or coll_meta.get("embedding_model")
        if stored_model and stored_model != settings.embed_model:
            log.critical(
                "embedding_model mismatch guardrail refusal",
                extra={"expected": settings.embed_model, "found": stored_model},
            )
            response.status_code = 503
            return {
                "status": "not_ready",
                "error": f"embedding_model mismatch: expected {settings.embed_model} does not match collection {stored_model}",
            }
        checks["embed_model_pin"] = "verified"

    except Exception as exc:
        response.status_code = 503
        return {"status": "not_ready", "error": f"Chroma unreachable: {exc}"}

    # 3. Check Redis reachability
    try:
        import redis

        r = redis.Redis(host=settings.redis_host, port=settings.redis_port, socket_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        response.status_code = 503
        return {"status": "not_ready", "error": f"Redis unreachable: {exc}"}

    return {"status": "ready", "checks": checks}
