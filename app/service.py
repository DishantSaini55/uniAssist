"""Application service: wires retrieval, ingestion, tools, LLM and the LangGraph pipeline."""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app import db
from app.config import Settings, settings as default_settings
from graph.pipeline import Pipeline
from ingestion.service import DocMeta, IngestionService
from llm.client import LLMClient
from rag.retriever import Retriever


class AssistantService:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.retriever = Retriever(self.cfg)
        self.llm = LLMClient(self.cfg)
        self.pipeline = Pipeline(self.retriever, self.llm)
        self.ingestion = IngestionService(self.cfg)
        self._ingest_lock = threading.Lock()

    def ask(self, question: str, student_id: str | None = None, as_of: str | None = None) -> dict[str, Any]:
        return self.pipeline.run(question, student_id.upper() if student_id else None,
                                 as_of or date.today().isoformat())

    def ingest(self, data: bytes, filename: str, meta: DocMeta) -> dict[str, Any]:
        with self._ingest_lock:  # one writer at a time; readers (/ask) are never blocked
            # Share the vector store handle so /ask sees new chunks immediately.
            self.ingestion._store = self.retriever.store
            return self.ingestion.ingest(data, filename, meta)

    def remove_document(self, doc_id: str) -> bool:
        with self._ingest_lock:
            self.ingestion._store = self.retriever.store
            return self.ingestion.remove(doc_id)

    def health(self) -> dict[str, Any]:
        comp: dict[str, Any] = {"api": {"status": "ok"}}
        try:
            comp["sqlite"] = {"status": "ok", "students": db.one("SELECT COUNT(*) n FROM students")["n"],
                              "documents": db.one("SELECT COUNT(*) n FROM source_register")["n"],
                              "rules": db.one("SELECT COUNT(*) n FROM rule_registry")["n"]}
        except Exception as exc:
            comp["sqlite"] = {"status": "error", "detail": type(exc).__name__}
        try:
            comp["vector_store"] = {"status": "ok", "chunks": self.retriever.store.count(),
                                    "embedder": self.retriever.store.embedder.name,
                                    "persisted_at": str(self.cfg.chroma_path)}
        except Exception as exc:
            comp["vector_store"] = {"status": "error", "detail": type(exc).__name__}
        comp["llm"] = self.llm.health()
        core_ok = comp["sqlite"]["status"] == "ok" and comp["vector_store"]["status"] == "ok"
        return {"status": "ok" if core_ok else "degraded", "components": comp}


_service: AssistantService | None = None


def get_service() -> AssistantService:
    global _service
    if _service is None:
        _service = AssistantService()
    return _service


def reset_service() -> None:
    global _service
    _service = None
