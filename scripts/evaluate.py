"""Run the evaluation set (evaluation/run_eval.py) and write evaluation/report.md + results.json."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "evaluation" / "run_eval.py"), run_name="__main__")
