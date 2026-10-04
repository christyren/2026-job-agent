"""Milestone 3 eval: run the scorer over hand-labelled cases and measure it.

An eval is just: labelled inputs + expected outputs + an accuracy number.
Its job is to catch regressions — if you change the prompt, the heuristic,
or REVIEW_THRESHOLD and accuracy drops, the eval tells you before a real
job list does. 10 cases is a toy (real eval sets want 50+); treat any
small percentage swing here as noise, but treat a *specific case flipping*
as a real signal worth reading.
"""
from __future__ import annotations

import json
from pathlib import Path

from .scoring import score_job
from .tools import get_preferences

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EVAL_PATH = DATA_DIR / "eval_jobs.json"


def load_eval_cases(path: Path = EVAL_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_eval(cases: list[dict] | None = None, prefs: dict | None = None) -> dict:
    cases = cases if cases is not None else load_eval_cases()
    prefs = prefs if prefs is not None else get_preferences()
    rows = []
    correct = 0
    for case in cases:
        job = {k: v for k, v in case.items() if k not in ("expected_verdict", "note")}
        result = score_job(job, prefs)
        ok = result.verdict == case["expected_verdict"]
        correct += int(ok)
        rows.append({
            "id": case.get("id", "?"),
            "company": case.get("company", "?"),
            "expected": case["expected_verdict"],
            "got": result.verdict,
            "confidence": result.confidence,
            "hard_filter_failed": result.hard_filter_failed,
            "ok": ok,
        })
    total = len(cases)
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else 0.0,
        "mismatches": [r for r in rows if not r["ok"]],
        "rows": rows,
    }


if __name__ == "__main__":
    report = run_eval()
    for r in report["rows"]:
        mark = "OK " if r["ok"] else "MISS"
        print(f"{mark} {r['id']} {r['company']}: expected={r['expected']} got={r['got']} "
              f"conf={r['confidence']} hard={r['hard_filter_failed']}")
    print(f"\naccuracy: {report['correct']}/{report['total']} = {report['accuracy']:.0%}")
