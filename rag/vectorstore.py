"""ChromaDB (persisted to disk) wrapper. Restarts never re-ingest: vectors live in runtime/chroma."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from app.config import Settings, settings as default_settings
from rag.embeddings import get_embedder

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")   # Chroma must not phone home with student-service data
_clients: dict[str, Any] = {}
_lock = threading.Lock()
COLLECTION = "uniassist_chunks"


def _client(path: Path):
    import chromadb

    key = str(path.resolve())
    with _lock:
        if key not in _clients:
            path.mkdir(parents=True, exist_ok=True)
            _clients[key] = chromadb.PersistentClient(path=key)
        return _clients[key]


class VectorStore:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.embedder = get_embedder(self.cfg.embedding_backend, self.cfg.embedding_model)
        self.collection = _client(self.cfg.chroma_path).get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine", "embedder": self.embedder.name})
        stored = (self.collection.metadata or {}).get("embedder")
        if stored and stored != self.embedder.name and self.collection.count():
            raise RuntimeError(
                f"Vector store was built with '{stored}' but the configured embedder is "
                f"'{self.embedder.name}'. Re-ingest or point RUNTIME_DIR at a fresh directory.")

    def count(self) -> int:
        return self.collection.count()

    def delete_doc(self, doc_id: str) -> None:
        self.collection.delete(where={"doc_id": doc_id})

    def add_chunks(self, doc_id: str, chunks: list) -> int:
        if not chunks:
            return 0
        vectors = self.embedder.encode_docs([c.embed_text for c in chunks])
        self.collection.add(
            ids=[f"{doc_id}::{c.index}" for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=vectors,
            metadatas=[{"doc_id": doc_id, "section": c.section, "page": int(c.page), "chunk_index": c.index}
                       for c in chunks])
        return len(chunks)

    def query(self, text: str, n: int) -> list[dict[str, Any]]:
        total = self.collection.count()
        if not total:
            return []
        res = self.collection.query(query_embeddings=[self.embedder.encode_query(text)],
                                    n_results=min(n, total))
        out = []
        for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            out.append({"chunk_id": cid, "text": doc, "doc_id": meta["doc_id"], "section": meta["section"],
                        "page": meta["page"], "score": round(1.0 - float(dist), 4)})
        return out

    def get_chunk(self, doc_id: str, section: str) -> dict[str, Any] | None:
        got = self.collection.get(where={"$and": [{"doc_id": doc_id}, {"section": section}]},
                                  include=["documents", "metadatas"])
        if not got["ids"]:
            return None
        return {"chunk_id": got["ids"][0], "text": got["documents"][0], "doc_id": doc_id, "section": section,
                "page": got["metadatas"][0]["page"], "score": 0.0}

    def page_of(self, doc_id: str, section: str) -> int | None:
        got = self.collection.get(where={"$and": [{"doc_id": doc_id}, {"section": section}]}, include=["metadatas"])
        pages = [m["page"] for m in got["metadatas"]]
        return min(pages) if pages else None
