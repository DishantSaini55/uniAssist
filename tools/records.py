"""Deterministic student-record tools. Student identity is always supplied by the caller (request context)."""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Any

from app import db

NON_PASS = ("FAIL", "ABSENT", "DETAINED")


def pct(attended: int, held: int) -> float:
    return round(float(Fraction(100 * attended, held)), 2) if held else 0.0


def get_student_profile(student_id: str) -> dict[str, Any]:
    r = db.one("SELECT student_id, full_name, programme, batch_year, current_semester, cgpa, active_backlogs "
               "FROM students WHERE student_id=?", (student_id,))
    return {"found": True, **r} if r else {"found": False, "student_id": student_id}


def get_course(course_code: str) -> dict[str, Any]:
    r = db.one("SELECT * FROM courses WHERE course_code=?", (course_code,))
    return {"found": True, **r} if r else {"found": False, "course_code": course_code}


def get_attendance(student_id: str, course_code: str | None = None) -> dict[str, Any]:
    sql = ("SELECT a.course_code, c.course_name, a.classes_held, a.classes_attended FROM attendance a "
           "LEFT JOIN courses c ON c.course_code=a.course_code WHERE a.student_id=?")
    params: list[Any] = [student_id]
    if course_code:
        sql += " AND a.course_code=?"
        params.append(course_code)
    recs = db.rows(sql + " ORDER BY a.course_code", params)
    for r in recs:  # computed, never stored (Annex C)
        r["attendance_pct"] = pct(r["classes_attended"], r["classes_held"])
    return {"student_id": student_id, "count": len(recs), "records": recs}


def get_results(student_id: str, course_code: str | None = None) -> dict[str, Any]:
    sql = ("SELECT r.course_code, c.course_name, r.exam_session, r.exam_type, r.internal_marks, r.external_marks, "
           "r.total_marks, r.max_marks, r.result FROM results r LEFT JOIN courses c ON c.course_code=r.course_code "
           "WHERE r.student_id=?")
    params: list[Any] = [student_id]
    if course_code:
        sql += " AND r.course_code=?"
        params.append(course_code)
    recs = db.rows(sql + " ORDER BY r.course_code, r.exam_session, r.exam_type", params)
    return {"student_id": student_id, "count": len(recs), "results": recs}


_MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)}


def session_key(session: str) -> tuple[int, int]:
    """'2026-MAY' -> (2026, 5): sessions must be ordered chronologically, not alphabetically."""
    year, _, mon = str(session).partition("-")
    return (int(year) if year.isdigit() else 0, _MONTHS.get(mon.strip().upper()[:3], int(mon) if mon.isdigit() else 0))


def _attempt_key(r: dict[str, Any]) -> tuple[int, int, int]:
    return (*session_key(r["exam_session"]), 1 if r["exam_type"] == "SUPPLEMENTARY" else 0)


def latest_result_per_course(student_id: str) -> dict[str, dict[str, Any]]:
    """Latest attempt wins: a passed SUPPLEMENTARY clears an earlier REGULAR fail."""
    latest: dict[str, dict[str, Any]] = {}
    for r in get_results(student_id)["results"]:
        cur = latest.get(r["course_code"])
        if cur is None or _attempt_key(r) >= _attempt_key(cur):
            latest[r["course_code"]] = r
    return latest


def get_backlogs(student_id: str) -> dict[str, Any]:
    s = db.one("SELECT active_backlogs FROM students WHERE student_id=?", (student_id,))
    outstanding = [{"course_code": c, "course_name": r["course_name"], "last_result": r["result"],
                    "exam_session": r["exam_session"], "total_marks": r["total_marks"], "max_marks": r["max_marks"]}
                   for c, r in latest_result_per_course(student_id).items() if r["result"] in NON_PASS]
    return {"student_id": student_id, "found": s is not None,
            "active_backlogs": s["active_backlogs"] if s else None, "outstanding_courses": outstanding}


def resolve_course(text: str, programme: str | None) -> dict[str, Any]:
    """Match a course by code, name or initials. Returns all matches so the caller can ask for clarification."""
    q = text.lower()
    courses = db.rows("SELECT * FROM courses" + (" WHERE programme=?" if programme else ""),
                      [programme] if programme else [])
    hits: dict[str, dict[str, Any]] = {}
    for c in courses:
        code, name = c["course_code"].lower(), c["course_name"].lower()
        initials = "".join(w[0] for w in re.findall(r"[a-z]+", name) if w not in {"and", "of", "to"})
        if re.search(rf"\b{re.escape(code)}\b", q) or name in q or \
                (len(initials) >= 2 and re.search(rf"\b{initials}\b", q)):
            hits[c["course_code"]] = c
    return {"matches": list(hits.values())}
