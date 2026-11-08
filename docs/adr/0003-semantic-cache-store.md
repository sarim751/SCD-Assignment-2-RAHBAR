# ADR 0003: Semantic Cache Implementation Using Redis over ChromaDB

## Status
Accepted · 2026-11-08

## Context
Repetitive student queries (e.g., "What is the minimum CGPA required for probation removal?", "When is the fee refund deadline?") impose redundant inference costs and latency on LLM providers. An in-memory semantic cache evaluates incoming queries against previously answered queries using cosine similarity over embeddings.

We need a dedicated storage and query engine for this cache layer, considering:
1. Operational complexity and resource footprints in a Kubernetes environment.
2. Latency of cache lookup versus standard vector retrieval.
3. Cache invalidation semantics when the corpus is re-indexed.
4. Support for strict "never-cache" guardrails (abstentions, prompt injections, and conversation follow-up turns).

## Decision
We deploy **Redis** (`redis:7.4-alpine`) as the semantic cache backing store alongside ChromaDB:
- Redis stores cached query embeddings, generated responses, and citation payloads with native TTL expiration.
- In-memory similarity computation or key lookups execute in under 2ms.
- **Cache Invalidation**: When the Indexer Job completes a re-indexing run, it issues an atomic flush/invalidation signal to Redis, ensuring stale answers from superseded policies are purged instantly.
- **Never-Cache Guardrails**:
  - Responses with `answered_from_context: false` (abstentions) are NEVER cached to prevent persisting temporary retrieval gaps.
  - Follow-up turns within conversational sessions are bypassed to avoid context drift.
  - Queries falling below the semantic similarity threshold (`CACHE_THRESHOLD = 0.88`) trigger a cache miss.

## Options Considered
1. **Secondary ChromaDB Collection**: While reusing Chroma reduces container types, Chroma's write latency, disk persistence model, and lack of native TTLs make dynamic cache entry expiration and high-throughput write operations sluggish.
2. **In-Memory Python Dictionary in API Pod**: Rejected because with 3 API replicas, each replica maintains a fractured, non-shared cache state that evaporates on pod restart.
3. **Dedicated Redis Deployment + PVC**: Selected. Provides a shared, sub-millisecond cache across all 3 API replicas, persistent across pod crashes, and simple to flush globally during re-indexing.

## Consequences
- **Positive**: Blazing fast cache hit latency (<5ms vs 1200ms LLM generation). Clean decoupling between vector knowledge base (Chroma) and conversational cache (Redis).
- **Negative**: Adds another Kubernetes workload (Deployment + PVC + Service) to manage in the `rahbar` namespace.
- **Watch items**: NetworkPolicy must strictly restrict Redis access to the API pods only.
