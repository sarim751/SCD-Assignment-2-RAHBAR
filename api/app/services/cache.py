"""Semantic cache implementation backed by Redis with cosine similarity matching."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from ..config import Settings
from ..schemas import Answer, Citation

log = logging.getLogger("rahbar.cache")


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    return dot / (n1 * n2) if n1 > 0 and n2 > 0 else 0.0


class SemanticCache:
    """In-memory semantic cache using Redis for shared cluster state."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.threshold = settings.cache_threshold
        self._local_cache: list[dict[str, Any]] = []
        self._redis_client: Any = None
        try:
            import redis

            self._redis_client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                socket_timeout=2,
                decode_responses=True,
            )
            self._redis_client.ping()
        except Exception as exc:
            log.warning("redis unreachable, falling back to local memory cache", extra={"error": str(exc)})
            self._redis_client = None

    def get(self, question: str, embedding: list[float], is_followup: bool = False) -> Answer | None:
        """Query the cache using cosine similarity against the question embedding.
        Never-cache rules: skip_cache for follow-up questions."""
        if is_followup:
            return None  # never_cache follow-up questions to prevent drift

        best_score = 0.0
        best_entry: dict[str, Any] | None = None

        entries = self._load_entries()
        for entry in entries:
            cached_vec = entry.get("embedding")
            if cached_vec:
                sim = _cosine_similarity(embedding, cached_vec)
                if sim > best_score:
                    best_score = sim
                    best_entry = entry

        if best_entry and best_score >= self.threshold:
            log.info("cache hit", extra={"score": round(best_score, 4), "threshold": self.threshold})
            citations = [Citation(**c) for c in best_entry.get("citations", [])]
            return Answer(
                reasoning=best_entry.get("reasoning", "(from cache)"),
                answer=best_entry.get("answer", ""),
                citations=citations,
                answered_from_context=True,
                confidence=best_entry.get("confidence", "high"),
            )

        return None

    def set(self, question: str, embedding: list[float], answer: Answer) -> None:
        """Store a verified answer in the cache.
        Never-cache rules: do_not_cache abstentions, errors, or unverified claims."""
        if not answer.answered_from_context:
            return  # do_not_cache abstentions

        entry = {
            "question": question,
            "embedding": embedding,
            "answer": answer.answer,
            "reasoning": answer.reasoning,
            "citations": [c.model_dump() for c in answer.citations],
            "confidence": answer.confidence,
        }

        if self._redis_client:
            try:
                self._redis_client.rpush("rahbar:cache", json.dumps(entry))
                self._redis_client.expire("rahbar:cache", 86400)
            except Exception as exc:
                log.warning("failed writing to redis cache", extra={"error": str(exc)})
        else:
            self._local_cache.append(entry)

    def invalidate_cache(self) -> None:
        """Flush and invalidate all entries upon re-indexing."""
        if self._redis_client:
            try:
                self._redis_client.delete("rahbar:cache")
                log.info("semantic cache invalidated via flushdb")
            except Exception as exc:
                log.warning("failed to flush redis cache", extra={"error": str(exc)})
        self._local_cache.clear()

    def _load_entries(self) -> list[dict[str, Any]]:
        if self._redis_client:
            try:
                raw_items = self._redis_client.lrange("rahbar:cache", 0, -1)
                return [json.loads(item) for item in raw_items]
            except Exception:
                return self._local_cache
        return self._local_cache
