"""Retrieval: vector search -> hydrate with Source Register metadata -> Annex A precedence -> abstention check."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app import registry
from app.config import Settings, settings as default_settings
from rag.claims import extract_claims
from rag.precedence import Candidate, Resolution, resolve
from rag.vectorstore import VectorStore

STOP = set("""what which when where does have that this with from about tell need many much will would should could
there their your know please student students university nsut rule rules policy policies regulation regulations
the and for are was were how can who why any into than then them they been being also more most some such only
over under after before between during each other required require appear needed allowed applicable exams exam
give list explain describe whats okay percent percentage enough sufficient""".split())


def _stem(w: str) -> str:
    return w[:4]


def content_terms(question: str) -> list[str]:
    words = re.findall(r"[a-z][a-z\-]{3,}", question.lower())
    return [w for w in dict.fromkeys(words) if w not in STOP]


@dataclass
class RetrievalResult:
    raw_topk: list[dict[str, Any]] = field(default_factory=list)      # before precedence (hit-rate metric)
    chunks: list[dict[str, Any]] = field(default_factory=list)         # after precedence, best first
    resolution: Resolution = field(default_factory=Resolution)
    best_score: float = 0.0
    coverage: float = 0.0
    unmatched_terms: list[str] = field(default_factory=list)
    sufficient: bool = False
    upcoming: list[dict[str, Any]] = field(default_factory=list)
    informational: list[dict[str, Any]] = field(default_factory=list)
    gap_terms: list[str] = field(default_factory=list)   # question terms that appear nowhere in the whole corpus


class Retriever:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self._store: VectorStore | None = None
        self._stems: tuple[int, set[str]] = (-1, set())

    def _corpus_stems(self) -> set[str]:
        n = self.store.count()
        if self._stems[0] != n:
            docs = self.store.collection.get(include=["documents"])["documents"]
            self._stems = (n, {_stem(w) for d in docs for w in re.findall(r"[a-z][a-z\-]{2,}", d.lower())})
        return self._stems[1]

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = VectorStore(self.cfg)
        return self._store

    def retrieve(self, question: str, as_of: str, programme: str | None = None, batch: int | None = None,
                 k: int | None = None) -> RetrievalResult:
        k = k or self.cfg.top_k
        raw = self.store.query(question, max(k * 3, 15))
        docs = registry.sources_by_id()
        hydrated: list[dict[str, Any]] = []
        for h in raw:
            d = docs.get(h["doc_id"])
            if not d:
                continue  # chunk of a document that is no longer registered
            hydrated.append({**h, "title": d["title"], "version": d["version"], "authority_level": d["authority_level"],
                             "effective_from": d["effective_from"], "doc_type": d["doc_type"]})
        result = RetrievalResult(raw_topk=hydrated[:k])
        if not hydrated:
            return result

        cands = []
        by_id = {}
        # Drop passages far less relevant than the best hit so precedence/upcoming notes stay on topic.
        floor = max(self.cfg.min_similarity - 0.1, hydrated[0]["score"] - 0.2)
        for h in [x for x in hydrated if x["score"] >= floor]:
            d = docs[h["doc_id"]]
            claims = [c.key() for c in extract_claims(h["text"])]
            cand = Candidate(id=h["chunk_id"], doc_id=h["doc_id"], title=d["title"], authority_level=d["authority_level"],
                             effective_from=d["effective_from"], effective_to=d.get("effective_to"),
                             supersedes=d.get("supersedes") or "", scope_programmes=d["scope_programmes"],
                             scope_batches=d["scope_batches"], section=h["section"], version=d["version"],
                             score=h["score"], claims=claims, payload=h)
            cands.append(cand)
            by_id[cand.id] = h
        # Precedence must compare *every* source that makes a claim on a topic, not only those that happen to
        # score well for this phrasing (a FAQ may match "65%" far better than the circular that overrides it).
        have = {(c.doc_id, c.section) for c in cands}
        for topic in {t for c in list(cands) for t, _ in c.claims}:
            src = max((c.score for c in cands if any(t == topic for t, _ in c.claims)), default=0.0)
            for rule in registry.rules_for(topic):
                key = (rule["source_doc_id"], rule["source_section"])
                d = docs.get(rule["source_doc_id"])
                if key in have or not d:
                    continue
                chunk = self.store.get_chunk(*key)
                if not chunk:
                    continue
                have.add(key)
                h = {**chunk, "score": src, "title": d["title"], "version": d["version"],
                     "authority_level": d["authority_level"], "effective_from": d["effective_from"],
                     "doc_type": d["doc_type"]}
                cand = Candidate(id=h["chunk_id"], doc_id=h["doc_id"], title=d["title"],
                                 authority_level=d["authority_level"], effective_from=d["effective_from"],
                                 effective_to=d.get("effective_to"), supersedes=d.get("supersedes") or "",
                                 scope_programmes=d["scope_programmes"], scope_batches=d["scope_batches"],
                                 section=h["section"], version=d["version"], score=src,
                                 claims=[c.key() for c in extract_claims(h["text"])], payload=h)
                cands.append(cand)
                by_id[cand.id] = h
        res = resolve(cands, as_of, programme, batch)
        # A conflict only matters if a conflicting passage is genuinely among the best answers.
        ranked = sorted(cands, key=lambda c: -c.score)[:3]
        top_ids = {c.id for c in ranked if c.score >= self.cfg.min_similarity}
        keep, dropped = [], set()
        for cf in res.conflicts:
            ids = {c.id for c in cands if (c.doc_id, c.section) in {(s["doc_id"], s["section"]) for s in cf["sources"]}}
            if ids & top_ids:
                keep.append(cf)
            else:
                dropped |= ids
        res.conflicts = keep
        res.winners = [c for c in res.winners if c.id not in dropped]
        result.resolution = res
        winners = sorted(res.winners, key=lambda c: (-c.score, c.authority_level))[:k]
        result.chunks = [by_id[c.id] for c in winners]
        seen: set[str] = set()
        for c in res.upcoming:
            if c.authority_level <= 3 and c.doc_id not in seen:
                seen.add(c.doc_id)
                result.upcoming.append(by_id[c.id])
        result.informational = [by_id[c.id] for c in res.informational][:3]
        pool = result.chunks or result.informational
        result.best_score = max([c["score"] for c in pool] or [0.0])

        terms = content_terms(question)
        text = " ".join(c["text"] for c in pool[:3]).lower()
        body = {_stem(w) for w in re.findall(r"[a-z][a-z\-]{2,}", text)}
        matched = [t for t in terms if _stem(t) in body]
        result.unmatched_terms = [t for t in terms if _stem(t) not in body]
        result.coverage = len(matched) / len(terms) if terms else 1.0
        corpus = self._corpus_stems()
        result.gap_terms = [t for t in result.unmatched_terms if _stem(t) not in corpus]
        result.sufficient = bool(pool) and result.best_score >= self.cfg.min_similarity and (
            result.coverage >= self.cfg.min_term_coverage
            or (result.best_score >= self.cfg.strong_similarity and result.coverage > 0))
        return result
