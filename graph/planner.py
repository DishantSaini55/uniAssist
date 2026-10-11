"""Question understanding: category, tools to call, whether to retrieve, whether to ask for clarification.

The deterministic planner is the default and the fallback. When LLM_MODE=ollama the model proposes a plan,
which is accepted only if it validates against the tool registry; otherwise we fall back (R5, section 5.1).
Identity never comes from the question text: tools are bound to the X-Student-Id by ToolRunner.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any

from tools import TOOLS
from tools.records import resolve_course

CATEGORIES = {"policy_fact", "procedure", "personal_data", "personal_eligibility", "multi_step", "other"}
SELF = re.compile(r"\b(my|mine|i|i'm|i've|me|myself)\b")
DATA_WORDS = re.compile(r"attendance|marks|result|cgpa|\bgpa\b|backlog|grade|score|semester|percentage|detained|profile")
ELIG = re.compile(r"\b(eligible|eligibility|allowed|permitted|qualif\w+|able to (?:appear|sit)|can i (?:appear|sit|write|take|apply))\b")
# A question *about the asker's own eligibility* ("am I allowed...", "can I appear..."), as opposed to
# "how many classes must I attend to be allowed...", which asks for the rule.
SELF_Q = re.compile(r"\b(am i|can i|could i|may i|will i|would i|do i qualify|do i meet|i am (?:eligible|allowed)|my eligibility)\b")
_D = DATA_WORDS.pattern
# "my attendance", "attendance of mine", "I failed ...", but not "attendance policy at my university".
PERSONAL_DATA = re.compile(rf"\b(?:my|mine)\b.{{0,30}}(?:{_D})|(?:{_D}).{{0,20}}\b(?:of mine|for me)\b|"
                           rf"\bam i (?:detained|failing)\b|\bi (?:failed|got|scored|attended|have)\b.{{0,30}}(?:{_D})|"
                           rf"(?:{_D}).{{0,30}}\b(?:do i have|i have|have i|i've)\b")
OTHER_PERSON = re.compile(r"\b(another|other|someone else|somebody|friend|classmate|roommate|brother|sister|everyone|"
                          r"everybody|all students|all the students|his|her|their|topper|next to me|beside me|"
                          r"sitting (?:next|beside|near)|neighbou?r|colleague|senior|junior|batchmate)\b")
STUDENT_ID = re.compile(r"\bs\d{4}\b", re.I)
COURSE_CODE = re.compile(r"\b[A-Z]{2,4}\d{3}\b")


@dataclass
class Plan:
    category: str = "policy_fact"
    retrieve: bool = False
    tools: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    clarification: str | None = None
    not_found: str | None = None
    source: str = "heuristic"
    notes: list[str] = field(default_factory=list)


def tool_args(name: str) -> set[str]:
    return set(inspect.signature(TOOLS[name]).parameters) - {"student_id", "as_of"}


def validate_llm_plan(raw: dict | None) -> Plan | None:
    if not isinstance(raw, dict) or raw.get("category") not in CATEGORIES:
        return None
    tools: list[tuple[str, dict[str, Any]]] = []
    for t in raw.get("tools") or []:
        if not isinstance(t, dict) or t.get("name") not in TOOLS:
            return None
        args = t.get("args") or {}
        if not isinstance(args, dict) or not set(args) <= tool_args(t["name"]):
            return None
        tools.append((t["name"], args))
    clar = raw.get("clarification")
    if clar is not None and not isinstance(clar, str):
        return None
    if not tools and not raw.get("retrieve") and not clar:
        return None
    return Plan(category=raw["category"], retrieve=bool(raw.get("retrieve")), tools=tools,
                clarification=clar or None, source="llm")


def _course_options(student: dict | None) -> list[dict[str, Any]]:
    from app import db

    if not student:
        return []
    return db.rows("SELECT c.course_code, c.course_name FROM attendance a JOIN courses c USING(course_code) "
                   "WHERE a.student_id=? ORDER BY 1", (student["student_id"],))


def _ask_course(student: dict | None, why: str) -> Plan:
    opts = _course_options(student)
    names = ", ".join(f"{c['course_name']} ({c['course_code']})" for c in opts)
    return Plan(category="personal_eligibility", clarification=f"Which course do you mean? {why}"
                + (f" Your courses: {names}." if names else ""))


_GENERIC = {"all", "every", "each", "this", "that", "current", "semester", "courses", "subjects", "total", "overall",
            "exam", "exams", "examination", "placement", "supplementary", "end", "sem", "record", "records", "details",
            "detail", "general", "campus", "mid", "regular", "result", "results", "attendance", "marks", "my", "me"}


def unknown_course_phrase(question: str) -> str | None:
    """'attendance in Biology' where Biology is not a known course -> 'biology'."""
    m = re.search(r"\b(?:in|for|of|about)\s+(?:the\s+|my\s+)?([a-z][a-z &\-]{2,40}?)"
                  r"(?:\s+(?:course|subject|exam|examination|class|classes|paper))?\s*[?.!]*$",
                  question.lower().strip())
    if not m:
        return None
    phrase = m.group(1).strip()
    return None if set(re.findall(r"[a-z]+", phrase)) & _GENERIC else phrase


def heuristic_plan(question: str, student: dict | None) -> Plan:
    q = question.lower()
    has_self = bool(SELF.search(q))
    prog = student["programme"] if student else None
    found = resolve_course(question, prog)["matches"]
    code_tokens = COURSE_CODE.findall(question.upper())
    unknown_code = [c for c in code_tokens if c not in {m["course_code"] for m in found}]
    course = found[0]["course_code"] if len(found) == 1 else None

    # ---- what-if / multi-step ------------------------------------------------------------
    if re.search(r"\bif\b", q) and (has_self or student):
        if "supplementary" in q and re.search(r"pass|clear|qualify|cleared", q) and re.search(r"placement|campus|eligible", q):
            if not course:
                backlog_courses = [c for c in _course_options(student)] if student else []
                from tools.records import get_backlogs

                out = get_backlogs(student["student_id"])["outstanding_courses"] if student else []
                if len(out) == 1:
                    course = out[0]["course_code"]
                else:
                    return _ask_course(student, "A supplementary applies to one course.") if backlog_courses else \
                        Plan(category="multi_step", clarification="Which course's supplementary do you mean?")
            return Plan("multi_step", False, [("what_if_pass_supplementary", {"course_code": course})],
                        notes=["what-if: supplementary pass -> placement"])
        m = re.search(r"(\d+)\s+(?:more\s+|extra\s+|additional\s+)?(?:classes|lectures)", q)
        if m and "attend" in q:
            if not course:
                return _ask_course(student, "I need the course to project your attendance.")
            return Plan("multi_step", False, [("what_if_attendance", {"course_code": course,
                                                                       "extra_classes": int(m.group(1))})])
        m = re.search(r"cgpa[^0-9]{0,25}(\d+(?:\.\d+)?)", q)
        if m and "placement" in q:
            return Plan("multi_step", False, [("check_placement_eligibility", {"cgpa": float(m.group(1))})],
                        notes=["what-if: hypothetical CGPA"])

    # ---- personal eligibility -------------------------------------------------------------
    if SELF_Q.search(q) and ELIG.search(q):
        if "supplementary" in q:
            if len(found) > 1:
                return _ask_course(student, "More than one course matches.")
            if not course and not unknown_code:
                return _ask_course(student, "A supplementary exam is course specific.")
            return Plan("personal_eligibility", False, [("check_supplementary_eligibility",
                                                         {"course_code": course or unknown_code[0]})])
        if "placement" in q or "campus" in q or "recruit" in q:
            return Plan("personal_eligibility", False, [("check_placement_eligibility", {})])
        if re.search(r"exam|appear|sit|attendance|end[- ]?sem|semester", q):
            if len(found) > 1:
                return _ask_course(student, "More than one course matches.")
            if course or unknown_code:
                return Plan("personal_eligibility", False, [("check_exam_eligibility", {"course_code": course or unknown_code[0]})])
            opts = _course_options(student)
            if not opts:
                return Plan("personal_eligibility", False, [("check_exam_eligibility", {"course_code": "-"})])
            return Plan("personal_eligibility", False,
                        [("check_exam_eligibility", {"course_code": c["course_code"]}) for c in opts],
                        notes=["no course named: checked every course"])

    # ---- unknown course named ---------------------------------------------------------------
    if student and not found and not code_tokens and (PERSONAL_DATA.search(q) or (SELF_Q.search(q) and ELIG.search(q))):
        phrase = unknown_course_phrase(question)
        if phrase and not re.search(r"cgpa|placement|backlog", q):
            return Plan("personal_data", not_found=f"no course matching '{phrase}' among your courses")

    # ---- personal data ----------------------------------------------------------------------
    if PERSONAL_DATA.search(q):
        if len(found) > 1:
            return _ask_course(student, "More than one course matches.")
        args = {"course_code": course or unknown_code[0]} if (course or unknown_code) else {}
        if "attendance" in q:
            return Plan("personal_data", False, [("get_attendance", args)])
        if "backlog" in q:
            return Plan("personal_data", False, [("get_backlogs", {})])
        if re.search(r"marks|result|grade|score|detained", q):
            return Plan("personal_data", False, [("get_results", args)])
        return Plan("personal_data", False, [("get_student_profile", {})])

    # ---- documents ----------------------------------------------------------------------------
    cat = "procedure" if re.match(r"\s*(how|what (?:are|is) the (?:steps|procedure|process)|steps|procedure|process)", q) \
        or re.search(r"\bhow (?:do|can|to|should)\b", q) else "policy_fact"
    return Plan(cat, True)


def find_other_student_reference(question: str, own_id: str | None) -> str | None:
    """R7: refuse requests that target another student, by id or by description."""
    ids = {m.upper() for m in STUDENT_ID.findall(question)}
    others = ids - ({own_id.upper()} if own_id else set())
    if others:
        return f"the question refers to student {sorted(others)[0]}"
    q = question.lower()
    if OTHER_PERSON.search(q) and (DATA_WORDS.search(q) or "record" in q or "details" in q or "data" in q):
        # "their attendance policy" is about policy, not a person: require a data word and no policy word
        if not re.search(r"\b(policy|rule|regulation|requirement|procedure|process|criteria)\b", q) or \
                re.search(r"\b(his|her|friend|classmate|roommate|brother|sister|topper|another student|next to me|beside me|sitting (?:next|beside|near)|neighbou?r|colleague|senior|junior|batchmate)\b", q):
            return "the question asks about someone else's records"
    return None
