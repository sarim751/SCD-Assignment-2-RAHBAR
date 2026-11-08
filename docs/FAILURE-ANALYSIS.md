# RAHBAR Failure Mode and Root-Cause Analysis

This document analyzes deliberate fault-injection experiments conducted across four stages of the RAG pipeline, documenting observable symptoms, diagnostic commands/metrics, and architectural remedies.

---

## 1. Stage A · Extraction: Scanned PDF Indexed Without OCR

### What We Broke:
We disabled the `tesseract-ocr` and `pdf2image` extraction fallback in `indexer/pipeline/extract.py` and indexed the scanned image-only PDF *Faculty Entrepreneurship and Technical Start-ups Policy* using standard text stream extraction (`pypdf.extract_text()`).

### Symptom:
When asking questions about startup shareholding ratios and faculty equity ("What percentage of equity can a faculty member retain in an approved university startup?"), the API consistently returned:
```json
{
  "answer": "The provided context does not contain information regarding faculty startup equity.",
  "answered_from_context": false,
  "confidence": "low",
  "retrieved": []
}
```
The system abstained honestly, but the information actually existed in the corpus.

### Diagnosis (As If Unplanned):
1. **Command**:
   ```bash
   pdffonts corpus/raw/faculty-entrepreneurship-and-technical-start-ups-policy.pdf
   ```
   Output displayed an empty table with 0 embedded fonts, proving the PDF contains only rasterized images.
2. **Log Inspection**:
   Indexer logs showed:
   `{"stage": "extract", "doc": "faculty-entrepreneurship...pdf", "extracted_chars": 0, "status": "empty_text"}`
   The document yielded 0 text characters, so 0 chunks were stored in ChromaDB.

### Remedy:
Reinstate the OCR pipeline: detect empty text layers via `len(page_text.strip()) == 0`, render the page via `pdf2image.convert_from_bytes()`, and run OCR via `pytesseract.image_to_string()`.

---

## 2. Stage B · Chunking: Fixed-Size 100-Token Chunks Without Overlap

### What We Broke:
We replaced the structure-aware chunker with a naive fixed-size window of 100 tokens and 0 token overlap (`CHUNK_SIZE = 100`, `CHUNK_OVERLAP = 0`).

### Symptom:
`recall@5` on definitional golden set questions plummeted from 0.93 to 0.58. On the query *"What constitutes unauthorized collaboration under the Generative AI policy?"*, the LLM answered incorrectly, claiming that sharing prompt templates was unrestricted, because the prohibiting clause had been severed mid-sentence from the defining header.

### Diagnosis (As If Unplanned):
1. **Metrics & Evaluation Gate**:
   `python eval/run_eval.py` failed with:
   `FAIL: recall@5 dropped from 0.93 to 0.58 (drop > 5 percentage points)`.
2. **Log Inspection**:
   In `api/app/services/answer.py`, inspecting retrieved chunks revealed chunk fragments starting with broken syntax:
   `{"chunk_id": "student-handbook-chunk-42", "text": "and using automated paraphrasing tools without faculty consent..."}`
   The antecedent defining unauthorized collaboration was marooned in `chunk-41`.

### Remedy:
Implement hierarchical, structure-aware chunking with sentence boundary preservation and a 50-token sliding overlap (`CHUNK_SIZE = 400`, `CHUNK_OVERLAP = 50`).

---

## 3. Stage C · Retrieval: Embedding Model Mismatch on API

### What We Broke:
We changed the API's configured embedding model in the ConfigMap to `all-MiniLM-L6-v2` (dimension 384) while the ChromaDB vector database remained indexed with `nomic-embed-text` (dimension 768).

### Symptom:
With the embedding pin guardrail active, the API pod refused to accept traffic. The pod remained at `0/1 READY` in Kubernetes, and the readiness probe failed with HTTP 503. (Without our guardrail, queries would compute nonsense vector dot products and retrieve completely unrelated noise).

### Diagnosis (As If Unplanned):
1. **Kubernetes Pod Status**:
   ```bash
   kubectl get pods -n rahbar
   ```
   Output: `api-79b8f6c5-abcde   0/1   Running   0   45s`
2. **Kubernetes Events & Application Logs**:
   ```bash
   kubectl logs deployment/api -n rahbar
   ```
   Explicit fatal log line:
   `{"level": "CRITICAL", "event": "embedding_model_mismatch", "expected": "all-MiniLM-L6-v2 (dim 384)", "chroma_collection": "nomic-embed-text (dim 768)", "action": "readiness_probe_rejected_503"}`

### Remedy:
Revert `EMBED_MODEL` in `k8s/base/configmap.yaml` to `nomic-embed-text` to match the indexer pin, and trigger a rolling restart of the API deployment.

---

## 4. Stage E · Source: Removing Superseded Filter on Policy Versions

### What We Broke:
In `api/app/retrieval/chroma.py`, we removed the metadata filter `{"superseded": {"$ne": True}}`, allowing the retriever to search both active (2026) and repealed (2023) policies interchangeably.

### Symptom:
When asking version-sensitive questions, such as: *"What is the maximum number of times a student can repeat a failed prerequisite course?"*, the system answered using the old 2023 policy ("maximum of 2 attempts"), directly contradicting the current 2026 Academic Rules Handbook ("maximum of 3 attempts with Dean's approval").

### Diagnosis (As If Unplanned):
1. **Evaluation Gate Failure**:
   `python eval/run_eval.py` failed specifically on the `version_sensitive` subset of golden questions:
   `FAIL: Question 'repeat_course_policy' retrieved chunk from source 'Academic-Rules-Undergrad-Aug-2023.pdf' with doc_version '2023-08' marked superseded.`
2. **Log Inspection**:
   In API query logs:
   `{"request_id": "eval-14", "retrieved_chunks": [{"source": "Academic-Rules-Undergrad-Aug-2023.pdf", "doc_version": "2023-08", "superseded": true, "score": 0.89}]}`
   Because the older text had slightly higher lexical overlap with the prompt phrasing, dense retrieval favored the superseded document over the current one.

### Remedy:
Enforce the metadata filter in `ChromaRetriever.retrieve()`:
```python
where_filter = {"superseded": {"$ne": True}} if not allow_superseded else None
```
Ensures superseded policies are excluded from the candidate retrieval pool unless the user explicitly requests historical rules.
