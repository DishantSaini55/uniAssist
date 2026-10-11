"""Ingest documents from the command line (the API equivalent is POST /ingest).

    python scripts/ingest.py --corpus                       # everything in corpus/source_register.csv
    python scripts/ingest.py --file path/to.pdf --meta meta.json
    python scripts/ingest.py --file notice.pdf --doc-id DEPT-2026-04 --title "..." --issuer "HoD CSE" \
        --authority 3 --type notice --version 1.0 --effective-from 2026-09-01
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.bootstrap import bootstrap  # noqa: E402
from app.config import settings  # noqa: E402
from ingestion.bootstrap import ingest_corpus  # noqa: E402
from ingestion.service import DocMeta, IngestionService  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--meta", help="JSON file with the Annex B fields")
    for f in ("doc-id", "title", "issuer", "type", "version", "effective-from", "effective-to", "supersedes",
              "scope-programmes", "scope-batches", "provenance", "retrieved-on"):
        ap.add_argument(f"--{f}")
    ap.add_argument("--authority", type=int)
    ap.add_argument("--synthetic", default="N")
    a = ap.parse_args()
    bootstrap(settings, students=False, corpus=False)
    if a.corpus:
        print(json.dumps(ingest_corpus(settings), indent=2))
    if a.file:
        meta = json.loads(Path(a.meta).read_text(encoding="utf-8")) if a.meta else {}
        for k, v in {"doc_id": a.doc_id, "title": a.title, "issuer": a.issuer, "doc_type": a.type, "version": a.version,
                     "effective_from": a.effective_from, "effective_to": a.effective_to, "supersedes": a.supersedes,
                     "scope_programmes": a.scope_programmes, "scope_batches": a.scope_batches,
                     "provenance": a.provenance, "retrieved_on": a.retrieved_on, "authority_level": a.authority,
                     "synthetic": a.synthetic}.items():
            if v is not None:
                meta[k] = v
        p = Path(a.file)
        print(json.dumps(IngestionService(settings).ingest(p.read_bytes(), p.name, DocMeta(**meta)), indent=2))
    if not (a.corpus or a.file):
        ap.error("give --corpus or --file")
