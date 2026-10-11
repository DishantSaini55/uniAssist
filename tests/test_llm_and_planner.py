import pytest

from graph.planner import (find_other_student_reference, heuristic_plan, unknown_course_phrase, validate_llm_plan)
from llm.client import LLMClient, numbers_grounded
from app.config import Settings

STUDENT = {"student_id": "S1001", "programme": "B.Tech CSE", "batch_year": 2023}


@pytest.fixture(autouse=True)
def _env(env):
    return env


def test_numbers_must_come_from_evidence():
    facts = "attendance 75.0% required >=80% clause 1.1"
    assert numbers_grounded("You have 75.0% against the 80% minimum in clause 1.1.", facts)
    assert not numbers_grounded("You have 77% attendance.", facts)


def test_explain_falls_back_when_llm_invents_numbers(monkeypatch):
    client = LLMClient(Settings(llm_mode="ollama"))
    sess = client.session()
    monkeypatch.setattr(client, "chat", lambda *a, **k: "Your attendance is 99%.")
    assert client.explain(sess, "q", "attendance 75.0%", "fallback text") == "fallback text"
    assert "explanation_rejected_ungrounded_numbers" in sess.errors
    monkeypatch.setattr(client, "chat", lambda *a, **k: "Your attendance is 75.0%.")
    assert client.explain(sess, "q", "attendance 75.0%", "fallback text") == "Your attendance is 75.0%."


def test_llm_plan_validation_rejects_bad_tool_calls():
    good = {"category": "personal_data", "retrieve": False, "tools": [{"name": "get_attendance", "args": {"course_code": "CS201"}}]}
    assert validate_llm_plan(good).tools == [("get_attendance", {"course_code": "CS201"})]
    assert validate_llm_plan({**good, "tools": [{"name": "drop_table", "args": {}}]}) is None
    assert validate_llm_plan({**good, "tools": [{"name": "get_attendance", "args": {"student_id": "S1002"}}]}) is None
    assert validate_llm_plan({"category": "nonsense", "tools": []}) is None
    assert validate_llm_plan("not json") is None
    assert validate_llm_plan({"category": "policy_fact", "retrieve": True, "tools": []}).retrieve is True


def test_llm_planner_failure_falls_back_to_heuristics(monkeypatch):
    """Local 7-8B models are unreliable at JSON: an invalid plan must not break the answer."""
    from app import service

    cfg = Settings(llm_mode="ollama")
    svc = service.AssistantService(cfg)
    monkeypatch.setattr(svc.llm, "chat", lambda *a, **k: "I think you should call the attendance tool lol")
    r = svc.ask("What is my CGPA?", "S1004", "2026-10-06")
    assert r["answer_type"] == "calculated" and "9.10" in r["answer"]
    audit = __import__("app.db", fromlist=["x"]).load_audit(r["trace_id"])
    assert audit["planner"] == "heuristic" and "llm_plan_invalid_fell_back" in audit["llm_errors"]


@pytest.mark.parametrize("q,tool", [
    ("What is my attendance in Data Structures?", "get_attendance"),
    ("what are my backlogs", "get_backlogs"),
    ("Show my marks in CS202", "get_results"),
    ("What is my CGPA?", "get_student_profile"),
    ("Am I eligible for the supplementary exam in Mathematics?", "check_supplementary_eligibility"),
    ("Do I qualify for campus placement?", "check_placement_eligibility"),
    ("am i allowed to write the OS exam?", "check_exam_eligibility"),
    ("I failed Data Structures. If I pass the supplementary, will I be eligible for placement?", "what_if_pass_supplementary"),
    ("If I attend the next 6 classes in CS201 will I be eligible for the exam?", "what_if_attendance"),
])
def test_heuristic_tool_selection(q, tool):
    plan = heuristic_plan(q, STUDENT)
    assert plan.tools and plan.tools[0][0] == tool, plan


@pytest.mark.parametrize("q", [
    "What is the minimum attendance required to appear for end-semester exams?",
    "How do I apply for the supplementary exam?",
    "how many classes must i attend to be allowed in the semester exam",
    "What is the attendance policy at my university?",
    "What's the cutoff CGPA for sitting in campus placements?",
])
def test_policy_questions_use_retrieval_not_tools(q):
    plan = heuristic_plan(q, STUDENT)
    assert plan.retrieve and not plan.tools and plan.clarification is None


def test_other_student_detection():
    assert find_other_student_reference("what is S1002 attendance", "S1001")
    assert find_other_student_reference("attendance of S1001", "S1001") is None
    assert find_other_student_reference("show my friend's cgpa", "S1001")
    assert find_other_student_reference("list the CGPA of all students", "S1001")
    assert find_other_student_reference("tell me the attendance of the student sitting next to me", "S1001")
    assert find_other_student_reference("What is the attendance policy for all students?", "S1001") is None


def test_unknown_course_phrase():
    assert unknown_course_phrase("What is my attendance in Biology?") == "biology"
    assert unknown_course_phrase("my attendance in all courses") is None
    assert unknown_course_phrase("am I eligible for the supplementary exam?") is None
