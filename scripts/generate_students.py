"""Deterministic generator for the synthetic student data kit (Annex C schema).

Identities (names, programme, batch, CGPA) come from data/seed/students_seed.csv, which was drafted by an
LLM (see data/prompts/). This script then builds attendance and results with a fixed random seed and
*injects the edge cases on purpose* (an LLM cannot be relied on to hit exact thresholds), and recomputes
`active_backlogs` from the results so the tables are mutually consistent.

    python scripts/generate_students.py --out data/students          # CSVs in the Annex C schema
    python scripts/generate_students.py --out data/students --load   # ...and load them into SQLite

Edge cases (student ids are listed in docs/data_card.md and printed by this script).
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED = 20261006
SESSION = "2026-MAY"

COURSES = [
    ("CS201", "Data Structures", "B.Tech CSE", 3, 4), ("CS202", "Database Management Systems", "B.Tech CSE", 3, 4),
    ("CS203", "Operating Systems", "B.Tech CSE", 3, 4), ("MA201", "Mathematics", "B.Tech CSE", 3, 4),
    ("EC201", "Digital Electronics", "B.Tech ECE", 3, 4), ("EC202", "Signals and Systems", "B.Tech ECE", 3, 4),
    ("EC203", "Microprocessors", "B.Tech ECE", 3, 4), ("MA251", "Mathematics", "B.Tech ECE", 3, 4),
]

# (student, course) -> (classes_held, classes_attended)   [attendance edge cases]
ATT = {
    ("S1001", "CS201"): (40, 30),   # exactly 75%  (old rule threshold; below the 80% circular)
    ("S1010", "CS201"): (40, 32),   # exactly 80%  (circular threshold)
    ("S1003", "CS203"): (40, 31),   # one class below 80% (77.5%)
    ("S1002", "CS201"): (40, 29),   # one class below 75% (72.5%)
    ("S1005", "CS201"): (40, 24),   # 60% -> detained
    ("S1007", "CS202"): (40, 31),   # 77.5%
    ("S1017", "EC202"): (40, 30),   # exactly 75%
    ("S1018", "EC201"): (40, 32),   # exactly 80%
    ("S1021", "EC203"): (40, 29),   # one class below 75%
}
# (student, course) -> (internal, external, result, exam_type, session)   [result edge cases]
RES = {
    ("S1003", "CS201"): (18, 21, "FAIL", "REGULAR", SESSION),       # 39/100: one mark below pass
    ("S1004", "CS202"): (19, 20, "FAIL", "REGULAR", SESSION),       # 39/100
    ("S1006", "CS201"): (19, 20, "FAIL", "REGULAR", SESSION),       # multiple backlogs
    ("S1006", "CS202"): (18, 21, "FAIL", "REGULAR", SESSION),
    ("S1007", "MA201"): (12, 26, "FAIL", "REGULAR", SESSION),       # Mathematics fail -> supplementary eligible
    ("S1005", "CS201"): (28, 0, "DETAINED", "REGULAR", SESSION),    # detained
    ("S1002", "CS201"): (30, 0, "DETAINED", "REGULAR", SESSION),
    ("S1005", "CS203"): (0, 0, "ABSENT", "REGULAR", SESSION),       # absent result
    ("S1018", "EC202"): (14, 24, "FAIL", "REGULAR", SESSION),       # ECE fail 38/100
    ("S1036", "CS202"): (15, 22, "FAIL", "REGULAR", SESSION),       # failed, then cleared below
}
SUPP = {("S1036", "CS202"): (20, 24, "PASS", "SUPPLEMENTARY", "2026-JUL")}   # supplementary cleared the backlog

EXTRA_STUDENTS = [   # edge-case identities added on top of the 32 seeded ones
    ("S1033", "Vihaan Chawla", "B.Tech CSE", 2023, 7, 6.50),   # CGPA exactly at the placement cut-off (6.5)
    ("S1034", "Reyansh Dutta", "B.Tech CSE", 2023, 7, 6.49),   # one hundredth below the cut-off
    ("S1035", "Atharv Bhalla", "B.Tech CSE", 2023, 7, 8.00),   # strong CGPA but a backlog (see RES below)
    ("S1036", "Zoya Qureshi", "B.Tech CSE", 2023, 7, 7.20),    # cleared a backlog via supplementary
]
RES[("S1035", "CS203")] = (17, 21, "FAIL", "REGULAR", SESSION)


def _attempt_key(r: dict) -> tuple:
    from tools.records import session_key

    return (*session_key(r["exam_session"]), r["exam_type"] == "SUPPLEMENTARY")


def build(seed_csv: Path) -> dict[str, list[dict]]:
    rnd = random.Random(SEED)
    students = [{"student_id": r["student_id"], "full_name": r["full_name"], "programme": r["programme"],
                 "batch_year": int(r["batch_year"]), "current_semester": int(r["current_semester"]),
                 "cgpa": float(r["cgpa"])} for r in csv.DictReader(open(seed_csv, encoding="utf-8"))]
    for sid, name, prog, batch, sem, cgpa in EXTRA_STUDENTS:
        students.append({"student_id": sid, "full_name": name, "programme": prog, "batch_year": batch,
                         "current_semester": sem, "cgpa": cgpa})
    by_prog: dict[str, list[tuple]] = {}
    for c in COURSES:
        by_prog.setdefault(c[2], []).append(c)

    attendance, results = [], []
    for s in students:
        for code, *_ in by_prog[s["programme"]]:
            held, att = ATT.get((s["student_id"], code), (None, None))
            if held is None:
                held = rnd.choice([36, 40, 40, 42, 45])
                att = min(held, round(held * rnd.uniform(0.84, 0.98)))   # safely above every threshold in the corpus
            attendance.append({"student_id": s["student_id"], "course_code": code, "classes_held": held,
                               "classes_attended": att})
            internal, external, result, etype, session = RES.get((s["student_id"], code), (None,) * 5)
            if internal is None:
                internal, external = rnd.randint(22, 36), rnd.randint(32, 56)
                result, etype, session = "PASS", "REGULAR", SESSION
            results.append({"student_id": s["student_id"], "course_code": code, "exam_session": session,
                            "exam_type": etype, "internal_marks": internal, "external_marks": external,
                            "total_marks": internal + external, "max_marks": 100, "result": result})
            if (s["student_id"], code) in SUPP:
                i2, e2, r2, t2, ses2 = SUPP[(s["student_id"], code)]
                results.append({"student_id": s["student_id"], "course_code": code, "exam_session": ses2,
                                "exam_type": t2, "internal_marks": i2, "external_marks": e2,
                                "total_marks": i2 + e2, "max_marks": 100, "result": r2})
    # active_backlogs = courses whose latest attempt is not a PASS (kept consistent with the results table)
    latest: dict[tuple, dict] = {}
    for r in results:
        k = (r["student_id"], r["course_code"])
        if k not in latest or _attempt_key(r) >= _attempt_key(latest[k]):
            latest[k] = r
    for s in students:
        s["active_backlogs"] = sum(1 for (sid, _), r in latest.items() if sid == s["student_id"] and r["result"] != "PASS")
    return {"students": students,
            "courses": [dict(zip(["course_code", "course_name", "programme", "semester", "credits"], c)) for c in COURSES],
            "attendance": attendance, "results": results}


def write_csvs(tables: dict[str, list[dict]], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-csv", default=str(ROOT / "data" / "seed" / "students_seed.csv"))
    ap.add_argument("--out", default=str(ROOT / "data" / "students"))
    ap.add_argument("--load", action="store_true", help="also load the CSVs into SQLite")
    a = ap.parse_args()
    tables = build(Path(a.seed_csv))
    write_csvs(tables, Path(a.out))
    print({k: len(v) for k, v in tables.items()})
    if a.load:
        from app import db
        from app.loader import load_dir
        db.ensure_schema()
        print(load_dir(Path(a.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
