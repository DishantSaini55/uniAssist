"""Tool registry. Every call is logged (inputs, outputs, status, latency) for the audit record."""
from __future__ import annotations

import time
from typing import Any, Callable

from tools import eligibility, records

TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_student_profile": records.get_student_profile,
    "get_attendance": records.get_attendance,
    "get_results": records.get_results,
    "get_backlogs": records.get_backlogs,
    "check_exam_eligibility": eligibility.check_exam_eligibility,
    "check_supplementary_eligibility": eligibility.check_supplementary_eligibility,
    "check_placement_eligibility": eligibility.check_placement_eligibility,
    "what_if_pass_supplementary": eligibility.what_if_pass_supplementary,
    "what_if_attendance": eligibility.what_if_attendance,
}
NEEDS_AS_OF = {"check_exam_eligibility", "check_supplementary_eligibility", "check_placement_eligibility",
               "what_if_pass_supplementary", "what_if_attendance"}

TOOL_DOCS = {
    "get_student_profile": "Logged-in student's programme, batch, semester, CGPA, active backlogs. args: {}",
    "get_attendance": "Attendance % per course for the logged-in student. args: {course_code?}",
    "get_results": "Exam results/marks for the logged-in student. args: {course_code?}",
    "get_backlogs": "Active backlogs and outstanding courses. args: {}",
    "check_exam_eligibility": "Can the student appear in end-semester exams for a course (attendance rule). args: {course_code}",
    "check_supplementary_eligibility": "Is the student eligible for a course's supplementary exam. args: {course_code}",
    "check_placement_eligibility": "Placement eligibility from CGPA/backlog rules. args: {}",
    "what_if_pass_supplementary": "If the student passes the supplementary in a course, are they placement-eligible. args: {course_code}",
    "what_if_attendance": "If the student attends N more classes, do they meet the attendance rule. args: {course_code, extra_classes}",
}


class ToolRunner:
    """Binds the request context (student id, as-of date) so a tool can never be pointed at another student."""

    def __init__(self, student_id: str | None, as_of: str):
        self.student_id, self.as_of = student_id, as_of
        self.log: list[dict[str, Any]] = []

    def call(self, name: str, **args: Any) -> dict[str, Any]:
        full = {**args, "student_id": self.student_id}
        if name in NEEDS_AS_OF:
            full["as_of"] = self.as_of
        t0 = time.perf_counter()
        status, out = "ok", {}
        try:
            out = TOOLS[name](**full)
        except Exception as exc:  # a tool failure must surface, never be papered over by the LLM
            status, out = "error", {"error": type(exc).__name__}
        self.log.append({"tool": name, "input": dict(args), "output": out, "status": status,
                         "ms": round((time.perf_counter() - t0) * 1000)})
        return out
