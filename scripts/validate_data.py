"""Validate the SQLite database against the Annex C schema and logical constraints; print violations.

    python scripts/validate_data.py            # exit code 1 if any error
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db  # noqa: E402
from app.validation import validate_db  # noqa: E402

if __name__ == "__main__":
    conn = db.connect()
    try:
        report = validate_db(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("students", "courses", "attendance", "results", "rule_registry", "source_register")}
        stats = {
            "programmes": [r[0] for r in conn.execute("SELECT DISTINCT programme FROM students")],
            "batches": [r[0] for r in conn.execute("SELECT DISTINCT batch_year FROM students")],
        }
    finally:
        conn.close()
    print(json.dumps({"row_counts": counts, **stats, "errors": report["errors"], "warnings": report["warnings"]}, indent=2))
    print("VALIDATION PASSED" if not report["errors"] else f"VALIDATION FAILED: {len(report['errors'])} error(s)")
    sys.exit(1 if report["errors"] else 0)
