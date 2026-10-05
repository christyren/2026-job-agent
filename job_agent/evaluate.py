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


def load_material_cases(path: Path = DATA_DIR / "eval_materials.json") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_materials_eval(cases: list[dict] | None = None) -> dict:
    """Milestone 4 eval: same labelled cases, two extraction tiers.

    Per tier we measure what routing actually trades off:
    - requirement_recall: expected requirements the extractor found.
      A missed requirement is the worst failure here — it silently
      produces neither a point nor a gap, so nobody ever reviews it.
    - gap_accuracy: cases where generated gaps == labelled expected_gaps.
    - grounded_rate: share of generated points passing verify_grounding
      (must be 1.0 on both tiers — routing must never buy recall with
      fabricated evidence).
    - total_cost_usd / cost_per_grounded_point from the token logs.
    """
    from .materials import generate_materials, load_resume_chunks, verify_grounding

    cases = cases if cases is not None else load_material_cases()
    chunks = load_resume_chunks()
    tiers: dict[str, dict] = {}
    for tier in ("cheap", "strong"):
        found_total, expected_total, gap_ok = 0, 0, 0
        grounded, points_total, cost = 0, 0, 0.0
        rows = []
        for case in cases:
            job = {k: v for k, v in case.items()
                   if k not in ("expected_requirements", "expected_gaps", "note")}
            draft = generate_materials(job, chunks=chunks, extract_tier=tier)
            extracted = [p["requirement"] for p in draft["points"]] + draft["gaps"]
            missed = [r for r in case["expected_requirements"] if r not in extracted]
            found_total += len(case["expected_requirements"]) - len(missed)
            expected_total += len(case["expected_requirements"])
            gap_ok += int(draft["gaps"] == case["expected_gaps"])
            grounded += len(draft["points"]) - len(verify_grounding(draft["points"], chunks))
            points_total += len(draft["points"])
            cost += draft["total_cost_usd"]
            rows.append({"id": case["id"], "missed": missed,
                         "gaps": draft["gaps"], "points": len(draft["points"]),
                         "cost_usd": draft["total_cost_usd"]})
        tiers[tier] = {
            "requirement_recall": round(found_total / expected_total, 3) if expected_total else 0.0,
            "gap_accuracy": round(gap_ok / len(cases), 3) if cases else 0.0,
            "grounded_rate": round(grounded / points_total, 3) if points_total else 1.0,
            "total_cost_usd": round(cost, 6),
            "cost_per_grounded_point_usd": round(cost / grounded, 6) if grounded else None,
            "rows": rows,
        }
    return {"cases": len(cases), "tiers": tiers}


if __name__ == "__main__":
    report = run_eval()
    for r in report["rows"]:
        mark = "OK " if r["ok"] else "MISS"
        print(f"{mark} {r['id']} {r['company']}: expected={r['expected']} got={r['got']} "
              f"conf={r['confidence']} hard={r['hard_filter_failed']}")
    print(f"\naccuracy: {report['correct']}/{report['total']} = {report['accuracy']:.0%}")
    print("\n--- milestone 4: materials eval (cheap vs strong extraction) ---")
    m = run_materials_eval()
    for tier, t in m["tiers"].items():
        print(f"{tier}: recall={t['requirement_recall']:.0%} gap_accuracy={t['gap_accuracy']:.0%} "
              f"grounded={t['grounded_rate']:.0%} cost=${t['total_cost_usd']} "
              f"per_point=${t['cost_per_grounded_point_usd']}")
