"""Source Register (Annex B) and rule registry (Annex C) access."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db

SR_COLS = ["doc_id", "title", "issuer", "authority_level", "doc_type", "version", "effective_from",
           "effective_to", "supersedes", "scope_programmes", "scope_batches", "provenance", "retrieved_on", "synthetic"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_sources() -> list[dict[str, Any]]:
    out = db.rows("SELECT * FROM source_register ORDER BY authority_level, doc_id")
    for r in out:
        r["flags"] = json.loads(r.get("flags") or "[]")
    return out


def get_source(doc_id: str) -> dict[str, Any] | None:
    return db.one("SELECT * FROM source_register WHERE doc_id=?", (doc_id,))


def sources_by_id() -> dict[str, dict[str, Any]]:
    return {r["doc_id"]: r for r in db.rows("SELECT * FROM source_register")}


def upsert_source(meta: dict[str, Any], *, content_hash: str, filename: str, chunks: int, pages: int,
                  ocr_pages: int, flags: list[str]) -> None:
    row = {k: meta.get(k) for k in SR_COLS}
    row.update(content_hash=content_hash, filename=filename, ingested_at=now_iso(), chunks_indexed=chunks,
               pages=pages, ocr_pages=ocr_pages, flags=json.dumps(flags))
    cols = list(row)
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT INTO source_register ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT(doc_id) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in cols if c != "doc_id"),
            [row[c] for c in cols])
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------------- rules
def rules_for(parameter: str) -> list[dict[str, Any]]:
    return db.rows(
        "SELECT r.*, s.title AS doc_title, s.authority_level, s.version AS doc_version, s.supersedes AS doc_supersedes, "
        "s.effective_from AS doc_effective_from FROM rule_registry r "
        "LEFT JOIN source_register s ON s.doc_id = r.source_doc_id WHERE r.parameter=?", (parameter,))


def replace_rules_for_doc(doc_id: str, rules: list[dict[str, Any]]) -> None:
    """Re-ingesting a document replaces the rules it previously contributed (admin/seed rules are kept)."""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM rule_registry WHERE source_doc_id=? AND origin IN ('extracted','admin')", (doc_id,))
        for r in rules:
            conn.execute(
                "INSERT OR REPLACE INTO rule_registry (rule_id, description, parameter, operator, value, scope_programmes,"
                " scope_batches, effective_from, effective_to, source_doc_id, source_section, origin, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["rule_id"], r["description"], r["parameter"], r["operator"], str(r["value"]), r["scope_programmes"],
                 r["scope_batches"], r["effective_from"], r.get("effective_to"), doc_id, r["source_section"],
                 r.get("origin", "extracted"), now_iso()))
        conn.commit()
    finally:
        conn.close()
