"""Ingest every document listed in corpus/source_register.csv (idempotent: unchanged files are skipped)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.config import Settings, settings as default_settings
from ingestion.service import DocMeta, IngestionService


def load_register(csv_path: Path) -> list[dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ingest_corpus(cfg: Settings | None = None, only_synthetic: bool | None = None,
                  service: IngestionService | None = None) -> list[dict[str, Any]]:
    cfg = cfg or default_settings
    csv_path = cfg.corpus_dir / "source_register.csv"
    if not csv_path.exists():
        return []
    svc = service or IngestionService(cfg)
    out = []
    for row in load_register(csv_path):
        if only_synthetic is not None and (row.get("synthetic", "N") == "Y") != only_synthetic:
            continue
        path = cfg.corpus_dir / row["filename"]
        if not path.is_file():
            out.append({"doc_id": row["doc_id"], "status": "missing_file", "file": str(path)})
            continue
        meta = DocMeta(**{k: v for k, v in row.items() if k != "filename" and v is not None})
        out.append(svc.ingest(path.read_bytes(), path.name, meta))
    return out
