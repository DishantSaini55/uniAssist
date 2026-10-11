"""Load students (and their courses/attendance/results) from CSVs in the Annex C schema.

    python scripts/load_students.py --dir test_students/

Files (all optional, any subset): students.csv, courses.csv, attendance.csv, results.csv.
Rows are upserted. Invalid rows are skipped and reported; `strict=True` aborts the whole load instead.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from app import db
from app.validation import REQUIRED_COLUMNS, check_attendance, check_course, check_result, check_student

SPEC = {
    "students": (["student_id"], check_student),
    "courses": (["course_code"], check_course),
    "attendance": (["student_id", "course_code"], check_attendance),
    "results": (["student_id", "course_code", "exam_session", "exam_type"], check_result),
}
ORDER = ["students", "courses", "attendance", "results"]


def read_csv_bytes(data: bytes) -> list[dict[str, str]]:
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig")))]


def load_tables(tables: dict[str, list[dict[str, str]]], strict: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {"loaded": {}, "skipped": [], "errors": []}
    conn = db.connect()
    try:
        pass_row = conn.execute("SELECT value FROM rule_registry WHERE parameter='pass_marks_pct' "
                                "ORDER BY effective_from DESC LIMIT 1").fetchone()
        pass_pct = float(pass_row["value"]) if pass_row else 40.0
        for table in ORDER:
            rows = tables.get(table)
            if rows is None:
                continue
            cols = REQUIRED_COLUMNS[table]
            if rows and (missing := [c for c in cols if c not in rows[0]]):
                report["errors"].append(f"{table}.csv is missing columns {missing}")
                continue
            keys, check = SPEC[table]
            n = 0
            for i, row in enumerate(rows, start=2):
                problems = check(row, pass_pct) if table == "results" else check(row)
                if table in ("attendance", "results"):
                    if not conn.execute("SELECT 1 FROM students WHERE student_id=?", (row["student_id"],)).fetchone():
                        problems.append(f"unknown student {row['student_id']}")
                    if not conn.execute("SELECT 1 FROM courses WHERE course_code=?", (row["course_code"],)).fetchone():
                        problems.append(f"unknown course {row['course_code']}")
                if problems:
                    report["skipped"].append({"file": f"{table}.csv", "line": i,
                                              "key": "/".join(row.get(k, "") for k in keys), "problems": problems})
                    continue
                vals = [row[c] for c in cols]
                conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                             f"ON CONFLICT({','.join(keys)}) DO UPDATE SET " +
                             ",".join(f"{c}=excluded.{c}" for c in cols if c not in keys), vals)
                n += 1
            report["loaded"][table] = n
        if (report["skipped"] or report["errors"]) and strict:
            conn.rollback()
            report["loaded"] = {}
            report["aborted"] = True
        else:
            conn.commit()
    finally:
        conn.close()
    return report


def load_dir(directory: Path, strict: bool = False) -> dict[str, Any]:
    tables = {t: read_csv_bytes((directory / f"{t}.csv").read_bytes())
              for t in ORDER if (directory / f"{t}.csv").exists()}
    if not tables:
        return {"loaded": {}, "skipped": [], "errors": [f"no students/courses/attendance/results CSV in {directory}"]}
    return load_tables(tables, strict)
