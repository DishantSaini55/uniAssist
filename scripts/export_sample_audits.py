"""Produce the sample audit records for different answer types (docs/sample_audit/*.json) by running real
questions through the real pipeline against a throw-away runtime.

    python scripts/export_sample_audits.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES = [
    ("calculated_personal_eligibility", "S1001", "Am I eligible to appear in the exam for Data Structures?", "2026-10-06"),
    ("retrieved_fact_precedence", None, "What is the minimum attendance required to appear for end-semester exams?", "2026-10-06"),
    ("calculated_multi_step_what_if", "S1003", "I failed Data Structures. If I pass the supplementary, will I be eligible for placement?", "2026-10-06"),
    ("conflict_flagged", "S1017", "What is the re-evaluation deadline?", "2026-10-06"),
    ("not_found", None, "What is the scholarship for studying in Antarctica?", "2026-10-06"),
    ("refused_other_student", "S1001", "What is the attendance of S1002?", "2026-10-06"),
]

if __name__ == "__main__":
    from app import bootstrap, config, db, service

    tmp = Path(tempfile.mkdtemp(prefix="uniassist_audit_"))
    config.configure(runtime_dir=tmp, sqlite_path=tmp / "university.db", llm_mode="mock")
    bootstrap.bootstrap(config.settings)
    out_dir = ROOT / "docs" / "sample_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    svc = service.get_service()
    for name, sid, q, asof in SAMPLES:
        r = svc.ask(q, sid, asof)
        rec = db.load_audit(r["trace_id"])
        (out_dir / f"{name}.json").write_text(json.dumps({"request": {"X-Student-Id": sid, "question": q, "as_of_date": asof},
                                                          "response": r, "audit_record": rec}, indent=2), encoding="utf-8")
        print(name, r["answer_type"], r["trace_id"])
