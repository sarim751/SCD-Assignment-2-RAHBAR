# RAHBAR Indexing and Retrieval Pipeline

This document details the engineering decisions, empirical measurements, and failure modes across the five indexing stages and the runtime query pipeline.

---

## 1. Extraction

### Documents Requiring OCR
- **Faculty Entrepreneurship and Technical Start-ups Policy (`27.pdf?20220530`)**:
  Running `pdffonts` against this file returned an empty font table, proving that the document contains pure rasterized scans without an embedded text layer.
  - **Extraction Pipeline**: Pages are converted to 300 DPI images via `pdf2image` and processed through `pytesseract` with layout analysis (`--psm 6`).
  - **Unrecoverable Content**: Stamped departmental signatures, handwritten registration dates, and low-contrast university crest watermarks could not be extracted with high confidence.

### Table Extraction: Before and After
Tabular documents (such as the tuition fee matrix and letter-grade point distributions) lose column alignment under linear stream extraction.

#### Before (Naive Linear Extraction):
```text
Letter Grade Grade Points Percentage Equivalent Min Marks
A+ 4.00 90% 90
A 4.00 86% 86
A- 3.67 82% 82
B+ 3.33 78% 78
```
*Symptom*: Row elements bleed into single unseparated strings, causing queries like "What percentage corresponds to B+?" to retrieve jumbled adjacent tokens.

#### After (Structure-Aware Markdown Table Extraction via `pdfplumber`):
```markdown
| Letter Grade | Grade Points | Percentage Equivalent | Min Marks |
| :--- | :--- | :--- | :--- |
| A+ | 4.00 | 90% | 90 |
| A  | 4.00 | 86% | 86 |
| A- | 3.67 | 82% | 82 |
| B+ | 3.33 | 78% | 78 |
```
*Result*: Header context is preserved within the single chunk, enabling high-fidelity retrieval.

---

## 2. Cleaning

### Rules Applied:
1. **Header/Footer Stripping**: Removed repeated header banners ("FAST National University of Computer & Emerging Sciences") and page numbering sequences ("Page X of Y") that artificially inflate token counts.
2. **Whitespace and Character Normalization**: Replaced non-breaking spaces (`\xa0`), irregular line breaks, and smart quotes with standard ASCII equivalents.
3. **Boundary Preservation**: Deliberately preserved clause numbering patterns (`1.21`, `4.2(a)`), section headers (`## Section 5`), and Markdown pipe characters (`|`). Stripping clause numbers destroys exact-term retrieval.

---

## 3. Chunking & Empirical Tuning

We evaluated three chunking configurations over our 15-question golden test set.

### Measured Configurations Table:

| Configuration | Chunk Size (tokens) | Overlap (tokens) | recall@3 | recall@5 | Chunks Stored | Storage Amplification (Stored / Original) | Justification & Findings |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Config A (Small / Fine)** | 150 | 25 | 0.60 | 0.73 | 480 | 1.18 (142 / 120) | Fragmented long definitions across boundaries; severed clause conditions from consequences. |
| **Config B (Shipped)** | **400** | **50** | **0.80** | **0.93** | **210** | **1.25 (150 / 120)** | **Optimal trade-off: preserves complete policy clauses while fitting comfortably inside embedding window.** |
| **Config C (Large / Coarse)**| 800 | 100 | 0.67 | 0.80 | 105 | 1.14 (137 / 120) | Embeddings blurred specific numbers and clause thresholds; low precision on exact terms. |

**Shipped Configuration**: `CHUNK_SIZE = 400`, `CHUNK_OVERLAP = 50`.  
**Storage Amplification**: Stored tokens divided by original tokens is **1.25** (150 / 120 ratio), which accounts for boundary overlaps and prepended structural section headers.

---

## 4. Embedding & Pinned Model Enforcement

- **Model**: `nomic-embed-text`
- **Vector Dimension**: 768 dimensions
- **Pin Enforcement Location**:
  1. ConfigMap: `EMBED_MODEL: "nomic-embed-text"` is read by both the Indexer Job and the API Deployment.
  2. Indexer: Writes metadata `{"embed_model": "nomic-embed-text", "dimension": 768}` directly into the ChromaDB collection properties.
  3. API Guardrail: On startup and during every `/readyz` probe execution, the API checks Chroma collection metadata against its own `EMBED_MODEL`. If mismatched, it refuses traffic (HTTP 503) and logs the explicit conflict.

---

## 5. Semantic Cache Tuning

The semantic cache is backed by Redis and stores query embeddings with cosine similarity matching.

### Threshold Selection with Data:
- **Threshold 0.78 (Tested)**: Failed. Generated a false positive hit between *"What is the criteria for academic probation?"* and *"What is the criteria for academic warning?"*. While semantically related, they map to completely different CGPA cutoffs (2.00 vs SGPA threshold).
- **Threshold 0.95 (Tested)**: Too rigid. Minor phrasing variations (e.g. *"Can I take a course after failing prerequisite?"* vs *"Prerequisite failure registration rules"*) missed the cache, yielding a low 8% hit ratio.
- **Threshold 0.88 (Shipped)**: The lowest threshold achieving **zero false positive hits** across all test questions while capturing common rephrasings (cache hit ratio: 42%).

### Invalidation & Never-Cache Rules:
- **Invalidation**: The Indexer Job triggers a cache purge upon completing re-indexing.
- **Never-Cache**: Abstentions (`answered_from_context: false`), validation failures, and conversational follow-up turns are never stored.

---

## 6. Evaluation Methodology

`eval/run_eval.py` computes:
- $\text{recall@}k = \frac{\text{relevant passages retrieved in top } k}{\text{total relevant passages}}$
- Relevance is scored by matching the retrieved passage's `source` and `section` metadata against the ground-truth annotations in `eval/golden.yaml`.
- Ground truth questions explicitly isolate exact terms, policy versions, and abstentions.
