"""Answer composition. Every number and decision in the answer comes from a tool result or a retrieved
passage; templates (and optionally the LLM, validated) only put it into words."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from rag.claims import blocks, sentences
from rag.retriever import RetrievalResult, content_terms

NOT_FOUND = "I could not find this information in the authorised university sources."
PageFn = Callable[[str, str], int | None]


@dataclass
class Composed:
    answer: str
    answer_type: str
    explanation: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    applied_rules: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    upcoming_changes: list[dict[str, Any]] = field(default_factory=list)
    precedence: str = ""
    facts: str = ""            # what the LLM may use to word the explanation


# --------------------------------------------------------------------------- citations
def _cite(doc: dict[str, Any], section: str, page: int | None) -> dict[str, Any]:
    return {"doc_id": doc["doc_id"], "title": doc["title"], "section": section, "page": page,
            "version": doc["version"], "effective_from": doc["effective_from"],
            "authority_level": doc.get("authority_level")}


def rule_citation(rule: dict[str, Any], page_fn: PageFn) -> dict[str, Any]:
    return _cite({"doc_id": rule["source_doc_id"], "title": rule.get("title") or rule["source_doc_id"],
                  "version": rule.get("version", ""), "effective_from": rule["effective_from"],
                  "authority_level": rule.get("authority_level")}, rule["section"], page_fn(rule["source_doc_id"], rule["section"]))


def applied(rule: dict[str, Any]) -> dict[str, Any]:
    return {"rule_id": rule["rule_id"], "value": rule["display"], "source_doc_id": rule["source_doc_id"],
            "section": rule["section"], "version": rule.get("version"), "effective_from": rule["effective_from"]}


def _unique(cites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for c in cites:
        k = (c["doc_id"], c["section"])
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _clause(rule: dict[str, Any]) -> str:
    return f"clause {rule['section']} of {rule.get('title') or rule['source_doc_id']} ({rule['source_doc_id']} v{rule.get('version', '?')})"


def _precedence_sentence(p: str | None) -> str:
    p = (p or "").strip()
    return "" if not p or p.startswith("No precedence conflict") else p


# ------------------------------------------------------------------------- tool answers
def _status_failure(name: str, out: dict[str, Any], page_fn: PageFn) -> Composed | None:
    res = out.get("result")
    if res == "NO_RECORD":
        return Composed(f"{NOT_FOUND} ({out.get('reason', 'no matching record')}.)", "not_found",
                        explanation="No matching record exists for the logged-in student.")
    if res == "NO_RULE":
        return Composed(f"{NOT_FOUND} No registered rule applies to your programme and batch on this date.", "not_found",
                        explanation=_precedence_sentence(out.get("precedence")) or "No applicable rule is registered.",
                        precedence=out.get("precedence", ""))
    if res == "CONFLICT":
        confl = out.get("conflicts", [])
        srcs = [s for c in confl for s in c["sources"]]
        parts = "; ".join(f"{s['doc_id']} (clause {s['section']}, level {s['authority_level']}) says {s['value']}" for s in srcs)
        cites = [rule_citation({"source_doc_id": s["doc_id"], "section": s["section"], "effective_from": s["effective_from"],
                                "authority_level": s["authority_level"]}, page_fn) for s in srcs]
        return Composed("The authorised sources conflict and the precedence policy cannot resolve it, so I will not pick one. "
                        f"{parts}. Please contact the issuing office to confirm.", "conflict_flagged",
                        explanation="Same authority level and effective date with different values (Annex A step 5).",
                        citations=_unique(cites), conflicts=confl, precedence=out.get("precedence", ""))
    return None


def _rule_lines(rules: list[dict[str, Any]], page_fn: PageFn) -> tuple[list[dict], list[dict]]:
    return [applied(r) for r in rules], _unique([rule_citation(r, page_fn) for r in rules])


def _upcoming(*outs: dict[str, Any]) -> list[dict[str, Any]]:
    seen, res = set(), []
    for o in outs:
        for u in o.get("upcoming", []):
            if u["rule_id"] not in seen:
                seen.add(u["rule_id"])
                res.append(u)
    return res


def _upcoming_text(up: list[dict[str, Any]]) -> str:
    return "".join(f" Upcoming change: {u['source_doc_id']} will set {u['value']} from {u['effective_from']}." for u in up)


def compose_tools(question: str, log: list[dict[str, Any]], page_fn: PageFn) -> Composed:
    calls = [(c["tool"], c["output"]) for c in log]
    name, out = calls[0]
    if log[0]["status"] == "error":
        return Composed("The record lookup failed, so I cannot answer this reliably. Please try again.", "not_found",
                        explanation="A tool returned an error.")

    # --- plain record lookups --------------------------------------------------------------
    if name == "get_student_profile":
        if not out.get("found"):
            return Composed(NOT_FOUND, "not_found")
        q = question.lower()
        if "cgpa" in q or "gpa" in q:
            a = f"Your CGPA is {out['cgpa']:.2f}."
        elif "backlog" in q:
            a = f"You have {out['active_backlogs']} active backlog(s)."
        elif "semester" in q:
            a = f"You are in semester {out['current_semester']}."
        else:
            a = (f"{out['full_name']} ({out['student_id']}): {out['programme']}, {out['batch_year']} batch, semester "
                 f"{out['current_semester']}, CGPA {out['cgpa']:.2f}, {out['active_backlogs']} active backlog(s).")
        return Composed(a, "calculated", "Read from your student record.", facts=json.dumps(out))
    if name == "get_attendance":
        recs = out["records"]
        if not recs:
            return Composed(f"{NOT_FOUND} (no attendance record{' for that course' if log[0]['input'].get('course_code') else ''}.)", "not_found")
        a = " ".join(f"Your attendance in {r['course_name']} ({r['course_code']}) is {r['attendance_pct']}% "
                     f"({r['classes_attended']} of {r['classes_held']} classes)." for r in recs)
        return Composed(a, "calculated", "Attendance % is computed from classes attended and held, never stored.",
                        facts=json.dumps(recs))
    if name == "get_results":
        recs = out["results"]
        if not recs:
            return Composed(f"{NOT_FOUND} (no result record.)", "not_found")
        a = "; ".join(f"{r['course_name']} ({r['course_code']}): {r['total_marks']}/{r['max_marks']} "
                      f"(internal {r['internal_marks']} + external {r['external_marks']}), {r['result']} - "
                      f"{r['exam_type'].lower()} {r['exam_session']}" for r in recs)
        return Composed(a + ".", "calculated", "Read from your results record.", facts=json.dumps(recs))
    if name == "get_backlogs":
        n = out["active_backlogs"]
        oc = out["outstanding_courses"]
        a = f"You have {n} active backlog(s)." + ("" if not oc else " Outstanding: " + "; ".join(
            f"{c['course_name']} ({c['course_code']}, {c['last_result']}, {c['total_marks']}/{c['max_marks']})" for c in oc) + ".")
        return Composed(a, "calculated", "Active backlogs are courses whose latest attempt is not a pass.",
                        facts=json.dumps(out))

    # --- rule-based decisions -----------------------------------------------------------------
    for _, o in calls:
        fail = _status_failure(name, o, page_fn)
        if fail:
            return fail

    if name == "check_exam_eligibility":
        rules = [o["rule"] for _, o in calls if o.get("rule")]
        ar, cites = _rule_lines(rules[:1], page_fn)
        up = _upcoming(*[o for _, o in calls])
        if len(calls) == 1:
            o = out
            r = o["rule"]
            if o["result"] == "ELIGIBLE":
                a = f"You are eligible to appear in the end-semester examination for {o['course_name']} ({o['course_code']})."
                why = (f"Your attendance in {o['course_code']} is {o['attendance_pct']}% ({o['classes_attended']} of "
                       f"{o['classes_held']} classes), which meets the {r['display']} minimum in {_clause(r)}.")
            else:
                a = (f"You are not eligible to appear in the end-semester examination for {o['course_name']} "
                     f"({o['course_code']}).")
                why = (f"Your attendance in {o['course_code']} is {o['attendance_pct']}% ({o['classes_attended']} of "
                       f"{o['classes_held']} classes), below the {r['display']} minimum in {_clause(r)}; you are "
                       f"{o['classes_short']} class(es) short at the current number of classes held.")
        else:
            bad = [o for _, o in calls if o["result"] == "NOT_ELIGIBLE"]
            r = rules[0]
            if bad:
                a = "You are not eligible to appear in: " + "; ".join(
                    f"{o['course_name']} ({o['course_code']}, {o['attendance_pct']}%)" for o in bad) + "."
            else:
                a = f"You meet the attendance requirement in all {len(calls)} of your courses."
            why = (f"The attendance minimum is {r['display']} ({_clause(r)}). " + "; ".join(
                f"{o['course_code']} {o['attendance_pct']}% -> {o['result']}" for _, o in calls) + ".")
        prec = _precedence_sentence(out.get("precedence"))
        full = (a + _upcoming_text(up)).strip()
        return Composed(full, "calculated", (why + " " + prec).strip(), cites, ar, [], [], up, prec,
                        facts=json.dumps({"decision": a, "reason": why, "precedence": prec,
                                          "tool_results": [o for _, o in calls]}, default=str))

    if name == "check_supplementary_eligibility":
        r = out["rule"]
        ar, cites = _rule_lines([r], page_fn)
        elig = out["result"] == "ELIGIBLE"
        a = (f"You are {'eligible' if elig else 'not eligible'} for the supplementary examination in "
             f"{out['course_code']}.")
        why = f"{out['reason'][0].upper()}{out['reason'][1:]} ({_clause(r)}, rule {r['rule_id']})."
        prec = _precedence_sentence(out.get("precedence"))
        return Composed(a, "calculated", (why + " " + prec).strip(), cites, ar, [], [], _upcoming(out), prec,
                        facts=json.dumps({"decision": a, "reason": why, "tool_result": out}, default=str))

    if name == "check_placement_eligibility":
        return _placement(out, page_fn, prefix="")

    if name == "what_if_pass_supplementary":
        if out["result"] == "NOT_APPLICABLE":
            return Composed(f"This what-if does not apply: {out['reason']}.", "calculated",
                            explanation=out["reason"], facts=json.dumps(out, default=str))
        c = _placement(out["placement"], page_fn,
                       prefix=f"If you pass the supplementary in {out['course_code']}: ")
        c.assumptions = out["assumptions"]
        c.explanation = (c.explanation + " Assumptions: " + " ".join(out["assumptions"])).strip()
        c.facts = json.dumps({"decision": c.answer, "reason": c.explanation, "tool_result": out}, default=str)
        return c

    if name == "what_if_attendance":
        r = out["rule"]
        ar, cites = _rule_lines([r], page_fn)
        elig = out["result"] == "ELIGIBLE"
        a = (f"If you attend those classes, your attendance in {out['course_code']} would be {out['hypothetical_pct']}% "
             f"({out['hypothetical_attended']} of {out['hypothetical_held']}), so you would "
             f"{'meet' if elig else 'still not meet'} the {r['display']} minimum.")
        why = f"Current attendance {out['current_pct']}%. Rule: {_clause(r)}. Assumption: {out['assumptions'][0]}"
        return Composed(a, "calculated", why, cites, ar, [], out["assumptions"], _upcoming(out),
                        _precedence_sentence(out.get("precedence")), facts=json.dumps(out, default=str))

    return Composed(NOT_FOUND, "not_found")


def _placement(out: dict[str, Any], page_fn: PageFn, prefix: str) -> Composed:
    fail = _status_failure("placement", out, page_fn)
    if fail:
        return fail
    ar, cites = _rule_lines(out["rules"], page_fn)
    elig = out["result"] == "ELIGIBLE"
    lines = []
    for c in out["checks"]:
        lines.append(f"{c['criterion']} {c['actual']} {'meets' if c['passed'] else 'does not meet'} {c['required']}"
                     f" (rule {c['rule_id']})")
    if prefix:
        a = f"{prefix}you would be eligible for campus placement." if elig else             f"{prefix}you would not be eligible for campus placement."
    else:
        a = "You are eligible for campus placement." if elig else "You are not eligible for campus placement."
    rules = out["rules"]
    why = "; ".join(lines) + ("." if lines else "") + (f" Source: {_clause(rules[0])}." if rules else "")
    up = _upcoming(out)
    prec = _precedence_sentence(out.get("precedence"))
    return Composed(a + _upcoming_text(up), "calculated", (why + " " + prec).strip(), cites, ar, [], [], up, prec,
                    facts=json.dumps({"decision": a, "reason": why, "tool_result": out}, default=str))


# ---------------------------------------------------------------------- retrieval answers
def _passage(question: str, text: str, procedure: bool) -> str:
    if "[TABLE]" in text:
        head, _, rest = text.partition("[TABLE]")
        rows, _, _ = rest.partition("[/TABLE]")
        lead = sentences(head)
        return (lead[-1] + "\n" if lead else "") + rows.strip()
    items = blocks(text)
    steps = [b for b in items if re.match(r"\d+[.)]\s+\S", b) and not re.match(r"\d+\.\d", b)]
    if procedure and len(steps) >= 2:
        intro = next((b for b in items if b.rstrip().endswith(":")), "")
        intro = re.sub(r"^\d+(?:\.\d+)*\s+[A-Z][\w ]*\.\s*", "", intro)
        return (intro + "\n" if intro else "") + "\n".join(steps)
    terms = {t[:4] for t in content_terms(question)}
    sents = [s for s in sentences(text) if len(s) > 25 or s.endswith(".")]
    sents = [s for s in sents if not re.fullmatch(r"\d+(?:\.\d+)*\s+[A-Za-z][A-Za-z ,&()/-]{0,70}", s)]
    scored = [(sum(1 for w in re.findall(r"[a-z\-]{3,}", s.lower()) if w[:4] in terms), i, s) for i, s in enumerate(sents)]
    best = sorted([x for x in scored if x[0] > 0], key=lambda x: (-x[0], x[1]))[:2]
    chosen = [s for _, _, s in sorted(best, key=lambda x: x[1])] or sents[:2]
    out = " ".join(chosen)
    return re.sub(r"^\d+(?:\.\d+)*\s+", "", out)


def compose_retrieval(question: str, rr: RetrievalResult, page_fn: PageFn, category: str, docs: dict) -> Composed:
    res = rr.resolution
    prec = _precedence_sentence(res.decision)
    up = [{"doc_id": c["doc_id"], "section": c["section"], "effective_from": c["effective_from"],
           "title": c["title"]} for c in rr.upcoming]
    if res.conflicts:
        srcs = [s for c in res.conflicts for s in c["sources"]]
        by_id = {c["chunk_id"]: c for c in rr.raw_topk + rr.chunks}
        # Look up each conflicting source's own passage for the user-facing statement.
        parts, cites = [], []
        for s in srcs:
            chunk = next((c for c in by_id.values() if c["doc_id"] == s["doc_id"] and c["section"] == s["section"]), None)
            parts.append(f"{s['doc_id']} (section {s['section']}) states: \"{_passage(question, chunk['text'], False) if chunk else s['value']}\"")
            if chunk:
                cites.append(_cite({**docs[s["doc_id"]], "doc_id": s["doc_id"]}, s["section"], chunk["page"]))
        return Composed(answer="The authorised sources conflict and the precedence policy cannot resolve it, so I will "
                        "not pick one. " + " ".join(parts) + " Please contact the issuing office to confirm.",
                        answer_type="conflict_flagged",
                        explanation="Equal authority level and effective date with different values (Annex A step 5).",
                        citations=_unique(cites), conflicts=res.conflicts, upcoming_changes=up, precedence=prec)
    if not rr.sufficient:
        extra = ""
        if rr.informational and not rr.chunks:
            extra = " Only unofficial (level 5) material matched, which cannot be treated as authoritative."
        return Composed(NOT_FOUND + extra, "not_found",
                        explanation="No retrieved passage was relevant enough to support an answer.",
                        upcoming_changes=up, precedence=prec)
    top = (rr.chunks or rr.informational)[0]
    picked = [top]
    for c in (rr.chunks or [])[1:]:
        if len(picked) < 2 and c["doc_id"] != top["doc_id"] and c["score"] >= top["score"] - 0.06:
            picked.append(c)
    body = []
    cites = []
    for c in picked:
        d = docs[c["doc_id"]]
        text = _passage(question, c["text"], category == "procedure")
        body.append(f"{text}\n(Source: {d['title']}, section {c['section']}, page {c['page']}, version {d['version']}, "
                    f"effective {d['effective_from']}.)")
        cites.append(_cite(d, c["section"], c["page"]))
    answer = "\n\n".join(body)
    if top["authority_level"] >= 5:
        answer = "Unofficial source only (informational, not authoritative): " + answer
    if rr.gap_terms and rr.coverage < 0.67:
        answer += (f"\n\nPartial evidence: the sources do not mention {', '.join(rr.gap_terms)}; "
                   "I cannot confirm anything about that.")
    for u in rr.upcoming[:2]:
        answer += (f"\n\nUpcoming change (not yet in force): {u['doc_id']} takes effect on {u['effective_from']}: "
                   f"\"{_passage(question, u['text'], False)}\"")
    d = docs[top["doc_id"]]
    why = (f"Answered directly from {d['title']} (authority level {d['authority_level']}, {d['doc_type']}). "
           + (prec if prec else "No conflicting sources applied.")).strip()
    return Composed(answer, "retrieved_fact", why, _unique(cites), [], [], [], up, prec,
                    facts="\n".join(f"<document id=\"{c['doc_id']}\" section=\"{c['section']}\">{c['text']}</document>"
                                    for c in picked) + f"\nPrecedence: {prec or 'none'}")
