"""Live document ingestion (R11): parse -> sanitise -> chunk -> embed -> register -> extract rules.

Works while the API is running; the very next /ask sees the new document. No restart, no code change.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app import registry
from app.config import Settings, settings as default_settings
from rag.claims import extract_claims
from rag.documents import extract_pages, make_chunks, neutralise_instructions
from rag.vectorstore import VectorStore

DOC_TYPES = ("regulation", "circular", "notice", "faq", "handbook", "unofficial")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RuleIn(BaseModel):
    """Optional explicit rule supplied by an administrator alongside a document."""
    parameter: str
    operator: str
    value: str
    section: str
    description: str = ""


class DocMeta(BaseModel):
    """Annex B Source Register fields (also the metadata JSON for POST /ingest)."""
    doc_id: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
    title: str = Field(min_length=2)
    issuer: str = Field(min_length=2)
    authority_level: int = Field(ge=1, le=5)
    doc_type: Literal["regulation", "circular", "notice", "faq", "handbook", "unofficial"]
    version: str = "1.0"
    effective_from: str
    effective_to: str | None = None
    supersedes: str | None = None
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    provenance: str = ""
    retrieved_on: str = Field(default_factory=lambda: date.today().isoformat())
    synthetic: Literal["Y", "N"] = "N"
    rules: list[RuleIn] = []

    @field_validator("effective_from", "retrieved_on")
    @classmethod
    def _date_required(cls, v: str) -> str:
        if not _DATE.match(v):
            raise ValueError("must be YYYY-MM-DD")
        date.fromisoformat(v)
        return v

    @field_validator("effective_to", "supersedes", "provenance", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return None if v is None or (isinstance(v, str) and not v.strip()) else v

    @field_validator("effective_to")
    @classmethod
    def _date_optional(cls, v: str | None) -> str | None:
        if v is not None:
            if not _DATE.match(v):
                raise ValueError("must be YYYY-MM-DD or empty")
            date.fromisoformat(v)
        return v

    @field_validator("authority_level", mode="before")
    @classmethod
    def _int(cls, v):
        return int(v)

    @field_validator("doc_type", "synthetic", mode="before")
    @classmethod
    def _normalise_enum(cls, v, info):
        # Be liberal in what the Source Register may contain: "Circular", " FAQ ", "y".
        if isinstance(v, str):
            v = v.strip()
            return v.upper() if info.field_name == "synthetic" else v.lower()
        return v


def _rule_id(doc_id: str, parameter: str, section: str) -> str:
    return f"R-{doc_id}-{parameter}-{re.sub(r'[^A-Za-z0-9.]+', '_', section)}"


class IngestionService:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self._store: VectorStore | None = None

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = VectorStore(self.cfg)
        return self._store

    def ingest(self, data: bytes, filename: str, meta: DocMeta) -> dict[str, Any]:
        if not data:
            raise ValueError("The uploaded file is empty.")
        digest = hashlib.sha256(data).hexdigest()
        existing = registry.get_source(meta.doc_id)
        if existing and existing.get("content_hash") == digest:
            return {"doc_id": meta.doc_id, "chunks_indexed": existing.get("chunks_indexed") or 0,
                    "status": "unchanged", "rules_extracted": 0, "warnings": []}

        pages = extract_pages(data, filename)
        warnings: list[str] = []
        flags: list[str] = []
        for p in pages:
            p.text, f = neutralise_instructions(p.text)
            flags += f
        if flags:
            warnings.append(f"{len(flags)} instruction-like sentence(s) were removed from the indexed text "
                            "(documents are data, not instructions).")
        text_len = sum(len(p.text.strip()) for p in pages)
        if text_len < 15:
            raise ValueError("No extractable text. The file may be a scan and OCR (tesseract) is not available.")
        ocr_pages = sum(1 for p in pages if p.ocr)
        if ocr_pages:
            warnings.append(f"{ocr_pages} page(s) had no text layer and were read with OCR; verify before relying on them.")

        chunks = make_chunks(meta.title, pages, self.cfg.chunking, self.cfg.chunk_size, self.cfg.chunk_overlap)
        if not chunks:
            raise ValueError("The document produced no indexable text.")
        for ref in (meta.supersedes or "").replace(",", ";").split(";"):
            target = ref.strip().split("#")[0]
            if target and not registry.get_source(target):
                warnings.append(f"supersedes '{target}' is not (yet) in the Source Register; the link takes "
                                "effect once that document is ingested.")

        # Index first, register second: a failed embedding run must never leave a half-registered document.
        self.store.delete_doc(meta.doc_id)
        n = self.store.add_chunks(meta.doc_id, chunks)
        m = meta.model_dump()
        registry.upsert_source(m, content_hash=digest, filename=filename, chunks=n, pages=len(pages),
                               ocr_pages=ocr_pages, flags=flags)
        self.cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.uploads_dir / f"{meta.doc_id}{'.pdf' if data[:5] == b'%PDF-' else '.txt'}").write_bytes(data)

        rules = self._rules(meta, chunks)
        registry.replace_rules_for_doc(meta.doc_id, rules)
        return {"doc_id": meta.doc_id, "chunks_indexed": n, "status": "updated" if existing else "ingested",
                "pages": len(pages), "rules_extracted": len(rules),
                "rules": [{"rule_id": r["rule_id"], "parameter": r["parameter"], "operator": r["operator"],
                           "value": r["value"], "section": r["source_section"]} for r in rules],
                "warnings": warnings}

    def remove(self, doc_id: str) -> bool:
        """Delete a document, its chunks and the rules it contributed (used by tests and admin tooling)."""
        from app import db

        self.store.delete_doc(doc_id)
        conn = db.connect()
        try:
            conn.execute("DELETE FROM rule_registry WHERE source_doc_id=?", (doc_id,))
            n = conn.execute("DELETE FROM source_register WHERE doc_id=?", (doc_id,)).rowcount
            conn.commit()
        finally:
            conn.close()
        return bool(n)

    def _rules(self, meta: DocMeta, chunks) -> list[dict[str, Any]]:
        """Propose rule_registry rows from the document text; administrators can add explicit ones."""
        out: dict[str, dict[str, Any]] = {}
        base = {"scope_programmes": meta.scope_programmes, "scope_batches": meta.scope_batches,
                "effective_from": meta.effective_from, "effective_to": meta.effective_to}
        for ch in chunks:
            for c in extract_claims(ch.text):
                if not c.is_rule:
                    continue
                rid = _rule_id(meta.doc_id, c.topic, ch.section)
                out.setdefault(rid, {**base, "rule_id": rid, "description": c.description, "parameter": c.topic,
                                     "operator": c.operator, "value": c.value, "source_section": ch.section,
                                     "origin": "extracted"})
        for r in meta.rules:
            rid = _rule_id(meta.doc_id, r.parameter, r.section)
            out[rid] = {**base, "rule_id": rid, "description": r.description or r.parameter, "parameter": r.parameter,
                        "operator": r.operator, "value": r.value, "source_section": r.section, "origin": "admin"}
        return list(out.values())
