"""Evaluation harness (R12 / section 7).

Runs evaluation/questions.json through the same code path as POST /ask and reports:
  answer correctness   answer_type matches AND every expected substring is present AND no forbidden one is
  citation accuracy    the cited (doc, section) is the expected one AND the stored text of that section contains the evidence
  abstention accuracy  not_found exactly when expected (answerable questions are not abstained on)
  tool-result correct  tool name and expected output fields match
  retrieval hit rate   expected document appears in the raw top-k chunks (before precedence)
  latency / cost       p50 / p95 latency, LLM calls and tokens per question
Method: deterministic exact/substring match (numbers and dates are matched literally). No LLM judge is used,
so the numbers are reproducible. Several configurations are compared and the default is chosen on the numbers.

    python evaluation/run_eval.py                      # compare all configurations
    python evaluation/run_eval.py --configs A          # one configuration
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

CONFIGS = {
    "A": {"label": "bge-small, section chunks, k=5", "embedding_model": "BAAI/bge-small-en-v1.5", "chunking": "section", "top_k": 5},
    "B": {"label": "all-MiniLM-L6-v2, section chunks, k=5", "embedding_model": "sentence-transformers/all-MiniLM-L6-v2", "chunking": "section", "top_k": 5},
    "C": {"label": "bge-small, fixed 400-char chunks, k=5", "embedding_model": "BAAI/bge-small-en-v1.5", "chunking": "fixed", "chunk_size": 400, "chunk_overlap": 80, "top_k": 5},
    "D": {"label": "bge-small, section chunks, k=2", "embedding_model": "BAAI/bge-small-en-v1.5", "chunking": "section", "top_k": 2},
}


def dig(obj, path: str):
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj.get(part) if isinstance(obj, dict) else None
        if obj is None:
            return None
    return obj


def pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def run_config(key: str, questions: list[dict]) -> dict:
    from app import bootstrap as boot
    from app import config, service

    spec = dict(CONFIGS[key])
    label = spec.pop("label")
    tmp = Path(tempfile.mkdtemp(prefix=f"uniassist_eval_{key}_"))
    config.configure(runtime_dir=tmp, sqlite_path=tmp / "university.db", embedding_backend="st", llm_mode="mock", **spec)
    service.reset_service()
    t0 = time.perf_counter()
    boot.bootstrap(config.settings)
    ingest_s = round(time.perf_counter() - t0, 1)
    svc = service.get_service()
    store = svc.retriever.store

    def section_text(doc_id: str, section: str) -> str:
        got = store.collection.get(where={"$and": [{"doc_id": doc_id}, {"section": section}]}, include=["documents"])
        return " ".join(" ".join(got["documents"]).split()).lower()

    rows = []
    for q in questions:
        t = time.perf_counter()
        r = svc.ask(q["question"], q.get("student_id"), q.get("as_of_date"))
        ms = (time.perf_counter() - t) * 1000
        audit = __import__("app.db", fromlist=["x"]).load_audit(r["trace_id"])
        ans = r["answer"].lower()
        checks = {}
        checks["type"] = r["answer_type"] == q["expected_answer_type"]
        checks["contains"] = all(s.lower() in ans for s in q.get("expected_contains", []))
        checks["forbidden"] = not any(s.lower() in ans for s in q.get("expected_not_contains", []))
        correct = all(checks.values())

        cite_ok = None
        if q.get("expected_source"):
            src = q["expected_source"]
            hit = [c for c in r["citations"] if c["doc_id"] == src["doc_id"] and c["section"] == src["section"]]
            cite_ok = bool(hit) and src["evidence"].lower() in section_text(src["doc_id"], src["section"]) and \
                all(c["page"] for c in hit)
        abst_expected = q["expected_answer_type"] == "not_found"
        abst_ok = (r["answer_type"] == "not_found") == abst_expected

        tool_ok = None
        if q.get("expected_tools"):
            tool_ok = True
            for et in q["expected_tools"]:
                calls = [c for c in r["tools_invoked"] if c["tool"] == et["tool"]]
                if not calls or not all(dig(calls[0]["output"], k) == v for k, v in et["output"].items()):
                    tool_ok = False
        if q.get("expected_no_tools"):
            tool_ok = (tool_ok is not False) and not r["tools_invoked"]
        if q.get("expects_assumptions") and not r["assumptions"]:
            correct = False

        retr = None
        if q.get("must_retrieve_doc"):
            retr = any(s["doc_id"] == q["must_retrieve_doc"] for s in audit["sources_retrieved"])
        rows.append({"id": q["id"], "held_out": int(q.get("held_out") or 0), "category": q["category"], "question": q["question"], "student_id": q.get("student_id"),
                     "expected_type": q["expected_answer_type"], "actual_type": r["answer_type"], "correct": correct,
                     "citation_ok": cite_ok, "abstention_ok": abst_ok, "tool_ok": tool_ok, "retrieval_hit": retr,
                     "latency_ms": round(ms, 1), "llm_calls": audit["llm_calls"], "tokens": audit["tokens"],
                     "checks": checks, "answer": r["answer"][:300], "cited": [(c["doc_id"], c["section"]) for c in r["citations"]]})

    def rate(field: str):
        vals = [x[field] for x in rows if x[field] is not None]
        return pct(sum(bool(v) for v in vals), len(vals)), len(vals)

    lat = sorted(x["latency_ms"] for x in rows)
    p = lambda f: round(lat[min(len(lat) - 1, int(f * len(lat)))], 1)  # noqa: E731
    by_cat: dict[str, list[bool]] = {}
    for x in rows:
        by_cat.setdefault(x["category"], []).append(x["correct"])
    summary = {
        "config": key, "label": label, "questions": len(rows), "ingest_seconds": ingest_s,
        "answer_correctness": rate("correct"), "citation_accuracy": rate("citation_ok"),
        "abstention_accuracy": rate("abstention_ok"), "tool_result_correctness": rate("tool_ok"),
        "retrieval_hit_rate": rate("retrieval_hit"),
        "latency_ms_p50": p(0.5), "latency_ms_p95": p(0.95), "latency_ms_mean": round(statistics.mean(lat), 1),
        "llm_calls_per_question": round(sum(x["llm_calls"] for x in rows) / len(rows), 2),
        "tokens_per_question": round(sum(x["tokens"] for x in rows) / len(rows), 1),
        "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_cat.items())},
        "tuned_set_correctness": pct(sum(x["correct"] for x in rows if x["held_out"] == 0), sum(1 for x in rows if x["held_out"] == 0)),
        "paraphrase_round1_correctness": pct(sum(x["correct"] for x in rows if x["held_out"] == 1), sum(1 for x in rows if x["held_out"] == 1)),
        "paraphrase_round2_correctness": pct(sum(x["correct"] for x in rows if x["held_out"] == 2), sum(1 for x in rows if x["held_out"] == 2)),
    }
    shutil.rmtree(tmp, ignore_errors=True)
    return {"summary": summary, "rows": rows}


def markdown(results: list[dict], default: str) -> str:
    def cell(v):
        return "n/a" if v[0] is None else f"{v[0]}% ({v[1]})"

    out = ["# Evaluation report", "",
           f"Questions: {results[0]['summary']['questions']} (`evaluation/questions.json`). "
           "Method: exact/substring match against expected answers, section-level citation check against the stored "
           "chunk text, literal tool-output comparison. LLM mode: `mock` (deterministic templates), so the numbers "
           "isolate retrieval, precedence, tools and routing. Corpus: synthetic fixtures (`corpus/fixtures`).", "",
           "## Configuration comparison", "",
           "| Config | Description | Answer correctness | Citation accuracy | Abstention accuracy | Tool-result correctness | Retrieval hit rate | p50 / p95 latency (ms) |",
           "|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = r["summary"]
        out.append(f"| {s['config']}{' (default)' if s['config'] == default else ''} | {s['label']} | {cell(s['answer_correctness'])} | "
                   f"{cell(s['citation_accuracy'])} | {cell(s['abstention_accuracy'])} | {cell(s['tool_result_correctness'])} | "
                   f"{cell(s['retrieval_hit_rate'])} | {s['latency_ms_p50']} / {s['latency_ms_p95']} |")
    out += ["", "Answer correctness by question set (see evaluation/README.md for how these sets were built):", "",
            "| Config | Original 39 (written alongside the system) | Paraphrase round 1 (10) | Paraphrase round 2 (10) |", "|---|---|---|---|"]
    for r in results:
        s = r["summary"]
        out.append(f"| {s['config']} | {s['tuned_set_correctness']}% | {s['paraphrase_round1_correctness']}% | "
                   f"{s['paraphrase_round2_correctness']}% |")
    out += ["", "LLM calls and tokens per question: 0 / 0 in mock mode (see README for the Ollama cost profile).", ""]
    for r in results:
        s = r["summary"]
        out += [f"## Config {s['config']}: {s['label']}", "", f"Ingest time {s['ingest_seconds']} s. Correct by category: " +
                ", ".join(f"{c} {v}" for c, v in s["by_category"].items()), ""]
        fails = [x for x in r["rows"] if not x["correct"] or x["citation_ok"] is False or x["tool_ok"] is False]
        if fails:
            out += ["| id | question | expected | actual | note |", "|---|---|---|---|---|"]
            for x in fails:
                note = "; ".join(k for k, v in x["checks"].items() if not v) or ("citation" if x["citation_ok"] is False else "tool")
                out.append(f"| {x['id']} | {x['question'][:70]} | {x['expected_type']} | {x['actual_type']} | {note} |")
            out.append("")
        else:
            out += ["No failures.", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--default", default="A")
    ap.add_argument("--out", default=str(ROOT / "evaluation" / "report.md"))
    ap.add_argument("--json", default=str(ROOT / "evaluation" / "results.json"))
    a = ap.parse_args()
    questions = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    results = [run_config(k.strip(), questions) for k in a.configs.split(",")]
    Path(a.json).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    Path(a.out).write_text(markdown(results, a.default), encoding="utf-8")
    for r in results:
        print(json.dumps(r["summary"]))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
