"""Load students from CSVs in the Annex C schema (judges' test-student loader).

    python scripts/load_students.py --dir test_students/
    python scripts/load_students.py --dir test_students/ --strict    # abort if any row is invalid

Expects any of students.csv, courses.csv, attendance.csv, results.csv in the directory. Upserts rows.
Same functionality is exposed as POST /admin/students.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db  # noqa: E402
from app.loader import load_dir  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    db.ensure_schema()
    report = load_dir(Path(a.dir), strict=a.strict)
    print(json.dumps(report, indent=2))
    sys.exit(1 if report["errors"] or report.get("aborted") else 0)
