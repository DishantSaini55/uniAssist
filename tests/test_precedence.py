"""Annex A source precedence policy, including the worked example (A.3)."""
from rag.precedence import Candidate, batch_in_scope, programme_in_scope, resolve


def cand(id, doc, level, eff, topic="min_attendance_pct", value="75", section="7.2", supersedes="", **kw):
    return Candidate(id=id, doc_id=doc, title=doc, authority_level=level, effective_from=eff, supersedes=supersedes,
                     section=section, claims=[(topic, value)], **kw)


def annex_a3():
    return [
        cand("reg", "ACAD-REG-2024", 1, "2024-07-01", value="75"),
        cand("cir", "ACAD-2026-08", 2, "2026-08-01", value="80", section="1", supersedes="ACAD-REG-2024#7.2"),
        cand("faq", "DEPT-FAQ", 4, "2026-09-15", value="65", section="3"),
    ]


def test_annex_a3_worked_example():
    r = resolve(annex_a3(), "2026-10-06")
    assert [c.doc_id for c in r.winners] == ["ACAD-2026-08"]
    steps = r.decision
    assert "supersedes ACAD-REG-2024 clause 7.2" in steps and "step 2" in steps
    assert "step 3" in steps  # FAQ is lower authority
    assert not r.conflicts


def test_before_circular_regulation_applies_and_circular_is_upcoming():
    r = resolve(annex_a3(), "2026-07-01")
    assert [c.doc_id for c in r.winners] == ["ACAD-REG-2024"]
    assert {c.doc_id for c in r.upcoming} == {"ACAD-2026-08"}  # FAQ is level 4: not announced as an upcoming change


def test_not_yet_effective_document_is_never_current():
    r = resolve([cand("a", "A", 1, "2030-01-01", value="99")], "2026-10-06")
    assert r.winners == [] and len(r.upcoming) == 1


def test_expired_document_excluded():
    c = cand("a", "A", 1, "2020-01-01", value="70")
    c.effective_to = "2025-12-31"
    assert resolve([c], "2026-10-06").winners == []


def test_authority_beats_recency():
    r = resolve([cand("hi", "REG", 1, "2020-01-01", value="75"), cand("lo", "NOTICE", 3, "2026-01-01", value="60")],
                "2026-10-06")
    assert [c.doc_id for c in r.winners] == ["REG"]


def test_recency_between_equal_authority():
    r = resolve([cand("old", "C1", 2, "2025-01-01", value="75"), cand("new", "C2", 2, "2026-01-01", value="80")],
                "2026-10-06")
    assert [c.doc_id for c in r.winners] == ["C2"] and "step 4" in r.decision


def test_unresolved_conflict_is_flagged_not_chosen():
    r = resolve([cand("a", "N1", 3, "2026-09-01", value="3"), cand("b", "N2", 3, "2026-09-01", value="5")], "2026-10-06")
    assert len(r.conflicts) == 1 and len(r.conflicts[0]["sources"]) == 2 and "step 5" in r.decision


def test_supersession_requires_level_1_or_2():
    docs = [cand("reg", "REG", 1, "2024-01-01", value="75"),
            cand("note", "NOTE", 3, "2026-01-01", value="60", section="1", supersedes="REG#7.2")]
    r = resolve(docs, "2026-10-06")
    assert [c.doc_id for c in r.winners] == ["REG"]  # a department notice cannot supersede a regulation


def test_level5_is_informational_only():
    r = resolve([cand("u", "FORUM", 5, "2026-09-01", value="50")], "2026-10-06")
    assert r.winners == [] and [c.doc_id for c in r.informational] == ["FORUM"]


def test_complementary_passages_all_kept():
    a = Candidate(id="a", doc_id="REG", title="REG", authority_level=1, effective_from="2024-01-01", section="1")
    b = Candidate(id="b", doc_id="FAQ", title="FAQ", authority_level=4, effective_from="2026-01-01", section="2")
    assert {c.id for c in resolve([a, b], "2026-10-06").winners} == {"a", "b"}


def test_scope_filters_by_batch_and_programme():
    c = cand("p", "PLC", 2, "2026-07-01", topic="placement_min_cgpa", value="6.5",
             scope_programmes="B.Tech", scope_batches="2023")
    assert resolve([c], "2026-10-06", "B.Tech CSE", 2023).winners
    assert not resolve([c], "2026-10-06", "B.Tech CSE", 2024).winners
    assert not resolve([c], "2026-10-06", "M.Tech", 2023).winners


def test_scope_parsers():
    assert batch_in_scope("2023+", 2025) and not batch_in_scope("2023+", 2022)
    assert batch_in_scope("2021-2023", 2022) and batch_in_scope("ALL", 1999) and batch_in_scope("2023;2024", 2024)
    assert programme_in_scope("B.Tech", "B.Tech CSE") and not programme_in_scope("B.Tech ECE", "B.Tech CSE")
    assert programme_in_scope("ALL", "anything")
