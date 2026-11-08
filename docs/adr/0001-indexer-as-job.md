# ADR 0001: Indexer as a Kubernetes Batch Job rather than API Startup

## Status
Accepted · 2026-11-08

## Context
In a Retrieval-Augmented Generation (RAG) system running on Kubernetes, raw documents must be ingested, cleaned, chunked, embedded, and stored into a vector database (ChromaDB). A seemingly intuitive approach is to run the indexing logic during the FastAPI application's startup lifecycle (`@app.on_event("startup")` or lifespan handler). 

However, RAHBAR requires:
1. A 3-replica API Deployment for high availability and zero-downtime rolling updates (`maxSurge: 1`, `maxUnavailable: 0`).
2. A single persistent ChromaDB volume (`ReadWriteOnce` PVC via StatefulSet).
3. Independent scaling, scheduled re-indexing, and clean separation between data writers and data readers.

If indexing runs inside API startup, severe architectural failures emerge:
1. **Concurrent Writer Race Condition**: 3 API pods boot simultaneously and race to write to the single Chroma persistent volume. Three concurrent writers half-way through different documents corrupt the underlying SQLite/HNSW vector indices, yielding an inconsistent store that matches no valid corpus state.
2. **Restart Amplification**: Every pod restart—triggered by an OOMKill, rolling rollout, node drain, or readiness probe hiccup—triggers the full multi-minute PDF extraction, OCR, and embedding cycle across all 40+ pages.
3. **The Readiness Lie & Rollout Deadlock**: If an API pod reports ready before indexing completes, it serves empty retrievals and false abstentions. If it waits until indexing finishes, startup exceeds Kubernetes probe timeouts (`startupProbe`/`livenessProbe`), causing the kubelet to kill the pod halfway through. Furthermore, during a rolling update, Kubernetes would terminate working old pods before new ones finish embedding, causing cluster-wide downtime.
4. **Coupled Release Cycles ("The Friday Failure")**: Re-indexing when policies update would require a full redeployment of the API service image.

## Decision
We decouple the indexing pipeline entirely from the API service:
- **API (Reader only)**: Runs as a 3-replica Deployment. It only reads from ChromaDB and never writes to it. Its `/readyz` probe verifies that Chroma is reachable and the collection is populated.
- **Indexer (Writer only)**: Runs as an ephemeral Kubernetes `Job` (`batch/v1`) with `restartPolicy: OnFailure` and `backoffLimit: 3`, triggered on-demand via `kubectl create job --from=cronjob/reindex` or scheduled weekly as a `CronJob`.

## Options Considered
1. **Indexing in API Lifespan/Startup**: Rejected due to writer contention, corrupt volume risk, and rollout deadlocks described above.
2. **InitContainer on API Pods**: Rejected because InitContainers run on *every* replica pod, so 3 replicas still attempt concurrent writes or sequential redundant indexing on pod creation.
3. **Separate Indexer Job + CronJob**: Selected. The Job runs as a one-shot batch process, extracts, chunks, embeds, records the embedding model pin into collection metadata, invalidates the semantic cache, and terminates cleanly with exit code 0.

## Consequences
- **Positive**: Zero writer contention on ChromaDB. Zero-downtime re-indexing without API restarts. The API startup time remains under 2 seconds.
- **Negative**: Re-indexing must be coordinated via Kubernetes Job manifests or CronJob triggers rather than automatic in-process reloads.
- **Watch items**: The API readiness probe must return HTTP 503 until the Indexer Job has populated the Chroma collection and verified the `EMBED_MODEL` pin.
