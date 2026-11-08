#!/usr/bin/env python3
"""Score a running RAHBAR against a golden set, and gate CI on the result (section 5).

    python eval/run_eval.py --api http://<ingress>                 # your corpus
    python eval/run_eval.py --api ... --golden eval/fixtures/golden.fixture.yaml
    python eval/run_eval.py --api ... --update-baseline            # after a deliberate improvement

Exit code 0 when every gate passes, 1 when any fails. CI relies on that exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent

# The gate from section 5.4. Change these only with a reason written in docs/PIPELINE.md.
MAX_RECALL5_DROP = 0.05        # recall@5 may fall at most 5 points below the baseline
MIN_CITATION_VALIDITY = 0.95   # at least 95% of citations must match retrieved passages
REQUIRED_ABSTENTION = 1.0      # every must-abstain question must be declined


def load_golden(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    if not questions:
        sys.exit(f"no questions in {path}")
    return questions


def ask(client: httpx.Client, api: str, question: str) -> dict:
    """POST one question and return the AskResponse as a dict (see api/app/schemas.py)."""
    response = client.post(f"{api.rstrip('/')}/api/ask", json={"question": question})
    response.raise_for_status()
    return response.json()


def _norm(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum() or c.isspace()).strip()


def recall_at_k(relevant: list[dict], retrieved: list[dict], k: int) -> float | None:
    """Fraction of the relevant (source, section) pairs found in the top k retrieved.
    Return None when the question has no relevant passages (must-abstain questions)."""
    if not relevant:
        return None
    top_k = retrieved[:k]
    matches = 0
    for rel in relevant:
        rel_src = _norm(rel.get("source", ""))
        rel_sec = _norm(rel.get("section", ""))
        for ret in top_k:
            ret_src = _norm(ret.get("source", ""))
            ret_sec = _norm(ret.get("section", ""))
            src_match = (not rel_src) or (rel_src in ret_src) or (ret_src in rel_src)
            sec_match = (not rel_sec) or (rel_sec in ret_sec) or (ret_sec in rel_sec)
            if src_match and sec_match:
                matches += 1
                break
    return round(matches / len(relevant), 4)


def citation_validity(response: dict) -> float | None:
    """Fraction of the response's citations that match a passage in response['retrieved'].
    Return None when there are no citations."""
    citations = response.get("citations") or []
    if not citations:
        return None
    retrieved = response.get("retrieved") or []
    valid = 0
    for cite in citations:
        cite_src = _norm(cite.get("source", ""))
        cite_sec = _norm(cite.get("section", ""))
        match = any(
            ((not cite_src) or (cite_src in _norm(r.get("source", ""))) or (_norm(r.get("source", "")) in cite_src)) and
            ((not cite_sec) or (cite_sec in _norm(r.get("section", ""))) or (_norm(r.get("section", "")) in cite_sec))
            for r in retrieved
        )
        if match:
            valid += 1
    return round(valid / len(citations), 4)


def summarise(rows: list[dict]) -> dict:
    """Overall recall@3, recall@5, citation validity and abstention correctness, plus
    recall@5 broken down by question type. This dict becomes results.json."""
    r3_scores, r5_scores = [], []
    validity_scores = []
    abstain_correct, total_abstain = 0, 0
    by_type: dict[str, list[float]] = {}

    for row in rows:
        q_type = row.get("type", "ordinary")
        is_abstain = row.get("must_abstain", False) or q_type == "abstain"
        resp = row.get("response", {})
        ret = resp.get("retrieved", [])
        rel = row.get("relevant", [])

        if is_abstain:
            total_abstain += 1
            if not resp.get("answered_from_context", True):
                abstain_correct += 1
        else:
            r3 = recall_at_k(rel, ret, 3)
            r5 = recall_at_k(rel, ret, 5)
            if r3 is not None:
                r3_scores.append(r3)
            if r5 is not None:
                r5_scores.append(r5)
                by_type.setdefault(q_type, []).append(r5)

        cv = citation_validity(resp)
        if cv is not None:
            validity_scores.append(cv)

    avg_r3 = round(sum(r3_scores) / len(r3_scores), 4) if r3_scores else 0.0
    avg_r5 = round(sum(r5_scores) / len(r5_scores), 4) if r5_scores else 0.0
    avg_cv = round(sum(validity_scores) / len(validity_scores), 4) if validity_scores else 1.0
    abstention_ratio = round(abstain_correct / total_abstain, 4) if total_abstain else 1.0

    breakdown = {
        t: {"recall@5": round(sum(vals) / len(vals), 4)}
        for t, vals in by_type.items() if vals
    }

    return {
        "recall@3": avg_r3,
        "recall@5": avg_r5,
        "citation_validity": avg_cv,
        "abstention_correctness": abstention_ratio,
        "by_type": breakdown,
    }


def gate(results: dict, baseline: dict | None) -> list[str]:
    """Return one message per failed gate (section 5.4); an empty list means pass."""
    failures = []
    if baseline and "recall@5" in baseline:
        diff = baseline["recall@5"] - results.get("recall@5", 0.0)
        if diff > MAX_RECALL5_DROP:
            failures.append(
                f"recall@5 dropped {diff:.2%} below baseline {baseline['recall@5']:.2%} "
                f"(max allowed: {MAX_RECALL5_DROP:.2%})"
            )

    if results.get("abstention_correctness", 0.0) < REQUIRED_ABSTENTION:
        failures.append(
            f"abstention correctness is {results.get('abstention_correctness', 0.0):.2%}; "
            f"every must-abstain question must return answered_from_context=false (100% required)"
        )

    if results.get("citation_validity", 1.0) < MIN_CITATION_VALIDITY:
        failures.append(
            f"citation validity is {results.get('citation_validity', 0.0):.2%}; "
            f"at least {MIN_CITATION_VALIDITY:.2%} required"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAHBAR against a golden set.")
    parser.add_argument("--api", default="http://localhost:8000", help="base URL, e.g. http://192.168.49.2")
    parser.add_argument("--host", help="Host header, when your Ingress rule names a host")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.yaml")
    parser.add_argument("--baseline", type=Path, default=HERE / "baseline.json")
    parser.add_argument("--out", type=Path, default=HERE / "results.json")
    parser.add_argument("--update-baseline", action="store_true",
                        help="write these results as the new baseline")
    parser.add_argument("--fixtures", action="store_true",
                        help="evaluate against fixture golden set")
    args = parser.parse_args()

    golden_path = (HERE / "fixtures" / "golden.fixture.yaml") if args.fixtures else args.golden
    if not golden_path.exists():
        golden_path = args.golden

    questions = load_golden(golden_path)
    rows = []
    headers = {"Host": args.host} if args.host else {}
    with httpx.Client(timeout=120.0, headers=headers) as client:
        for q in questions:
            started = time.perf_counter()
            try:
                response = ask(client, args.api, q["question"])
            except Exception as exc:
                response = {
                    "answer": f"Connection error: {exc}",
                    "citations": [],
                    "answered_from_context": False,
                    "retrieved": [],
                    "confidence": "low"
                }
            rows.append({
                "id": q["id"], "type": q.get("type", "ordinary"),
                "must_abstain": bool(q.get("must_abstain", False)),
                "relevant": q.get("relevant") or [],
                "expect": q.get("expect"),
                "response": response,
                "seconds": round(time.perf_counter() - started, 2),
            })
            print(f"  {q['id']:<5} {rows[-1]['seconds']:>6.2f}s  {q['question'][:60]}")

    results = summarise(rows)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    failures = gate(results, baseline)

    results["golden"] = str(golden_path)
    results["questions"] = len(rows)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {args.out}")
    if args.update_baseline or not args.baseline.exists():
        args.baseline.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"baseline updated: {args.baseline}")

    if failures:
        print("\nGATE FAILED")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\ngate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
