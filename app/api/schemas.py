from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AnswerType = Literal["retrieved_fact", "calculated", "not_found", "clarification_needed", "refused", "conflict_flagged"]


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    as_of_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
                                   description="YYYY-MM-DD; defaults to today")


class Citation(BaseModel):
    doc_id: str
    title: str
    section: str | None = None
    page: int | None = None
    version: str | None = None
    effective_from: str | None = None
    authority_level: int | None = None


class ToolCall(BaseModel):
    tool: str
    input: dict[str, Any] = {}
    output: dict[str, Any] = {}


class AskResponse(BaseModel):
    trace_id: str
    answer: str
    answer_type: AnswerType
    citations: list[Citation] = []
    tools_invoked: list[ToolCall] = []
    applied_rules: list[dict[str, Any]] = []
    conflicts_detected: list[dict[str, Any]] = []
    explanation: str = ""
    as_of_date: str
    assumptions: list[str] = []
    upcoming_changes: list[dict[str, Any]] = []
