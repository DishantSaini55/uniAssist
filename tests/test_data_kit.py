"""Synthetic data kit: schema, edge cases, validation and loader."""
import csv
import sqlite3
from pathlib import Path

import pytest

from app import validation as V
from scripts.generate_students import build

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tables():
    return build(ROOT / "data" / "seed" / "students_seed.csv")


def test_minimum_requirements(tables):
    st = tables["students"]
    assert len(st) >= 30 and len({s["programme"] for s in st}) >= 2 and len({s["batch_year"] for s in st}) >= 2
    assert len(tables["courses"]) >= 6
    assert not any(9000 <= int(s["student_id"][1:]) <= 9999 for s in st)          # reserved for judges
    assert not any(c["course_code"].startswith("JDG") for c in tables["courses"])  # reserved for judges


def test_generator_is_deterministic(tables):
    assert tables == build(ROOT / "data" / "seed" / "students_seed.csv")


def test_committed_csvs_match_generator(tables):
    rows = list(csv.DictReader(open(ROOT / "data" / "students" / "attendance.csv", encoding="utf-8")))
    assert len(rows) == len(tables["attendance"])


def test_edge_cases_present(tables):
    att = {(a["student_id"], a["course_code"]): a for a in tables["attendance"]}
    pct = lambda s, c: 100 * att[(s, c)]["classes_attended"] / att[(s, c)]["classes_held"]  # noqa: E731
    assert pct("S1001", "CS201") == 75.0 and pct("S1010", "CS201") == 80.0           # exactly at thresholds
    assert pct("S1002", "CS201") == 72.5 and pct("S1003", "CS203") == 77.5           # one class below
    res = {(r["student_id"], r["course_code"], r["exam_type"]): r for r in tables["results"]}
    assert res[("S1003", "CS201", "REGULAR")]["total_marks"] == 39                   # just below the 40 pass mark
    assert res[("S1005", "CS203", "REGULAR")]["result"] == "ABSENT"
    assert res[("S1005", "CS201", "REGULAR")]["result"] == "DETAINED"
    st = {s["student_id"]: s for s in tables["students"]}
    assert st["S1006"]["active_backlogs"] == 2                                       # multiple backlogs
    assert st["S1033"]["cgpa"] == 6.5                                                # exactly at the placement cut-off


def test_row_validators():
    assert V.check_attendance({"classes_held": 40, "classes_attended": 41})
    assert V.check_result({"internal_marks": 20, "external_marks": 20, "total_marks": 41, "max_marks": 100,
                           "exam_type": "REGULAR", "result": "PASS"})            # total != internal + external
    assert V.check_result({"internal_marks": 20, "external_marks": 19, "total_marks": 39, "max_marks": 100,
                           "exam_type": "REGULAR", "result": "PASS"})            # PASS below pass mark
    assert V.check_result({"internal_marks": 30, "external_marks": 30, "total_marks": 60, "max_marks": 100,
                           "exam_type": "REGULAR", "result": "FAIL"})            # FAIL above pass mark
    assert V.check_result({"internal_marks": 0, "external_marks": 0, "total_marks": 0, "max_marks": 100,
                           "exam_type": "REGULAR", "result": "ABSENT"}) == []
    assert V.check_student({"student_id": "X1", "full_name": "a", "programme": "p", "batch_year": 2023,
                            "current_semester": 11, "cgpa": 10.5, "active_backlogs": -1})


def test_validate_db_passes_on_shipped_data(env):
    from app import db

    conn = db.connect()
    try:
        report = V.validate_db(conn)
    finally:
        conn.close()
    assert report["errors"] == [], report["errors"][:5]


def test_validate_db_detects_violations(tmp_path, env):
    from app import db

    p = tmp_path / "bad.db"
    db.ensure_schema(p)
    c = sqlite3.connect(p)
    c.executescript("PRAGMA foreign_keys=OFF;"
                    "INSERT INTO students VALUES ('S1001','A','B.Tech CSE',2023,3,7.0,0);"
                    "INSERT INTO courses VALUES ('CS201','DS','B.Tech CSE',3,4);")
    c.commit()
    c.close()
    conn = db.connect(p)
    conn.execute("INSERT INTO rule_registry (rule_id,description,parameter,operator,value,effective_from,source_doc_id,source_section)"
                 " VALUES ('R1','d','p','>=','1','2026-01-01','MISSING-DOC','')")
    conn.commit()
    report = V.validate_db(conn)
    conn.close()
    assert any("not in the Source Register" in e for e in report["errors"])
    assert any("source_section is empty" in e for e in report["errors"])
