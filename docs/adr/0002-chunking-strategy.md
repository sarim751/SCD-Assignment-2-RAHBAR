# ADR 0002: Structure-Aware Hierarchical Chunking with Boundary Overlap

## Status
Accepted · 2026-11-08

## Context
Standard RAG pipelines frequently employ naive character or token chunking with fixed window sizes (e.g. 200 tokens with 20-token sliding windows). When applied to university policy documents, handbooks, and regulatory statutes (such as FAST-NUCES Undergraduate Rules), naive chunking fails severely:
1. Long multi-clause definitions (e.g. academic integrity violations, Generative AI policies) are split down the middle, separating conditions from their penalties.
2. Tables carrying structured data (such as tuition fees, refund schedules, and grade point equivalencies) are severed mid-row, producing illegible column fragments.
3. Metadata such as section titles, clause numbers, and document versions are lost when boundaries fall arbitrarily.

## Decision
We implement a structure-aware hierarchical chunker:
1. **Structural Segmentation**: Documents are parsed by section and clause boundaries (e.g., regex pattern matching on `Section X.Y`, clause headers, numbered lists, and Markdown headers).
2. **Sub-chunking with Overlap**: If a section exceeds the target `CHUNK_SIZE` (400 tokens), it splits on paragraph boundaries (`\n\n`), then sentence boundaries, retaining a 50-token `CHUNK_OVERLAP` between consecutive chunks.
3. **Table Preservation**: Markdown and HTML tables are extracted intact as standalone single chunks whenever they fit within the maximum token limit.
4. **Rich Metadata Attachment**: Every produced chunk carries structured metadata: `source` (document filename), `section` (clause or heading name), `page` (original page number), `doc_version` (policy revision date), and `superseded` (`true` or `false`).

## Options Considered
1. **Fixed-size 100-token window with 0 overlap**: Produces 0 storage amplification but tears apart policy clauses and yields disastrous `recall@5` on definitional queries.
2. **Recursive character chunking without document metadata**: Simple to implement but loses versioning information, making it impossible to filter out superseded 2023 regulations in favor of 2026 revisions.
3. **Structure-Aware Chunking (Target: 400 tokens, Overlap: 50 tokens)**: Selected. Yields optimal balance between chunk coherence, semantic density, and storage amplification.

## Consequences
- **Positive**: Policy clauses remain semantically unified. `recall@5` increases substantially on complex regulatory queries.
- **Negative**: Storage amplification increases by approximately 20-25% due to boundary overlaps and metadata overhead.
- **Watch items**: Table extraction must handle complex multi-line cells without column misalignment.
