"""Source Precedence Policy (Annex A) as pure, testable code.

Order applied when documents disagree:
  1. Applicability   effective on as_of_date and scope covers the student. Future documents are
                     excluded but reported as upcoming changes.
  2. Supersession    a level 1-2 document that explicitly supersedes a document/clause replaces it.
  3. Authority       a higher authority (lower level number) prevails over a lower one.
  4. Recency         between equal authority, the later effective_from prevails.
  5. Unresolved      otherwise report conflict_flagged; never silently choose.
Level 5 (unofficial) content is informational only and can never override anything.
Authority/recency are applied only between candidates that make *different claims about the same
topic*; complementary passages from different authorities are all kept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

LEVEL_NAMES = {1: "statute/ordinance/regulation", 2: "official circular", 3: "department notice",
               4: "handbook/FAQ", 5: "unofficial"}


@dataclass
class Candidate:
    id: str                      # chunk id or rule id
    doc_id: str
    title: str
    authority_level: int
    effective_from: str
    effective_to: str | None = None
    supersedes: str = ""
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    section: str = ""
    version: str = ""
    score: float = 0.0
    claims: list[tuple[str, str]] = field(default_factory=list)   # (topic, value)
    payload: dict[str, Any] = field(default_factory=dict)

    def brief(self) -> dict[str, Any]:
        return {"id": self.id, "doc_id": self.doc_id, "section": self.section,
                "authority_level": self.authority_level, "effective_from": self.effective_from}


@dataclass
class Resolution:
    winners: list[Candidate] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    upcoming: list[Candidate] = field(default_factory=list)
    informational: list[Candidate] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def decision(self) -> str:
        return " ".join(self.steps) if self.steps else "No precedence conflict: the applicable sources agree."


# ------------------------------------------------------------------------------ scope
def _tokens(s: str | None) -> list[str]:
    return [t.strip() for t in re.split(r"[;,]", s or "") if t.strip()]


def batch_in_scope(scope: str | None, batch: int | None) -> bool:
    toks = _tokens(scope)
    if not toks or any(t.upper() == "ALL" for t in toks) or batch is None:
        return True
    for t in toks:
        if re.fullmatch(r"\d{4}\+", t) and batch >= int(t[:-1]):
            return True
        m = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", t)
        if m and int(m.group(1)) <= batch <= int(m.group(2)):
            return True
        if re.fullmatch(r"\d{4}", t) and int(t) == batch:
            return True
    return False


def programme_in_scope(scope: str | None, programme: str | None) -> bool:
    toks = _tokens(scope)
    if not toks or any(t.upper() == "ALL" for t in toks) or not programme:
        return True
    p = programme.lower()
    return any(t.lower() in p or p in t.lower() for t in toks)


def parse_supersedes(value: str | None) -> list[tuple[str, str | None]]:
    out = []
    for t in _tokens(value):
        doc, _, clause = t.partition("#")
        out.append((doc.strip().lower(), _norm_clause(clause) if clause else None))
    return out


def _norm_clause(c: str) -> str:
    return re.sub(r"^(clause|section|§|para(graph)?)\s*", "", c.strip(), flags=re.I).strip().lower()


def _clause_covers(clause: str, section: str) -> bool:
    s = _norm_clause(section)
    return s == clause or s.startswith(clause + ".")


# ---------------------------------------------------------------------------- resolve
def resolve(cands: list[Candidate], as_of: str, programme: str | None = None, batch: int | None = None) -> Resolution:
    res = Resolution()
    if programme is None and batch is None and any(
            c.scope_programmes.upper() != "ALL" or c.scope_batches.upper() != "ALL" for c in cands):
        res.notes.append("No student identity was supplied, so programme/batch scope was not applied.")

    # 1. Applicability ---------------------------------------------------------------
    live: list[Candidate] = []
    for c in cands:
        in_scope = programme_in_scope(c.scope_programmes, programme) and batch_in_scope(c.scope_batches, batch)
        if not in_scope:
            res.excluded.append({**c.brief(), "step": 1, "reason": "outside the student's programme/batch scope"})
        elif c.effective_from and c.effective_from > as_of:
            if c.authority_level <= 3:   # FAQs/handbooks/unofficial text cannot announce a change
                res.upcoming.append(c)
        elif c.effective_to and c.effective_to < as_of:
            res.excluded.append({**c.brief(), "step": 1, "reason": f"expired on {c.effective_to}"})
        else:
            live.append(c)
    seen_up = set()
    for c in res.upcoming:
        if c.doc_id not in seen_up and c.authority_level <= 3:
            seen_up.add(c.doc_id)
            res.steps.append(f"Applicability (step 1): {c.doc_id} takes effect on {c.effective_from}, after the "
                             f"as-of date {as_of}, so it is not treated as current (upcoming change).")

    # 2. Explicit supersession -------------------------------------------------------
    remaining: list[Candidate] = []
    for x in live:
        replaced_by = None
        for y in live:
            if y.doc_id == x.doc_id or y.authority_level > 2:
                continue
            for doc, clause in parse_supersedes(y.supersedes):
                if doc == x.doc_id.lower() and (clause is None or _clause_covers(clause, x.section)):
                    replaced_by = (y, clause)
                    break
            if replaced_by:
                break
        if replaced_by:
            y, clause = replaced_by
            what = f"{x.doc_id} clause {clause}" if clause else x.doc_id
            msg = (f"Explicit supersession (step 2): {y.doc_id} (level {y.authority_level}) supersedes {what}, "
                   f"so that text is not applied.")
            if msg not in res.steps:
                res.steps.append(msg)
            res.excluded.append({**x.brief(), "step": 2, "reason": f"superseded by {y.doc_id}"})
        else:
            remaining.append(x)

    # Level 5 content is informational only.
    official = [c for c in remaining if c.authority_level < 5]
    res.informational = [c for c in remaining if c.authority_level >= 5]
    if res.informational:
        res.steps.append("Level 5 (unofficial) content is informational only and cannot override an official source.")

    # 3-5. Conflicts between different claims on the same topic ----------------------
    dropped: set[str] = set()
    topics: dict[str, list[tuple[Candidate, str]]] = {}
    for c in official:
        for topic, value in c.claims:
            topics.setdefault(topic, []).append((c, value))
    for topic, items in topics.items():
        items = [(c, v) for c, v in items if c.id not in dropped]
        docs = {c.doc_id for c, _ in items}
        values = {v for _, v in items}
        if len(docs) < 2 or len(values) < 2:
            continue
        best = min(c.authority_level for c, _ in items)
        for c, v in items:
            if c.authority_level > best:
                dropped.add(c.id)
                win = next(w for w, _ in items if w.authority_level == best)
                res.excluded.append({**c.brief(), "step": 3, "reason": f"lower authority than {win.doc_id} on {topic}"})
                res.steps.append(
                    f"Authority (step 3): {c.doc_id} (level {c.authority_level}) says {topic}={v}, but "
                    f"{win.doc_id} (level {win.authority_level}) prevails over it.")
        items = [(c, v) for c, v in items if c.id not in dropped]
        if len({v for _, v in items}) < 2 or len({c.doc_id for c, _ in items}) < 2:
            continue
        newest = max(c.effective_from for c, _ in items)
        for c, v in items:
            if c.effective_from < newest:
                dropped.add(c.id)
                win = next(w for w, _ in items if w.effective_from == newest)
                res.excluded.append({**c.brief(), "step": 4, "reason": f"older than {win.doc_id} at the same authority"})
                res.steps.append(
                    f"Recency (step 4): {win.doc_id} (effective {win.effective_from}) is later than {c.doc_id} "
                    f"(effective {c.effective_from}) at the same authority level, so it prevails.")
        items = [(c, v) for c, v in items if c.id not in dropped]
        if len({v for _, v in items}) >= 2 and len({c.doc_id for c, _ in items}) >= 2:
            res.conflicts.append({"topic": topic,
                                  "sources": [{**c.brief(), "value": v} for c, v in items],
                                  "reason": "same authority level and effective date but different values; "
                                            "the precedence policy cannot resolve this"})
            res.steps.append(f"Unresolved (step 5): sources at the same authority and date disagree on {topic}; "
                             f"both are reported and the student should contact the issuing office.")
    res.winners = [c for c in official if c.id not in dropped]
    return res
