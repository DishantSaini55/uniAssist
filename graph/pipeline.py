"""LangGraph workflow for one question.

    guard -> plan -> retrieve -> tools -> compose -> finalize
       |        |  (conditional skips)                  ^
       +--------+----> finalize  (refused / clarification_needed)

Design note (why a fixed graph and not a supervisor with specialist agents): every step is either
deterministic (guard, retrieval, precedence, tools, composition) or a single, validated LLM call
(planning, explanation). There is no step where autonomous multi-agent behaviour would buy anything,
and a fixed graph is auditable end to end.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app import db, registry
from graph import compose as C
from graph.planner import Plan, find_other_student_reference, heuristic_plan, validate_llm_plan
from llm.client import LLMClient
from rag.retriever import Retriever
from tools import TOOL_DOCS, ToolRunner
from tools.records import get_student_profile

REFUSAL_OTHER = ("I can only share the records of the logged-in student. I cannot provide another student's data "
                 "({why}).")
REFUSAL_ANON = ("This question is about personal records, but no student identity was provided. "
                "Send the X-Student-Id header (log in) to ask about your own data.")


class State(TypedDict, total=False):
    question: str
    student_id: str | None
    as_of: str
    trace_id: str
    t0: float
    student: dict | None
    plan: Plan
    retrieval: Any
    tool_log: list
    composed: C.Composed
    refusal: str | None
    clarification: str | None
    not_found: str | None
    llm: Any
    category: str
    response: dict


class Pipeline:
    def __init__(self, retriever: Retriever, llm: LLMClient):
        self.retriever, self.llm = retriever, llm
        self.graph = self._build()

    # ------------------------------------------------------------------------------ nodes
    def guard(self, s: State) -> dict:
        student = None
        sid = s.get("student_id")
        if sid:
            profile = get_student_profile(sid.upper())
            student = profile if profile["found"] else None
        why = find_other_student_reference(s["question"], sid)
        if why:
            return {"student": student, "refusal": REFUSAL_OTHER.format(why=why), "category": "other_student_data"}
        if sid and not student:
            return {"student": None, "refusal": f"The student id in the request context ({sid}) is not a known student.",
                    "category": "unknown_identity"}
        return {"student": student}

    def plan(self, s: State) -> dict:
        student = s.get("student")
        sess = s["llm"]
        plan = None
        if self.llm.enabled and student:
            courses = [f"{c['course_name']} ({c['course_code']})" for c in db.rows(
                "SELECT c.course_code, c.course_name FROM attendance a JOIN courses c USING(course_code) "
                "WHERE a.student_id=?", (student["student_id"],))]
            plan = validate_llm_plan(self.llm.plan(sess, s["question"], TOOL_DOCS, courses))
            if plan is None:
                sess.errors.append("llm_plan_invalid_fell_back")
        if plan is None:
            plan = heuristic_plan(s["question"], student)
        if plan.tools and not student:
            return {"plan": plan, "refusal": REFUSAL_ANON, "category": "personal_no_identity"}
        return {"plan": plan, "category": plan.category, "clarification": plan.clarification,
                "not_found": plan.not_found}

    def retrieve(self, s: State) -> dict:
        st = s.get("student")
        rr = self.retriever.retrieve(s["question"], s["as_of"], st["programme"] if st else None,
                                     st["batch_year"] if st else None)
        return {"retrieval": rr}

    def tools(self, s: State) -> dict:
        runner = ToolRunner(s["student"]["student_id"], s["as_of"])
        for name, args in s["plan"].tools:
            runner.call(name, **args)
        return {"tool_log": runner.log}

    def compose(self, s: State) -> dict:
        page_fn = lambda d, sec: self._page(d, sec)  # noqa: E731
        plan = s["plan"]
        if s.get("tool_log"):
            c = C.compose_tools(s["question"], s["tool_log"], page_fn)
        else:
            c = C.compose_retrieval(s["question"], s["retrieval"], page_fn, plan.category, registry.sources_by_id())
        if self.llm.enabled and c.answer_type in {"calculated", "retrieved_fact"} and c.facts:
            c.explanation = self.llm.explain(s["llm"], s["question"], c.facts, c.explanation)
        return {"composed": c}

    def finalize(self, s: State) -> dict:
        if s.get("refusal"):
            c = C.Composed(s["refusal"], "refused", explanation="Request not permitted (R7).")
        elif s.get("not_found"):
            c = C.Composed(f"{C.NOT_FOUND} ({s['not_found']}.)", "not_found", explanation="No matching course record.")
        elif s.get("clarification") and not s.get("tool_log"):
            c = C.Composed(s["clarification"], "clarification_needed", explanation="The question is ambiguous.")
        else:
            c = s["composed"]
        latency = round((time.perf_counter() - s["t0"]) * 1000)
        sess = s["llm"]
        tools = s.get("tool_log") or []
        rr = s.get("retrieval")
        trace = s["trace_id"]
        response = {
            "trace_id": trace, "answer": c.answer, "answer_type": c.answer_type, "citations": c.citations,
            "tools_invoked": [{"tool": t["tool"], "input": t["input"], "output": t["output"]} for t in tools],
            "applied_rules": c.applied_rules, "conflicts_detected": c.conflicts, "explanation": c.explanation,
            "as_of_date": s["as_of"], "assumptions": c.assumptions, "upcoming_changes": c.upcoming_changes,
        }
        audit = {
            "trace_id": trace, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "student_id": (s.get("student") or {}).get("student_id") or s.get("student_id"),
            "question_category": s.get("category") or "other", "as_of_date": s["as_of"],
            "question_sha256": hashlib.sha256(s["question"].encode()).hexdigest()[:16],  # not the text: privacy
            "sources_retrieved": [{"doc_id": h["doc_id"], "section": h["section"], "score": h["score"]}
                                  for h in (rr.raw_topk if rr else [])],
            "precedence_decision": (rr.resolution.decision if rr else c.precedence) or c.precedence or "n/a",
            "tools_invoked": [{"tool": t["tool"], "input": t["input"], "output": t["output"], "status": t["status"],
                               "ms": t["ms"]} for t in tools],
            "rules_applied": c.applied_rules, "conflicts_detected": c.conflicts,
            "answer_type": c.answer_type,
            "planner": s["plan"].source if s.get("plan") else "n/a",
            "model": sess.model, "llm_calls": sess.calls, "tokens": sess.tokens, "llm_errors": sess.errors,
            "latency_ms": latency,
            "retrieval_config": {"embedding": self.retriever.store.embedder.name if rr is not None else None,
                                 "chunking": self.retriever.cfg.chunking, "top_k": self.retriever.cfg.top_k},
            "abstention_signals": ({"best_score": rr.best_score, "term_coverage": round(rr.coverage, 2),
                                    "unmatched_terms": rr.unmatched_terms} if rr is not None else None),
        }
        db.save_audit(audit)
        return {"response": response}

    # ----------------------------------------------------------------------------- wiring
    def _page(self, doc_id: str, section: str) -> int | None:
        try:
            return self.retriever.store.page_of(doc_id, section)
        except Exception:
            return None

    def _build(self):
        g = StateGraph(State)
        for name in ("guard", "plan", "retrieve", "tools", "compose", "finalize"):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "guard")
        g.add_conditional_edges("guard", lambda s: "finalize" if s.get("refusal") else "plan",
                                {"finalize": "finalize", "plan": "plan"})
        g.add_conditional_edges("plan", self._after_plan,
                                {"finalize": "finalize", "retrieve": "retrieve", "tools": "tools"})
        g.add_edge("retrieve", "compose")
        g.add_edge("tools", "compose")
        g.add_edge("compose", "finalize")
        g.add_edge("finalize", END)
        return g.compile()

    @staticmethod
    def _after_plan(s: State) -> str:
        if s.get("refusal") or s.get("clarification") or s.get("not_found"):
            return "finalize"
        return "tools" if s["plan"].tools else "retrieve"

    def run(self, question: str, student_id: str | None, as_of: str) -> dict:
        state = self.graph.invoke({"question": question, "student_id": student_id, "as_of": as_of,
                                   "trace_id": uuid.uuid4().hex[:12], "t0": time.perf_counter(),
                                   "llm": self.llm.session()})
        return state["response"]
