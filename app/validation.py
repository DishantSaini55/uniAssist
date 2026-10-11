"""Schema and logical-constraint validation for Annex C data (used by the loader and validate_data.py)."""
from __future__ import annotations

import re
import sqlite3
from typing import Any

REQUIRED_COLUMNS = {
    "students": ["student_id", "full_name", "programme", "batch_year", "current_semester", "cgpa", "active_backlogs"],
    "courses": ["course_code", "course_name", "programme", "semester", "credits"],
    "attendance": ["student_id", "course_code", "classes_held", "classes_attended"],
    "results": ["student_id", "course_code", "exam_session", "exam_type", "internal_marks", "external_marks",
                "total_marks", "max_marks", "result"],
    "rule_registry": ["rule_id", "description", "parameter", "operator", "value", "scope_programmes", "scope_batches",
                      "effective_from", "effective_to", "source_doc_id", "source_section"],
    "source_register": ["doc_id", "title", "issuer", "authority_level", "doc_type", "version", "effective_from",
                        "effective_to", "supersedes", "scope_programmes", "scope_batches", "provenance",
                        "retrieved_on", "synthetic"],
}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def check_student(r: dict[str, Any]) -> list[str]:
    v = []
    if not re.fullmatch(r"S\d{4}", str(r.get("student_id", ""))):
        v.append("student_id must be S followed by 4 digits")
    if not str(r.get("full_name", "")).strip():
        v.append("full_name is empty")
    if not str(r.get("programme", "")).strip():
        v.append("programme is empty")
    if _int(r.get("batch_year")) is None or not 1990 <= _int(r["batch_year"]) <= 2100:
        v.append("batch_year must be a year")
    sem = _int(r.get("current_semester"))
    if sem is None or not 1 <= sem <= 10:
        v.append("current_semester must be 1-10")
    cg = _float(r.get("cgpa"))
    if cg is None or not 0 <= cg <= 10:
        v.append("cgpa must be 0.00-10.00")
    bl = _int(r.get("active_backlogs"))
    if bl is None or bl < 0:
        v.append("active_backlogs must be >= 0")
    return v


def check_course(r: dict[str, Any]) -> list[str]:
    v = []
    if not str(r.get("course_code", "")).strip():
        v.append("course_code is empty")
    if _int(r.get("semester")) is None:
        v.append("semester must be an integer")
    if _int(r.get("credits")) is None or _int(r["credits"]) <= 0:
        v.append("credits must be a positive integer")
    return v


def check_attendance(r: dict[str, Any]) -> list[str]:
    v = []
    held, att = _int(r.get("classes_held")), _int(r.get("classes_attended"))
    if held is None or held <= 0:
        v.append("classes_held must be > 0")
    if att is None or att < 0:
        v.append("classes_attended must be >= 0")
    if held is not None and att is not None and att > held:
        v.append(f"classes_attended ({att}) exceeds classes_held ({held})")
    return v


def check_result(r: dict[str, Any], pass_pct: float = 40.0) -> list[str]:
    v = []
    i, e, t, m = (_int(r.get(k)) for k in ("internal_marks", "external_marks", "total_marks", "max_marks"))
    if None in (i, e, t, m):
        return ["marks must be integers"]
    if i < 0 or e < 0:
        v.append("marks must be >= 0")
    if m <= 0:
        v.append("max_marks must be > 0")
    if t != i + e:
        v.append(f"total_marks ({t}) != internal ({i}) + external ({e})")
    if t > m:
        v.append(f"total_marks ({t}) exceeds max_marks ({m})")
    if r.get("exam_type") not in ("REGULAR", "SUPPLEMENTARY"):
        v.append("exam_type must be REGULAR or SUPPLEMENTARY")
    res = r.get("result")
    if res not in ("PASS", "FAIL", "ABSENT", "DETAINED"):
        v.append("result must be PASS, FAIL, ABSENT or DETAINED")
    elif m > 0:
        passing = t * 100 >= pass_pct * m
        if res == "PASS" and not passing:
            v.append(f"result PASS but total {t}/{m} is below the {pass_pct:g}% pass mark")
        if res == "FAIL" and passing:
            v.append(f"result FAIL but total {t}/{m} meets the {pass_pct:g}% pass mark")
        if res == "ABSENT" and t != 0:
            v.append("result ABSENT must have 0 marks")
        if res == "DETAINED" and e != 0:
            v.append("result DETAINED must have 0 external marks")
    return v


def validate_db(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Whole-database validation. Returns {'errors': [...], 'warnings': [...]}."""
    errors: list[str] = []
    warnings: list[str] = []
    q = lambda sql: [dict(r) for r in conn.execute(sql)]  # noqa: E731
    have = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, cols in REQUIRED_COLUMNS.items():
        if table not in have:
            errors.append(f"missing table {table}")
            continue
        got = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        miss = [c for c in cols if c not in got]
        if miss:
            errors.append(f"table {table} is missing required columns {miss}")
    if errors:
        return {"errors": errors, "warnings": warnings}

    pass_row = conn.execute("SELECT value FROM rule_registry WHERE parameter='pass_marks_pct' "
                            "ORDER BY effective_from DESC LIMIT 1").fetchone()
    pass_pct = float(pass_row["value"]) if pass_row else 40.0

    students = q("SELECT * FROM students")
    courses = {c["course_code"]: c for c in q("SELECT * FROM courses")}
    sprog = {s["student_id"]: s for s in students}
    for s in students:
        errors += [f"students {s['student_id']}: {m}" for m in check_student(s)]
    for c in courses.values():
        errors += [f"courses {c['course_code']}: {m}" for m in check_course(c)]
        if c["programme"] not in {s["programme"] for s in students}:
            errors.append(f"courses {c['course_code']}: programme '{c['programme']}' matches no student programme")
    for a in q("SELECT * FROM attendance"):
        key = f"attendance {a['student_id']}/{a['course_code']}"
        errors += [f"{key}: {m}" for m in check_attendance(a)]
        if a["student_id"] not in sprog:
            errors.append(f"{key}: unknown student")
        if a["course_code"] not in courses:
            errors.append(f"{key}: unknown course")
    for r in q("SELECT * FROM results"):
        key = f"results {r['student_id']}/{r['course_code']}/{r['exam_session']}/{r['exam_type']}"
        errors += [f"{key}: {m}" for m in check_result(r, pass_pct)]
        if r["student_id"] not in sprog:
            errors.append(f"{key}: unknown student")
        if r["course_code"] not in courses:
            errors.append(f"{key}: unknown course")
    # Logical consistency (warnings: judges' data may legitimately differ from our convention).
    from tools.records import latest_result_per_course  # local import: needs the configured DB path

    for s in students:
        try:
            latest = latest_result_per_course(s["student_id"])
        except Exception:  # pragma: no cover - only when DB path differs from conn
            break
        outstanding = sum(1 for r in latest.values() if r["result"] != "PASS")
        if outstanding != s["active_backlogs"]:
            warnings.append(f"students {s['student_id']}: active_backlogs={s['active_backlogs']} but "
                            f"{outstanding} course(s) have a non-pass latest result")
    for r in q("SELECT * FROM rule_registry"):
        if not DATE.match(str(r["effective_from"])):
            errors.append(f"rule_registry {r['rule_id']}: effective_from must be YYYY-MM-DD")
        if not conn.execute("SELECT 1 FROM source_register WHERE doc_id=?", (r["source_doc_id"],)).fetchone():
            errors.append(f"rule_registry {r['rule_id']}: source_doc_id '{r['source_doc_id']}' is not in the Source Register")
        if not str(r["source_section"]).strip():
            errors.append(f"rule_registry {r['rule_id']}: source_section is empty (every rule must cite a clause)")
    for d in q("SELECT * FROM source_register"):
        if not 1 <= int(d["authority_level"]) <= 5:
            errors.append(f"source_register {d['doc_id']}: authority_level must be 1-5")
        if not DATE.match(str(d["effective_from"])):
            errors.append(f"source_register {d['doc_id']}: effective_from must be YYYY-MM-DD")
    return {"errors": errors, "warnings": warnings}
