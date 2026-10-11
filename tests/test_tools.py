"""Deterministic tools: thresholds come from rule_registry; boundaries are exact."""
import pytest

from tools import ToolRunner
from tools import eligibility as E
from tools import records as R
from tools.rules import resolve_rule

NOW = "2026-10-06"


@pytest.fixture(autouse=True)
def _env(env):
    return env


def test_rule_registry_is_populated_from_documents_and_traces_to_clauses():
    from app import db

    rules = db.rows("SELECT * FROM rule_registry")
    assert rules and all(r["source_doc_id"] and r["source_section"] for r in rules)
    docs = {d["doc_id"] for d in db.rows("SELECT doc_id FROM source_register")}
    assert {r["source_doc_id"] for r in rules} <= docs


def test_attendance_is_computed_not_stored():
    a = R.get_attendance("S1001", "CS201")["records"][0]
    assert (a["classes_attended"], a["classes_held"], a["attendance_pct"]) == (30, 40, 75.0)


def test_precedence_picks_circular_over_regulation_and_faq():
    rr = resolve_rule("min_attendance_pct", NOW, "B.Tech CSE", 2023)
    assert rr["status"] == "ok" and rr["rule"]["value"] == "80" and rr["rule"]["source_doc_id"] == "FIX-CIR-2026-08"
    old = resolve_rule("min_attendance_pct", "2026-05-01", "B.Tech CSE", 2023)
    assert old["rule"]["value"] == "75" and old["rule"]["source_doc_id"] == "FIX-REG-2024"
    assert [u["value"] for u in old["upcoming"]] == [">=80%", ">=85%"]
    future = resolve_rule("min_attendance_pct", "2027-03-01", "B.Tech CSE", 2023)
    assert future["rule"]["value"] == "85"


@pytest.mark.parametrize("sid,course,asof,result", [
    ("S1001", "CS201", "2026-05-01", "ELIGIBLE"),       # exactly 75% vs 75
    ("S1001", "CS201", NOW, "NOT_ELIGIBLE"),            # 75% vs 80
    ("S1010", "CS201", NOW, "ELIGIBLE"),                # exactly 80% vs 80
    ("S1003", "CS203", NOW, "NOT_ELIGIBLE"),            # 77.5% vs 80: one class below
    ("S1002", "CS201", "2026-05-01", "NOT_ELIGIBLE"),   # 72.5% vs 75: one class below
])
def test_exam_eligibility_boundaries(sid, course, asof, result):
    assert E.check_exam_eligibility(sid, course, asof)["result"] == result


def test_exam_eligibility_reports_classes_short():
    out = E.check_exam_eligibility("S1001", "CS201", NOW)
    assert out["classes_short"] == 2 and out["rule"]["rule_id"].startswith("R-FIX-CIR-2026-08")


def test_no_record_and_unknown_student():
    assert E.check_exam_eligibility("S1001", "ZZ999", NOW)["result"] == "NO_RECORD"
    assert E.check_exam_eligibility("S9999", "CS201", NOW)["result"] == "NO_RECORD"


@pytest.mark.parametrize("sid,course,result", [
    ("S1007", "MA201", "ELIGIBLE"),        # failed
    ("S1005", "CS203", "ELIGIBLE"),        # absent
    ("S1001", "MA201", "NOT_ELIGIBLE"),    # passed
    ("S1005", "CS201", "NOT_ELIGIBLE"),    # detained
    ("S1036", "CS202", "NOT_ELIGIBLE"),    # already cleared via supplementary
])
def test_supplementary_eligibility(sid, course, result):
    assert E.check_supplementary_eligibility(sid, course, NOW)["result"] == result


@pytest.mark.parametrize("sid,result", [("S1033", "ELIGIBLE"), ("S1034", "NOT_ELIGIBLE"), ("S1035", "NOT_ELIGIBLE"),
                                        ("S1009", "NO_RULE")])
def test_placement_boundaries_and_scope(sid, result):
    assert E.check_placement_eligibility(sid, NOW)["result"] == result


def test_what_if_supplementary_to_placement_states_assumptions():
    ok = E.what_if_pass_supplementary("S1003", "CS201", NOW)
    assert ok["result"] == "ELIGIBLE" and ok["hypothetical"]["active_backlogs"] == 0 and len(ok["assumptions"]) == 2
    still = E.what_if_pass_supplementary("S1006", "CS201", NOW)
    assert still["result"] == "NOT_ELIGIBLE" and still["hypothetical"]["active_backlogs"] == 1
    assert E.what_if_pass_supplementary("S1001", "CS201", NOW)["result"] == "NOT_APPLICABLE"


def test_what_if_attendance_exact_threshold():
    assert E.what_if_attendance("S1001", "CS201", 10, NOW)["result"] == "ELIGIBLE"      # 40/50 = 80% exactly
    assert E.what_if_attendance("S1001", "CS201", 5, NOW)["result"] == "NOT_ELIGIBLE"   # 35/45 = 77.8%


def test_latest_attempt_wins_for_backlogs():
    assert R.get_backlogs("S1036")["active_backlogs"] == 0 and R.get_backlogs("S1036")["outstanding_courses"] == []
    assert [c["course_code"] for c in R.get_backlogs("S1006")["outstanding_courses"]] == ["CS201", "CS202"]


def test_toolrunner_binds_identity_and_logs():
    run = ToolRunner("S1001", NOW)
    out = run.call("get_attendance", course_code="CS201")
    assert out["student_id"] == "S1001"
    assert run.log[0]["tool"] == "get_attendance" and run.log[0]["input"] == {"course_code": "CS201"}
    assert run.log[0]["status"] == "ok"
    # the bound identity always wins: a tool signature has no way to name another student
    other = run.call("get_attendance", course_code="CS201")
    assert other["student_id"] == "S1001"


def test_rule_thresholds_are_not_hard_coded(env):
    """Changing the registry changes the decision: the tool reads thresholds, not constants."""
    from app import db

    rid = "R-FIX-CIR-2026-08-min_attendance_pct-1.1"
    conn = db.connect()
    try:
        assert E.check_exam_eligibility("S1010", "CS201", NOW)["result"] == "ELIGIBLE"       # 80% >= 80
        conn.execute("UPDATE rule_registry SET value='90' WHERE rule_id=?", (rid,))
        conn.commit()
        assert E.check_exam_eligibility("S1010", "CS201", NOW)["result"] == "NOT_ELIGIBLE"   # 80% < 90
    finally:
        conn.execute("UPDATE rule_registry SET value='80' WHERE rule_id=?", (rid,))
        conn.commit()
        conn.close()
