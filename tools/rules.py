"""Rule registry resolution. Thresholds come from SQLite rule_registry (never from constants in code), and
every rule traces to a clause of a Source Register document. Annex A precedence picks between rules."""
from __future__ import annotations

from typing import Any

from app import registry
from rag.precedence import Candidate, resolve

PCT_PARAMS = {"min_attendance_pct", "pass_marks_pct"}


def compare(op: str, actual: float, value: str) -> bool:
    v = float(value)
    return {">=": actual >= v, "<=": actual <= v, ">": actual > v, "<": actual < v, "==": actual == v}[op]


def display(rule: dict[str, Any]) -> str:
    if rule["operator"] == "in":
        return f"in [{rule['value']}]"
    unit = "%" if rule["parameter"] in PCT_PARAMS else ""
    return f"{rule['operator']}{rule['value']}{unit}"


def resolve_rule(parameter: str, as_of: str, programme: str | None, batch: int | None) -> dict[str, Any]:
    rows = registry.rules_for(parameter)
    if not rows:
        return {"status": "no_rule", "parameter": parameter, "upcoming": [],
                "precedence": f"No rule for {parameter} is registered."}
    cands = [Candidate(id=r["rule_id"], doc_id=r["source_doc_id"], title=r.get("doc_title") or r["source_doc_id"],
                       authority_level=r.get("authority_level") or 3, effective_from=r["effective_from"],
                       effective_to=r.get("effective_to"), supersedes=r.get("doc_supersedes") or "",
                       scope_programmes=r["scope_programmes"], scope_batches=r["scope_batches"],
                       section=r["source_section"], version=r.get("doc_version") or "",
                       claims=[(parameter, r["value"])], payload=r) for r in rows]
    res = resolve(cands, as_of, programme, batch)
    # Only official circulars/notices (level <= 3) are announced as upcoming changes; FAQs and handbooks
    # cannot change a rule on their own.
    upcoming = [{"rule_id": c.id, "value": display(c.payload), "source_doc_id": c.doc_id,
                 "effective_from": c.effective_from} for c in res.upcoming if c.authority_level <= 3]
    out: dict[str, Any] = {"parameter": parameter, "precedence": res.decision, "upcoming": upcoming,
                           "notes": res.notes, "excluded": res.excluded}
    if res.conflicts:
        out.update(status="conflict", conflicts=res.conflicts)
        return out
    if not res.winners:
        out.update(status="no_rule", precedence=res.decision + f" No registered {parameter} rule applies on {as_of}.")
        return out
    # Several agreeing winners: cite the most authoritative, then the latest.
    w = sorted(res.winners, key=lambda c: (c.authority_level, tuple(-ord(ch) for ch in c.effective_from)))[0]
    r = w.payload
    out.update(status="ok", rule={"rule_id": r["rule_id"], "parameter": parameter, "operator": r["operator"],
                                  "value": r["value"], "display": display(r), "source_doc_id": r["source_doc_id"],
                                  "section": r["source_section"], "version": w.version, "title": w.title,
                                  "effective_from": r["effective_from"], "authority_level": w.authority_level})
    return out
