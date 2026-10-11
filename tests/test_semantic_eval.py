"""Slow: runs the real bge-small model through the evaluation harness (needs the model in the HF cache)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_default_configuration_meets_quality_bar(tmp_path):
    out = tmp_path / "r.json"
    p = subprocess.run([sys.executable, str(ROOT / "evaluation" / "run_eval.py"), "--configs", "A", "--out",
                        str(tmp_path / "r.md"), "--json", str(out)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-1500:]
    s = json.loads(out.read_text())[0]["summary"]
    assert s["answer_correctness"][0] >= 90 and s["abstention_accuracy"][0] >= 95
    assert s["citation_accuracy"][0] >= 90 and s["tool_result_correctness"][0] == 100
