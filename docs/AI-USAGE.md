# AI Usage and Attribution

Under the course policy for Assignment 02 (CS4032), this document honestly discloses the use of AI tools throughout the design, development, and debugging of RAHBAR.

---

## Tool Disclosure Table

| Tool | What It Wrote or Shaped | What We Changed Afterwards, and Why |
| :--- | :--- | :--- |
| **Google Antigravity (Gemini)** | Scaffolded baseline Kubernetes manifests (`k8s/base/`) including Deployments, StatefulSet, Services, Ingress, and NetworkPolicy rules. | Updated `api-deployment.yaml` probes to decouple `/healthz` (liveness) from `/readyz` (readiness) to eliminate dependency storms. Configured explicit `emptyDir` mounts for `/tmp` to allow non-root `readOnlyRootFilesystem: true` operation. |
| **Google Antigravity (Gemini)** | Drafted the multi-stage Dockerfiles for API, Indexer, and UI microservices. | Refined `ui/Dockerfile` to use `nginxinc/nginx-unprivileged:1.27-alpine` with non-root UID 101, and installed `tesseract-ocr` and `poppler-utils` in `indexer/Dockerfile` to support OCR image processing. |
| **Google Antigravity (Gemini)** | Generated regex patterns and structural parsing templates for `indexer/pipeline/chunk.py` and `clean.py`. | Tuned chunk size from 150 to 400 tokens and overlap to 50 tokens based on empirical measurements of `recall@5` against the golden test set in `docs/PIPELINE.md`. |
| **Google Antigravity (Gemini)** | Assisted with the GitHub Actions workflow templates (`ci.yml` and `cd.yml`). | Enforced `needs:` dependencies across all publishing and deployment jobs, pinned actions by version, and added ephemeral Kind cluster e2e evaluation gates. |
| **Google Antigravity (Gemini)** | Structured the four Architecture Decision Records (`0001` to `0004`) and Failure Analysis scenarios. | Verified all diagnostic CLI commands (`pdffonts`, `kubectl top`, `kubectl describe pod`) against actual local cluster behaviors and documented exact line-level references. |
