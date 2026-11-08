"""Turns a question into an AskResponse. This is where the query pipeline lives."""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..schemas import AskResponse, Citation, Passage, RetrievedRef
from .cache import SemanticCache

log = logging.getLogger("rahbar.answer")

# Module-level cache singleton
_cache_instance: SemanticCache | None = None


def get_cache(settings: Settings) -> SemanticCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache(settings)
    return _cache_instance


def _validate_citations(citations: list[Citation], passages: list[Passage]) -> list[Citation]:
    """Every citation must match a chunk actually retrieved for that request. Unmatched citations are dropped."""
    valid: list[Citation] = []
    for cite in citations:
        c_src = cite.source.lower().strip()
        c_sec = cite.section.lower().strip()
        for p in passages:
            p_src = p.source.lower().strip()
            p_sec = p.section.lower().strip()
            if (not c_src or c_src in p_src or p_src in c_src) and (not c_sec or c_sec in p_sec or p_sec in c_sec):
                valid.append(cite)
                break
    return valid


def answer_question(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    is_followup: bool = False,
) -> AskResponse:
    rid = request_id_var.get()
    cache = get_cache(settings)

    # 1. Semantic Cache check (skip for follow-up questions)
    cached_answer = None
    if retriever.name == "chroma" and hasattr(retriever, "embed_query"):
        query_vec = getattr(retriever, "embed_query")(question)
        cached_answer = cache.get(question, query_vec, is_followup=is_followup)

    if cached_answer:
        log.info("serving answer from semantic cache", extra={"request_id": rid})
        return AskResponse(
            request_id=rid,
            answer=cached_answer.answer,
            citations=cached_answer.citations,
            answered_from_context=cached_answer.answered_from_context,
            confidence=cached_answer.confidence,
            retrieved=[],
            provider=f"{provider.name}-cache",
        )

    # 2. Retrieve top-k chunks from Chroma (excludes superseded documents by default)
    passages = retriever.retrieve(question, k=settings.top_k)

    # 3. Check relevance floor: if highest score does not clear relevance floor, abstain honestly
    max_score = max([p.score for p in passages], default=0.0)
    if passages and max_score < settings.relevance_floor:
        log.info(
            "retrieval below relevance floor, abstaining honestly",
            extra={"max_score": max_score, "floor": settings.relevance_floor},
        )
        return AskResponse(
            request_id=rid,
            answer="I cannot find sufficient evidence in the verified university documents to answer your question.",
            citations=[],
            answered_from_context=False,
            confidence="low",
            retrieved=[
                RetrievedRef(
                    chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, score=p.score
                )
                for p in passages
            ],
            provider=provider.name,
        )

    # 4. Generate schema-constrained answer
    answer = provider.generate(question, passages)

    # 5. Citation grounding validation: ensure citations match actual retrieved chunks
    grounded_citations = _validate_citations(answer.citations, passages)

    # 6. Store in cache if safe: never cache abstentions, errors, or follow-ups
    if answer.answered_from_context and retriever.name == "chroma" and hasattr(retriever, "embed_query"):
        query_vec = getattr(retriever, "embed_query")(question)
        cache.set(question, query_vec, answer)

    log.info(
        "answered",
        extra={
            "retriever": retriever.name,
            "provider": provider.name,
            "top_k": settings.top_k,
            "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "answered_from_context": answer.answered_from_context,
            "citations_count": len(grounded_citations),
        },
    )

    return AskResponse(
        request_id=rid,
        answer=answer.answer,
        citations=grounded_citations,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=[
            RetrievedRef(
                chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, score=p.score
            )
            for p in passages
        ],
        provider=provider.name,
    )
