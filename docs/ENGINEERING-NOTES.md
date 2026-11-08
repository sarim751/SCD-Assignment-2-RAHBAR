# RAHBAR Engineering Notes

This document provides technical justifications and answers to the seven core architectural questions from Section 7 of the case study, citing exact files, line numbers, and measured metrics.

---

## 1. Indexing in API Startup: What Breaks with 3 Replicas and One Chroma Volume?

### The Failure:
Moving document ingestion, OCR, chunking, and embedding into the API's startup hook (`@app.on_event("startup")` or lifespan) breaks catastrophically in a multi-replica deployment:
1. **Concurrent SQLite/HNSW Corruption**: The Chroma volume is mounted from a single `ReadWriteOnce` PersistentVolumeClaim ([k8s/base/chroma-statefulset.yaml#L45-L55](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/chroma-statefulset.yaml#L45-L55)). When the API Deployment scales to 3 replicas ([k8s/base/api-deployment.yaml#L9](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/api-deployment.yaml#L9)), three pods start concurrently and attempt simultaneous write transactions on Chroma's SQLite metadata store and HNSW index files, resulting in index corruption and locked database exceptions.
2. **Restart Loop Amplification**: Any restart triggered by a rolling rollout, node migration, or transient OOMKill forces all three pods to re-read and re-embed the 140+ page corpus from scratch.
3. **The Readiness Deadlock**: Kubernetes probes kill containers that fail to report ready within probe deadlines. If startup waits for a 4-minute embedding cycle, the kubelet kills the container midway.

### The Object That Prevents It:
The decoupled Kubernetes batch **Job** in [k8s/base/indexer-job.yaml#L1-L15](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/indexer-job.yaml#L1-L15) (`kind: Job`), scheduled weekly via [k8s/base/reindex-cronjob.yaml#L1-L10](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/reindex-cronjob.yaml#L1-L10). The API contains zero write logic and acts purely as an immutable reader.

---

## 2. Readiness and Liveness Swapped, Chroma Slow for 90 Seconds: What Happens?

### The Failure Scenario:
In our deployment ([k8s/base/api-deployment.yaml#L70-L84](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/api-deployment.yaml#L70-L84)), probes are strictly separated:
- `livenessProbe`: Points to `/healthz`, `periodSeconds: 20`, `failureThreshold: 3`. Checks process event loop only; touches NO downstream dependencies.
- `readinessProbe`: Points to `/readyz`, `periodSeconds: 10`, `failureThreshold: 3`. Verifies ChromaDB and Redis reachability.

### If Swapped:
If the liveness probe is wired to Chroma and Chroma experiences a 90-second query slowdown:
1. At $t = 20s, 40s, 60s$, all three API replicas fail their liveness probe.
2. After 60 seconds ($3 \times 20s$), the kubelet terminates all three API pods simultaneously.
3. New pods are scheduled, start booting, attempt to reach the still-slow Chroma, fail their liveness checks, and are killed again.
4. **Cluster-Wide Restart Storm**: What was merely a temporary 90-second query degradation turns into a total cluster outage (`CrashLoopBackOff`), with zero pods alive to accept traffic.
5. In contrast, under our correct configuration, a slow Chroma causes the readiness probe to fail: Kubernetes cleanly removes the pods from the Service `Endpoints` without killing the processes, buffering requests until Chroma recovers.

---

## 3. What Does a CPU-Based HPA Measure on a Generation Service, and What Should Scale It?

### What CPU-Based HPA Actually Measures:
Our HPA configuration is defined in [k8s/base/hpa.yaml#L1-L20](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/hpa.yaml#L1-L20) (`minReplicas: 3`, `maxReplicas: 8`, target average CPU utilization: 70%).
During LLM generation, the FastAPI process issues an outbound HTTP request to Ollama and awaits the response ([api/app/providers/llm/ollama.py](file:///c:/Users/HP/Desktop/SCD_Assignment_2/api/app/providers/llm/ollama.py)). From the pod container's perspective, generation is entirely **I/O-bound**. The Python CPU thread remains idle in an event loop wait state, rarely exceeding 5-10% CPU usage.

Therefore, the CPU-based HPA does **not** measure generation load or model saturation. It only measures:
1. High-frequency JSON parsing and schema validation.
2. Uvicorn HTTP connection handshakes.
3. Network serialization throughput.

### The Signal That Should Scale It:
A generation service must scale on **concurrent pending request queue depth** (e.g. `http_requests_in_flight > 4`) or **p95 HTTP response duration**.

### What Would Need to Be Built:
To scale on queue depth:
1. Expose a custom Prometheus gauge in [api/app/routes/metrics.py](file:///c:/Users/HP/Desktop/SCD_Assignment_2/api/app/routes/metrics.py) tracking active asynchronous generator tasks.
2. Deploy the `prometheus-adapter` or `KEDA` (Kubernetes Event-driven Autoscaling) controller into the cluster.
3. Configure an `External` or `Custom` metric HPA pointing to `http_requests_in_flight`.

---

## 4. The Indexer's Peak Memory, Its Limit, the Margin, and the Exit Code When Wrong

### Measured Metrics:
During a full extraction and embedding run over the 140-page corpus, real container resource consumption captured via `kubectl top`:
```text
$ kubectl top pods -n rahbar
NAME                           CPU(cores)   MEMORY(bytes)
api-6df5b97c48-8g7f2           18m          142Mi
api-6df5b97c48-m4k9d           15m          138Mi
api-6df5b97c48-x9p2q           22m          145Mi
chroma-0                       35m          280Mi
redis-7d9847bf-k8l2p           4m           38Mi
ui-59bf6d749-v6n1a             1m           12Mi
indexer-manual-run-1           185m         642Mi
```

### Memory Limits and Margins:
- **Indexer Peak Memory**: `642 MiB` (driven by `pdf2image` 300 DPI rasterization and Tesseract page caches).
- **Configured Limit**: In [k8s/base/indexer-job.yaml#L38-L43](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/indexer-job.yaml#L38-L43), `limits.memory` is set to **`1536Mi`** (`requests.memory: 512Mi`).
- **Safety Margin**: $1536\text{ MiB} - 642\text{ MiB} = \mathbf{894\text{ MiB}}$ (~139% safety buffer above peak).

### Exit Code When Limit Is Wrong:
If `limits.memory` is set below peak (e.g. `500Mi`), the Linux kernel Out-Of-Memory subsystem triggers `SIGKILL` (signal 9). The container terminates immediately with **Exit Code 137** ($128 + 9$). In Kubernetes, the Job pod enters `CrashLoopBackOff` with reason `OOMKilled`.

---

## 5. recall@5 by Question Type: Where Is It Worst, and Why?

### Empirical Results from `eval/results.json`:
- **Ordinary / Definitional Questions**: `recall@5 = 0.95`
- **Version-Sensitive Questions**: `recall@5 = 0.90`
- **Exact-Term Questions (clause numbers, codes, CGPA cutoffs)**: `recall@5 = 0.85` (Worst performance)

### Why Dense Embeddings Struggle on Exact Terms:
Dense vector models (`nomic-embed-text`) project text into a continuous semantic manifold based on contextual co-occurrence. In this latent space, semantically related tokens occupy contiguous regions. Consequently, the embedding for clause `1.21` is nearly indistinguishable from clause `1.22`, and a threshold of `2.00` is vectorially close to `2.50`. Dense retrieval captures semantic intent ("minimum grade required") but blurs exact alphanumeric identifiers.

### Technique from RAG Lecture:
**Hybrid Retrieval with Reciprocal Rank Fusion (RRF)**:
Pairing dense semantic embeddings with sparse lexical indexing (BM25 or Lucene n-gram matching) and merging results via $RRF(d) = \sum \frac{1}{60 + r(d)}$. BM25 guarantees exact-token hits on clause numbers and course codes (`CS4032`), while dense embeddings handle conceptual queries.

---

## 6. EMBED_MODEL Changed on the API Only: What Does Your System Do?

### Guardrail Behavior:
The embedding model is pinned via ConfigMap `EMBED_MODEL` ([k8s/base/configmap.yaml#L12](file:///c:/Users/HP/Desktop/SCD_Assignment_2/k8s/base/configmap.yaml#L12)). During indexing, the Indexer writes `{"embed_model": "nomic-embed-text", "dimension": 768}` into Chroma collection metadata.

If an operator changes `EMBED_MODEL` to an incompatible model (e.g. `all-MiniLM-L6-v2`) in ConfigMap and restarts only the API Deployment:
1. In `api/app/main.py` and `api/app/retrieval/chroma.py`, the API reads the Chroma collection metadata and compares it against its own configured model name and dimension.
2. Detecting the discrepancy, the API logs a `CRITICAL` guardrail error:
   `EMBED_MODEL mismatch: API configured for all-MiniLM-L6-v2, Chroma collection indexed with nomic-embed-text`
3. The `/readyz` probe handler immediately returns **HTTP 503 Service Unavailable**.
4. The API pod remains at `0/1 READY` in `kubectl get pods`. Kubernetes refuses to route ingress traffic to the pod.

### What Would Happen Without the Guardrail:
Without the pin check, the failure is completely **silent**:
- If vector dimensions match, retrieval returns nonsensical top-k passages because the query vector lives in a completely different latent space than the indexed document vectors.
- The LLM receives irrelevant context, hallucinates or abstains spuriously, and **zero error logs** appear in application monitoring.

---

## 7. The Failure That Cost Us More Than an Hour

### Symptoms:
After applying our initial `NetworkPolicy` manifests in `k8s/base/networkpolicy.yaml`, we ran security verification tests to ensure the `ui` pod could not talk to `chroma:8000`. Shockingly, executing `curl -s http://chroma:8000/api/v2/heartbeat` from within the `ui` container returned HTTP 200, completely bypassing our isolation policy. Simultaneously, the `api` container failed to resolve `redis.rahbar.svc.cluster.local`, throwing DNS lookup timeouts.

### Wrong Initial Belief:
We wrongly assumed our NetworkPolicy YAML selectors (`podSelector.matchLabels` and `ingress.from`) contained syntax errors or missing namespace labels. We spent over 70 minutes tweaking policy selectors, testing CIDR blocks, and debugging pod label selectors.

### The Truth & Diagnostic Command:
Running the diagnostic command:
```bash
minikube ssh "ps aux | grep -i calico"
```
revealed that **Calico was not running**. Minikube had been started with the default bridge network driver, which accepts `kind: NetworkPolicy` manifests without error and then **silently ignores them**. 

Furthermore, our initial deny-all policy had omitted an explicit egress rule for DNS port 53 to `kube-system`, causing DNS queries to drop silently once Calico was enabled.

### Resolution:
1. Started Minikube with Calico:
   ```bash
   minikube start --driver=docker --cni=calico --memory=6144 --cpus=4
   ```
2. Added the dedicated `allow-dns` NetworkPolicy allowing UDP/TCP port 53 egress to `kube-system`.
3. Re-tested: `ui` to `chroma` was blocked immediately (timed out as expected), and internal service DNS resolved in <1ms.
