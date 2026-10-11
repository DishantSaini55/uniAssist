"""Deterministic extraction of comparable claims (topic, value) from document text.

Claims serve two purposes: (1) detecting that two documents disagree so the Annex A precedence
policy can be applied, and (2) proposing rule_registry rows when a document is ingested.
The LLM is never involved: a threshold the tools use must trace to a clause of a cited document.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Claim:
    topic: str           # also the rule_registry `parameter` for rule topics
    value: str           # normalised: "75", "6.5", "FAIL,ABSENT"
    operator: str        # >=, <=, in, ==
    description: str
    sentence: str
    is_rule: bool = True

    def key(self) -> tuple[str, str]:
        return self.topic, self.value


# A newline ends a sentence only before a numbered clause / table marker / bullet; other newlines are PDF line wraps.
_ITEM_BREAK = re.compile(r"\n(?=\s*(?:\d+(?:\.\d+)+[.)]?\s|\d+[.)]\s|\d+\s+[A-Z]|\[/?TABLE\]|[-•*]\s))|\n{2,}")
_SENT = re.compile(r"(?<=[.!?;])\s+")
_NUM = r"(\d{1,3}(?:\.\d+)?)"
_CONDONE = re.compile(r"condon|relax|shortage|shortfall|waiv|upto|up to|exempt|medical", re.I)
# "attendance below 80% ... will not be allowed to appear" states the same minimum as "at least 80%".
_BELOW = re.compile(r"\b(?:below|less than|under|lower than)\b", re.I)
_BARRED = re.compile(r"\b(?:not (?:be )?(?:allowed|permitted|eligible|admitted)|barred|debarred|detain\w*|ineligible|"
                     r"disallowed|prohibited|shall not (?:appear|be allowed)|cannot appear)\b", re.I)


def blocks(text: str) -> list[str]:
    """Clause/step-level blocks with PDF line wraps joined (no sentence splitting)."""
    return [re.sub(r"\s+", " ", b).strip() for b in _ITEM_BREAK.split(text) if b.strip()]


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for block in _ITEM_BREAK.split(text):
        block = re.sub(r"\s+", " ", block)
        out += [s.strip() for s in _SENT.split(block) if s.strip()]
    return out


def _attendance(s: str) -> Claim | None:
    if not re.search(r"attendance|classes|lectures", s, re.I) or _CONDONE.search(s):
        return None
    if _BELOW.search(s) and not _BARRED.search(s):
        return None
    pcts = re.findall(_NUM + r"\s*(?:%|per\s*cent|percent)", s, re.I)
    if len(set(pcts)) != 1:
        return None
    v = float(pcts[0])
    if not 30 <= v <= 100:
        return None
    return Claim("min_attendance_pct", f"{v:g}", ">=",
                 "Minimum attendance required to appear for end-semester examinations", s)


def _placement_cgpa(s: str, ctx: str) -> Claim | None:
    if not re.search(r"cgpa|cpi|\bgpa\b", s, re.I) or not re.search(r"placement|campus|recruit", ctx, re.I):
        return None
    m = re.search(r"(?:cgpa|cpi|gpa)[^.\d]{0,70}?" + _NUM, s, re.I) or re.search(_NUM + r"[^.\d]{0,20}(?:cgpa|cpi)", s, re.I)
    if not m or not 0 < float(m.group(1)) <= 10:
        return None
    return Claim("placement_min_cgpa", f"{float(m.group(1)):g}", ">=",
                 "Minimum CGPA to be eligible for campus placement", s)


def _placement_backlogs(s: str, ctx: str) -> Claim | None:
    if not re.search(r"backlog", s, re.I) or not re.search(r"placement|campus|recruit", ctx, re.I):
        return None
    if re.search(r"\bno\b[^.]{0,30}backlog|without[^.]{0,20}backlog|zero[^.]{0,20}backlog|backlog[^.]{0,25}\b(?:nil|zero)\b", s, re.I):
        n = 0
    else:
        m = re.search(r"(?:more than|at most|maximum of|not exceed(?:ing)?|up to|upto)\s+(\d+)[^.]{0,25}backlog", s, re.I)
        if not m:
            return None
        n = int(m.group(1))
    return Claim("placement_max_active_backlogs", str(n), "<=",
                 "Maximum active backlogs permitted for campus placement", s)


def _pass_marks(s: str) -> Claim | None:
    if not re.search(r"\bpass(?:es|ing)?\b", s, re.I) or not re.search(r"marks|score", s, re.I):
        return None
    m = re.search(r"(?:minimum|at least|not less than|secure|obtain)[^.%\d]{0,60}" + _NUM + r"\s*%", s, re.I) or \
        re.search(_NUM + r"\s*%[^.]{0,60}(?:to pass|for passing|pass mark)", s, re.I)
    if not m or not 20 <= float(m.group(1)) <= 80:
        return None
    return Claim("pass_marks_pct", f"{float(m.group(1)):g}", ">=",
                 "Minimum percentage of maximum marks (internal + external) required to pass a course", s)


def _supplementary(s: str) -> Claim | None:
    if not re.search(r"supplementary", s, re.I):
        return None
    allowed = []
    if re.search(r"\bfail(?:ed|ure)?\b|\bF grade\b", s, re.I):
        allowed.append("FAIL")
    if re.search(r"\babsent\b", s, re.I):
        allowed.append("ABSENT")
    if not allowed or re.search(r"\bnot\b[^.]{0,40}\b(?:failed|absent)\b", s, re.I):
        return None
    return Claim("supplementary_allowed_results", ",".join(allowed), "in",
                 "Regular-exam results for which a student may appear in the supplementary examination", s)


def _deadline(s: str, ctx: str) -> Claim | None:
    m = re.search(r"within\s+(\d+)\s+(?:working\s+|calendar\s+)?days", s, re.I)
    if not m:
        return None
    if re.search(r"re-?evaluation|revaluation|re-?check|re-?totall?ing", ctx, re.I):
        return Claim("reevaluation_deadline_days", m.group(1), "<=", "Re-evaluation request deadline (days)", s, False)
    if re.search(r"supplementary", ctx, re.I) and re.search(r"appl|register|form", ctx, re.I):
        return Claim("supplementary_apply_deadline_days", m.group(1), "<=", "Supplementary application deadline (days)", s, False)
    return None


def extract_claims(text: str) -> list[Claim]:
    out: list[Claim] = []
    for s in sentences(text):
        for c in (_attendance(s), _placement_cgpa(s, text), _placement_backlogs(s, text), _pass_marks(s),
                  _supplementary(s), _deadline(s, text)):
            if c:
                out.append(c)
    return out
