"""Create the SQLite schema, load the synthetic students and ingest corpus/source_register.csv (idempotent)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.bootstrap import bootstrap  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(bootstrap(), indent=2))
