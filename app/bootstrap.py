"""First-boot setup: schema, synthetic students, corpus ingestion. Every step is idempotent, so restarting the
service never re-embeds documents and never duplicates rows."""
from __future__ import annotations

from typing import Any

from app import db
from app.config import Settings, settings as default_settings


def bootstrap(cfg: Settings | None = None, students: bool = True, corpus: bool = True) -> dict[str, Any]:
    cfg = cfg or default_settings
    cfg.ensure_dirs()
    db.ensure_schema(cfg.sqlite_path)
    out: dict[str, Any] = {}
    if students and db.one("SELECT COUNT(*) AS n FROM students")["n"] == 0:
        from app.loader import load_dir

        out["students"] = load_dir(cfg.data_dir / "students")
    if corpus:
        from ingestion.bootstrap import ingest_corpus

        out["corpus"] = [{k: v for k, v in r.items() if k in ("doc_id", "status", "chunks_indexed")}
                         for r in ingest_corpus(cfg)]
    return out
