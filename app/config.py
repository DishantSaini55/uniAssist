"""Runtime configuration. Everything is overridable through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    # Structured data: students, courses, attendance, results, rule_registry, source_register.
    # Created on first boot from data/students and corpus/; lives in runtime/ so Docker can persist it.
    sqlite_path: Path = field(default_factory=lambda: Path(_env(
        "SQLITE_PATH", str(Path(_env("RUNTIME_DIR", str(ROOT / "runtime"))) / "university.db"))))
    # Generated state (Chroma vectors, audit log, uploaded originals). Persisted; never re-ingested on restart.
    runtime_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_DIR", str(ROOT / "runtime"))))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "data"))))
    corpus_dir: Path = field(default_factory=lambda: Path(_env("CORPUS_DIR", str(ROOT / "corpus"))))

    # Retrieval
    embedding_backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "st"))  # st | hash
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    chunking: str = field(default_factory=lambda: _env("CHUNKING", "section"))  # section | fixed
    chunk_size: int = field(default_factory=lambda: int(_env("CHUNK_SIZE", "900")))
    chunk_overlap: int = field(default_factory=lambda: int(_env("CHUNK_OVERLAP", "150")))
    top_k: int = field(default_factory=lambda: int(_env("RETRIEVAL_TOP_K", "5")))
    # Abstention: below this cosine similarity (or term coverage) the answer is not_found.
    min_similarity: float = field(default_factory=lambda: float(_env("MIN_SIMILARITY", "0.45")))
    # At or above this similarity a passage is accepted even when the question uses different words
    # ("least attendance" vs "minimum attendance") provided at least some terms overlap.
    strong_similarity: float = field(default_factory=lambda: float(_env("STRONG_SIMILARITY", "0.64")))
    min_term_coverage: float = field(default_factory=lambda: float(_env("MIN_TERM_COVERAGE", "0.5")))

    # LLM (explanations + optional tool planning). mock = deterministic templates, no model.
    llm_mode: str = field(default_factory=lambda: _env("LLM_MODE", "mock").lower())  # mock | ollama
    llm_model: str = field(default_factory=lambda: _env("LOCAL_LLM_MODEL", "llama3.1:8b"))
    llm_base_url: str = field(default_factory=lambda: _env("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"))
    llm_timeout: int = field(default_factory=lambda: int(_env("LLM_TIMEOUT", "90")))

    @property
    def chroma_path(self) -> Path:
        return self.runtime_dir / "chroma"

    @property
    def audit_path(self) -> Path:
        return self.runtime_dir / "audit.db"

    @property
    def uploads_dir(self) -> Path:
        return self.runtime_dir / "uploads"

    def ensure_dirs(self) -> None:
        for p in (self.runtime_dir, self.chroma_path, self.uploads_dir, self.sqlite_path.parent):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()


def configure(**overrides) -> Settings:
    """Mutate the process-wide settings (tests, evaluation harness). Paths may be given as str."""
    for k, v in overrides.items():
        if not hasattr(settings, k):
            raise AttributeError(k)
        setattr(settings, k, Path(v) if k.endswith(("_path", "_dir")) else v)
    return settings
