"""Test environment: throw-away runtime dir, the real default embedder (bge-small), mock LLM."""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


@pytest.fixture(scope="session")
def env():
    tmp = Path(tempfile.mkdtemp(prefix="uniassist_test_"))
    config.configure(runtime_dir=tmp, sqlite_path=tmp / "university.db", embedding_backend="st",
                     embedding_model="BAAI/bge-small-en-v1.5", llm_mode="mock", chunking="section")
    from app import bootstrap as boot, service

    service.reset_service()
    boot.bootstrap(config.settings)
    return tmp


@pytest.fixture(scope="session")
def client(env):
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as c:
        yield c
