"""Deterministic eligibility checks and what-if simulations. No LLM arithmetic, no LLM judgement."""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

from tools import records
from tools.rules import compare, resolve_rule


def _ctx(student_id: str | None):
    s = records.get_student_profile(student_id) if student_id else {"found": False}
    return s if s["found"] else None


def _rule_block(rr: dict[str, Any]) -> dict[str, Any]:
    return {"status": rr["status"], "rule": rr.get("rule"), "precedence": rr.get("precedence"),
            "upcoming": rr.get("upcoming", []), "conflicts": rr.get("conflicts", [])}


def check_exam_eligibility(student_id: str, course_code: str, as_of: str) -> dict[str, Any]:
    s = _ctx(student_id)
    if not s:
        return {"result": "NO_RECORD", "reason": "student not found"}
    att = records.get_attendance(student_id, course_code)["records"]
    if not att:
        return {"result": "NO_RECORD", "student_id": student_id, "course_code": course_code,
                "reason": "no attendance record for this course"}
    a = att[0]
    rr = resolve_rule("min_attendance_pct", as_of, s["programme"], s["batch_year"])
    out = {"student_id": student_id, "course_code": course_code, "course_name": a["course_name"],
           "classes_held": a["classes_held"], "classes_attended": a["classes_attended"],
           "attendance_pct": a["attendance_pct"], **_rule_block(rr)}
    if rr["status"] == "no_rule":
        return {**out, "result": "NO_RULE"}
    if rr["status"] == "conflict":
        return {**out, "result": "CONFLICT"}
    req = Fraction(rr["rule"]["value"])
    # Exact rational comparison: attended/held >= required/100 (no float rounding at the threshold).
    ok = Fraction(a["classes_attended"], a["classes_held"]) * 100 >= req
    short = 0 if ok else math.ceil(a["classes_held"] * req / 100 - a["classes_attended"])
    return {**out, "required_pct": float(req), "meets_threshold": ok, "classes_short": short,
            "result": "ELIGIBLE" if ok else "NOT_ELIGIBLE"}


def check_supplementary_eligibility(student_id: str, course_code: str, as_of: str) -> dict[str, Any]:
    s = _ctx(student_id)
    if not s:
        return {"result": "NO_RECORD", "reason": "student not found"}
    res = records.get_results(student_id, course_code)["results"]
    regular = [r for r in res if r["exam_type"] == "REGULAR"]
    if not regular:
        return {"result": "NO_RECORD", "student_id": student_id, "course_code": course_code,
                "reason": "no regular-exam result for this course"}
    last_reg = regular[-1]
    rr = resolve_rule("supplementary_allowed_results", as_of, s["programme"], s["batch_year"])
    out = {"student_id": student_id, "course_code": course_code, "regular_result": last_reg["result"],
           "regular_marks": f"{last_reg['total_marks']}/{last_reg['max_marks']}", **_rule_block(rr)}
    if rr["status"] == "no_rule":
        return {**out, "result": "NO_RULE"}
    if rr["status"] == "conflict":
        return {**out, "result": "CONFLICT"}
    if any(r["exam_type"] == "SUPPLEMENTARY" and r["result"] == "PASS" for r in res):
        return {**out, "result": "NOT_ELIGIBLE", "reason": "the course was already cleared in a supplementary exam"}
    allowed = rr["rule"]["value"].split(",")
    ok = last_reg["result"] in allowed
    reason = (f"the regular result is {last_reg['result']}, which qualifies for the supplementary exam" if ok else
              f"the regular result is {last_reg['result']}; the rule allows only {'/'.join(allowed)}")
    return {**out, "allowed_results": allowed, "reason": reason, "result": "ELIGIBLE" if ok else "NOT_ELIGIBLE"}


def check_placement_eligibility(student_id: str, as_of: str, cgpa: float | None = None,
                                active_backlogs: int | None = None) -> dict[str, Any]:
    s = _ctx(student_id)
    if not s:
        return {"result": "NO_RECORD", "reason": "student not found"}
    use_cgpa = s["cgpa"] if cgpa is None else cgpa
    use_bl = s["active_backlogs"] if active_backlogs is None else active_backlogs
    checks: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    status = "ELIGIBLE"
    for param, actual, label in (("placement_min_cgpa", use_cgpa, "CGPA"),
                                 ("placement_max_active_backlogs", use_bl, "active backlogs")):
        rr = resolve_rule(param, as_of, s["programme"], s["batch_year"])
        blocks.append(_rule_block(rr))
        if rr["status"] == "conflict":
            status = "CONFLICT"
            checks.append({"criterion": label, "actual": actual, "passed": None, "conflict": True})
        elif rr["status"] == "no_rule":
            checks.append({"criterion": label, "actual": actual, "passed": None, "missing": True})
        else:
            r = rr["rule"]
            passed = compare(r["operator"], float(actual), r["value"])
            checks.append({"criterion": label, "actual": actual, "required": r["display"], "passed": passed,
                           "rule_id": r["rule_id"]})
            if not passed and status == "ELIGIBLE":
                status = "NOT_ELIGIBLE"
    missing = [c for c in checks if c.get("missing")]
    if missing and status != "CONFLICT" and not any(c["passed"] is False for c in checks):
        status = "NO_RULE"  # cannot certify eligibility while a criterion has no applicable rule
    return {"student_id": student_id, "result": status, "checks": checks,
            "rules": [b["rule"] for b in blocks if b["rule"]],
            "precedence": " ".join(b["precedence"] or "" for b in blocks),
            "upcoming": [u for b in blocks for u in b["upcoming"]],
            "conflicts": [c for b in blocks for c in b["conflicts"]]}


def what_if_pass_supplementary(student_id: str, course_code: str, as_of: str) -> dict[str, Any]:
    s = _ctx(student_id)
    if not s:
        return {"result": "NO_RECORD", "reason": "student not found"}
    supp = check_supplementary_eligibility(student_id, course_code, as_of)
    pending = {b["course_code"] for b in records.get_backlogs(student_id)["outstanding_courses"]}
    out: dict[str, Any] = {"student_id": student_id, "course_code": course_code, "supplementary": supp,
                           "assumptions": []}
    if supp["result"] != "ELIGIBLE" or course_code not in pending:
        return {**out, "result": "NOT_APPLICABLE",
                "reason": "there is no outstanding backlog in this course that a supplementary pass would clear"}
    new_bl = max(0, s["active_backlogs"] - 1)
    out["assumptions"] = [
        f"Passing the supplementary clears the backlog in {course_code}: active backlogs "
        f"{s['active_backlogs']} -> {new_bl}.",
        f"CGPA stays at {s['cgpa']} (a supplementary pass may change it; that recalculation is not modelled).",
    ]
    placement = check_placement_eligibility(student_id, as_of, active_backlogs=new_bl)
    return {**out, "hypothetical": {"active_backlogs": new_bl, "cgpa": s["cgpa"]}, "placement": placement,
            "result": placement["result"]}


def what_if_attendance(student_id: str, course_code: str, extra_classes: int, as_of: str) -> dict[str, Any]:
    s = _ctx(student_id)
    att = records.get_attendance(student_id, course_code)["records"] if s else []
    if not s or not att:
        return {"result": "NO_RECORD", "reason": "no attendance record"}
    a = att[0]
    held, attended = a["classes_held"] + extra_classes, a["classes_attended"] + extra_classes
    rr = resolve_rule("min_attendance_pct", as_of, s["programme"], s["batch_year"])
    out = {"student_id": student_id, "course_code": course_code, "current_pct": a["attendance_pct"],
           "hypothetical_pct": records.pct(attended, held), "hypothetical_held": held,
           "hypothetical_attended": attended, **_rule_block(rr),
           "assumptions": [f"The next {extra_classes} classes are all held and all attended."]}
    if rr["status"] != "ok":
        return {**out, "result": "NO_RULE" if rr["status"] == "no_rule" else "CONFLICT"}
    ok = Fraction(attended, held) * 100 >= Fraction(rr["rule"]["value"])
    return {**out, "required_pct": float(rr["rule"]["value"]), "result": "ELIGIBLE" if ok else "NOT_ELIGIBLE"}
