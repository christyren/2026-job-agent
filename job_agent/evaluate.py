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
    """Milestone 4 eval: same labelled cases, three extraction tiers.

    Per tier we measure what routing actually trades off:
    - requirement_recall: expected requirements the extractor found.
      A missed requirement is the worst failure here — it silently
      produces neither a point nor a gap, so nobody ever reviews it.
    - gap_accuracy: cases where generated gaps == labelled expected_gaps.
      Note it can stay 1.0 while recall is bad: a missed requirement
      never enters gaps at all, so only recall sees a silent miss.
    - grounded_rate: share of generated points passing verify_grounding
      (must be 1.0 on all tiers — routing must never buy recall with
      fabricated evidence).
    - total_cost_usd / cost_per_grounded_point from the token logs.
    """
    from .materials import generate_materials, load_resume_chunks, verify_grounding

    cases = cases if cases is not None else load_material_cases()
    chunks = load_resume_chunks()
    tiers: dict[str, dict] = {}
    for tier in ("cheap", "mid", "strong"):
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


def run_materials_cascade_eval(cases: list[dict] | None = None,
                               min_requirements: int = 4) -> dict:
    """Iteration 2026-11-15 eval: cascade extraction vs the fixed tiers.

    Same labelled cases and same metrics as run_materials_eval (recall /
    gap accuracy / grounded rate / cost), plus the cascade audit trail:
    how many cases escalated, and which tier each case stopped at.
    The threshold itself is measured, not assumed: `threshold_sensitivity`
    reports recall at min_requirements=3 vs 4, because the trigger is a
    heuristic — at 3, eval-m01 looks 'fine' (3 direct hits) and the
    cascade stops before ever seeing Kafka / REST API.
    """
    from .materials import (MIN_REQUIREMENTS, extract_requirements_cascade,
                            generate_materials_cascade, load_resume_chunks,
                            verify_grounding)

    cases = cases if cases is not None else load_material_cases()
    chunks = load_resume_chunks()
    found_total, expected_total, gap_ok = 0, 0, 0
    grounded, points_total, cost = 0, 0, 0.0
    escalated, rows = 0, []
    for case in cases:
        job = {k: v for k, v in case.items()
               if k not in ("expected_requirements", "expected_gaps", "note")}
        draft = generate_materials_cascade(job, chunks=chunks,
                                           min_requirements=min_requirements)
        extracted = [p["requirement"] for p in draft["points"]] + draft["gaps"]
        missed = [r for r in case["expected_requirements"] if r not in extracted]
        found_total += len(case["expected_requirements"]) - len(missed)
        expected_total += len(case["expected_requirements"])
        gap_ok += int(draft["gaps"] == case["expected_gaps"])
        grounded += len(draft["points"]) - len(verify_grounding(draft["points"], chunks))
        points_total += len(draft["points"])
        cost += draft["total_cost_usd"]
        escalated += int(draft["cascade"]["escalated"])
        rows.append({"id": case["id"], "missed": missed,
                     "tiers_tried": draft["cascade"]["tiers_tried"],
                     "final_tier": draft["cascade"]["final_tier"],
                     "points": len(draft["points"]),
                     "cost_usd": draft["total_cost_usd"]})
    sensitivity = {}
    for threshold in (3, 4):
        found = 0
        for case in cases:
            job = {k: v for k, v in case.items()
                   if k not in ("expected_requirements", "expected_gaps", "note")}
            got = extract_requirements_cascade(job, min_requirements=threshold)
            found += sum(1 for r in case["expected_requirements"]
                         if r in got["requirements"])
        sensitivity[threshold] = round(found / expected_total, 3) if expected_total else 0.0
    return {
        "cases": len(cases),
        "min_requirements": min_requirements,
        "requirement_recall": round(found_total / expected_total, 3) if expected_total else 0.0,
        "gap_accuracy": round(gap_ok / len(cases), 3) if cases else 0.0,
        "grounded_rate": round(grounded / points_total, 3) if points_total else 1.0,
        "total_cost_usd": round(cost, 6),
        "cost_per_grounded_point_usd": round(cost / grounded, 6) if grounded else None,
        "escalated_cases": escalated,
        "threshold_sensitivity": sensitivity,
        "rows": rows,
    }


def build_adversarial_drafts(chunks=None) -> list[dict]:
    """Iteration 2026-11-16: six hand-built BAD drafts.

    All six start from the eval-m01 job (RoutingCloud: Java /
    distributed systems / Kafka / REST API / AWS) and real resume
    chunks — each draft breaks exactly one rule, so a checker that
    passes it is provably blind to that failure mode. adv-06 (added
    2026-11-18) is a sharper second case for
    evidence_supports_requirement: same rule as adv-03, but the wrong
    chunk is the education block — a chunk about degrees, not
    engineering at all — and the quote is verbatim, so the verbatim
    check must pass while the supports check must fail.
    """
    from .materials import load_resume_chunks

    chunks = chunks if chunks is not None else load_resume_chunks()
    by_id = {c.id: c for c in chunks}
    cases = {c["id"]: c for c in load_material_cases()}
    job = {k: v for k, v in cases["eval-m01"].items()
           if k not in ("expected_requirements", "expected_gaps", "note")}
    skills = by_id["resume-skills"]
    tesla = by_id["tesla-fullstack"]
    spark_chunk = by_id["microsoft-data-ai"]
    education = by_id["education"]

    def point(req, chunk, quote=None):
        return {"requirement": req, "evidence_chunk_id": chunk.id,
                "evidence_quote": quote if quote is not None else chunk.text,
                "point": f"For {req}: {chunk.text}"}

    honest_points = [point(r, by_id["resume-skills"]) for r in
                     ("Java", "distributed systems", "Kafka", "REST API", "AWS")]
    return [
        {"id": "adv-01-fabricated-quote", "job": job,
         "draft": {"job_company": "RoutingCloud",
                   "points": [point("Java", skills,
                                    "10 years of Kafka experience leading platform teams")]
                             + honest_points[1:],
                   "gaps": []},
         "expected_failing_check": "evidence_verbatim",
         "note": "quote is invented, not in the cited chunk"},
        {"id": "adv-02-requirement-not-in-jd", "job": job,
         "draft": {"job_company": "RoutingCloud",
                   "points": honest_points + [point("Spark", spark_chunk)],
                   "gaps": []},
         "expected_failing_check": "requirement_in_jd",
         "note": "Spark is a real resume skill, but this JD never asks for it"},
        {"id": "adv-03-wrong-chunk", "job": job,
         "draft": {"job_company": "RoutingCloud",
                   "points": [point("Java", skills),
                              point("distributed systems", skills),
                              point("Kafka", tesla),
                              point("REST API", skills),
                              point("AWS", skills)],
                   "gaps": []},
         "expected_failing_check": "evidence_supports_requirement",
         "note": "real chunk + verbatim quote, but the chunk is about Tesla C#/SQL, not Kafka"},
        {"id": "adv-04-fake-gap", "job": job,
         "draft": {"job_company": "RoutingCloud",
                   "points": [p for p in honest_points if p["requirement"] != "Kafka"],
                   "gaps": ["Kafka"]},
         "expected_failing_check": "gap_is_real",
         "note": "claims no resume evidence for Kafka, but resume-skills covers it"},
        {"id": "adv-05-orphan-requirement", "job": job,
         "draft": {"job_company": "RoutingCloud",
                   "points": [p for p in honest_points if p["requirement"] != "AWS"],
                   "gaps": []},
         "expected_failing_check": "no_orphan_requirements",
         "note": "AWS silently dropped: neither a point nor a gap"},
        {"id": "adv-06-wrong-chunk-education", "job": job,
         "draft": {"job_company": "RoutingCloud",
                   "points": [point("Java", education)] + honest_points[1:],
                   "gaps": []},
         "expected_failing_check": "evidence_supports_requirement",
         "note": "Java IS in the JD and the quote IS verbatim from the "
                 "education chunk — but that chunk is about degrees "
                 "(statistics / WashU / BFSU) and says nothing about "
                 "Java; adv-03's sharper cousin: the wrong chunk is "
                 "not even engineering"},
    ]


def run_checker_eval(cases: list[dict] | None = None) -> dict:
    """Iteration 2026-11-16 eval: the independent checker, three ways.

    A. Baseline: the honest cascade pipeline on the 5 labelled cases —
       the checker should find nothing wrong (0 repairs). A checker
       that flags honest drafts is noise, not safety.
    B. Adversarial: the 6 hand-built bad drafts — the checker must
       catch the exact failing check in each, and one repair + re-check
       must clear it. Detection and repair are measured separately:
       catching a bug you cannot fix still ends at a human.
    C. Full checked pipeline on the 5 cases — checker_passed rate,
       repairs used, steps (must stay <= 8), and total cost.
    """
    from .checker import (MAX_STEPS_CHECKED, check_and_repair,
                          check_materials, repair_materials)
    from .materials import (generate_materials_cascade, load_resume_chunks)

    cases = cases if cases is not None else load_material_cases()
    chunks = load_resume_chunks()

    baseline_rows, baseline_pass = [], 0
    for case in cases:
        job = {k: v for k, v in case.items()
               if k not in ("expected_requirements", "expected_gaps", "note")}
        draft = generate_materials_cascade(job, chunks=chunks)
        report = check_materials(job, draft, chunks=chunks)
        baseline_pass += int(report["passed"])
        baseline_rows.append({"id": case["id"], "passed": report["passed"],
                              "num_checks": report["num_checks"],
                              "num_failed": report["num_failed"]})

    adv_rows, detected, repaired = [], 0, 0
    for adv in build_adversarial_drafts(chunks):
        report = check_materials(adv["job"], adv["draft"], chunks=chunks)
        caught = any(f["check"] == adv["expected_failing_check"]
                     for f in report["failures"])
        detected += int(caught)
        fixed = repair_materials(adv["job"], adv["draft"], report["failures"],
                                 chunks=chunks)
        recheck = check_materials(adv["job"], fixed["draft"], chunks=chunks)
        repaired += int(recheck["passed"])
        adv_rows.append({"id": adv["id"],
                         "expected_failing_check": adv["expected_failing_check"],
                         "caught": caught,
                         "failures": [f["check"] for f in report["failures"]],
                         "repairs": [r["fix"] for r in fixed["repairs"]],
                         "recheck_passed": recheck["passed"]})

    checked_rows, checked_pass, repairs_used, steps_max, cost = [], 0, 0, 0, 0.0
    for case in cases:
        job = {k: v for k, v in case.items()
               if k not in ("expected_requirements", "expected_gaps", "note")}
        from .checker import generate_materials_checked
        result = generate_materials_checked(job, chunks=chunks)
        checked_pass += int(result["checker_passed"])
        repairs_used += len(result["repairs"])
        steps_max = max(steps_max, result["steps_used"])
        cost += result["total_cost_usd"]
        checked_rows.append({"id": case["id"],
                             "checker_passed": result["checker_passed"],
                             "repairs": len(result["repairs"]),
                             "steps_used": result["steps_used"],
                             "cost_usd": result["total_cost_usd"]})
    return {
        "baseline": {"cases": len(cases), "passed": baseline_pass,
                     "rows": baseline_rows},
        "adversarial": {"cases": len(adv_rows), "detected": detected,
                        "repaired": repaired, "rows": adv_rows},
        "checked_pipeline": {"cases": len(cases), "passed": checked_pass,
                             "repairs_used": repairs_used,
                             "max_steps_used": steps_max,
                             "step_cap": MAX_STEPS_CHECKED,
                             "total_cost_usd": round(cost, 6), "rows": checked_rows},
    }


def run_trace_eval(cases: list[dict] | None = None,
                   max_cost_usd: float = 0.10) -> dict:
    """Iteration 2026-11-17 eval: traces + cost-cap governance.

    Two parts, both on the same 5 labelled materials cases:
    A. Normal cap ($0.10, ~2x a healthy run): every traced run must
       finish ok, ship its draft, and stay under the cap — the cap
       must not trip on honest work, or it is noise, not safety.
    B. Forced over-cap (cap $0.001 on eval-m01): the run MUST trip,
       ship NO draft, and come back over_cap=True + needs_human=True,
       with the trace naming the step that crossed the line.
       A cap that never trips in a test is an untested promise.
    Traces are written to a throwaway path (not data/traces.jsonl),
    so the eval never pollutes the real audit trail.
    """
    import tempfile

    from .tracing import (DEFAULT_MAX_RUN_COST_USD, run_traced_checked,
                          trace_summary)

    cases = cases if cases is not None else load_material_cases()
    with tempfile.TemporaryDirectory() as tmp:
        trace_path = Path(tmp) / "traces.jsonl"
        rows, ok_runs, cost = [], 0, 0.0
        for case in cases:
            job = {k: v for k, v in case.items()
                   if k not in ("expected_requirements", "expected_gaps", "note")}
            out = run_traced_checked(job, max_cost_usd=max_cost_usd,
                                     trace_path=trace_path)
            ok_runs += int(not out["over_cap"] and out["draft"] is not None)
            cost += out["trace"]["total_cost_usd"]
            rows.append({"id": case["id"],
                         "status": out["trace"]["status"],
                         "steps": out["trace"]["num_steps"],
                         "cost_usd": out["trace"]["total_cost_usd"],
                         "over_alert": out["trace"]["over_alert"],
                         "draft_shipped": out["draft"] is not None})
        forced_job = {k: v for k, v in cases[0].items()
                      if k not in ("expected_requirements", "expected_gaps", "note")}
        forced = run_traced_checked(forced_job, max_cost_usd=0.001,
                                    trace_path=trace_path)
        summary = trace_summary(path=trace_path)
    return {
        "cases": len(cases),
        "max_cost_usd": max_cost_usd,
        "normal_cap": {"ok_runs": ok_runs, "total_cost_usd": round(cost, 6),
                       "rows": rows},
        "forced_over_cap": {
            "case_id": cases[0]["id"],
            "cap_usd": 0.001,
            "over_cap": forced["over_cap"],
            "needs_human": forced["needs_human"],
            "draft_shipped": forced["draft"] is not None,
            "steps_recorded": forced["trace"]["num_steps"],
            "cost_at_trip_usd": forced["trace"]["total_cost_usd"],
            "error": forced["error"],
        },
        "trace_summary": summary,
    }


def load_planner_cases(path: Path = DATA_DIR / "eval_planner.json") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_planner_eval(cases: list[dict] | None = None) -> dict:
    """Milestone 6 eval: labelled scenarios for the planner.

    Each case is a snapshot (apps + memory + fixed 'today') with the
    expected TOP action after thresholding. We check the top action
    only — the plan is ordered, so position 1 is the decision that
    would actually drive the morning. A case flipping is a real signal
    (e.g. plan-05 must stay needs_review: memory is pausing that
    company, and a planner that 'forgets' it fails here first).
    """
    from .planning import plan_today

    cases = cases if cases is not None else load_planner_cases()
    rows = []
    correct = 0
    for case in cases:
        plan = plan_today(apps=case["apps"], today=case["today"],
                          memory_entries=case.get("memory_entries", []))
        top = plan[0] if plan else {"action": "wait", "confidence": 0.0}
        ok = top["action"] == case["expected_top_action"]
        correct += int(ok)
        rows.append({"id": case["id"], "expected": case["expected_top_action"],
                     "got": top["action"], "confidence": top["confidence"],
                     "ok": ok})
    total = len(cases)
    return {"total": total, "correct": correct,
            "accuracy": round(correct / total, 3) if total else 0.0,
            "mismatches": [r for r in rows if not r["ok"]], "rows": rows}


def load_decisions_cases(path: Path = DATA_DIR / "eval_decisions.json") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_decisions_eval(cases: list[dict] | None = None) -> dict:
    """Iteration 2026-11-12 eval: planner -> Decisions-API batch.

    Each case is a snapshot (apps + memory + fixed 'today') with the
    expected TOP choice, its confidence, the batch score question's value,
    and the total question count (2 per decision + 1 batch score).
    A case flips only if the mapping itself is wrong — the planner eval
    already covers the underlying decisions, this one covers the shape
    (noul/choice/score) they are packed into.
    """
    from .decisions import build_decisions_batch

    cases = cases if cases is not None else load_decisions_cases()
    rows = []
    correct = 0
    for case in cases:
        report = build_decisions_batch(apps=case["apps"], today=case["today"],
                                       memory_entries=case.get("memory_entries", []))
        choices = [q for q in report["questions"] if q["question_type"] == "choice"]
        top = choices[0] if choices else {"chosen": "wait", "confidence": 0.0}
        ok = (top["chosen"] == case["expected_top_chosen"]
              and top["confidence"] == case["expected_top_confidence"]
              and report["plan_score"] == case["expected_plan_score"]
              and report["num_questions"] == case["expected_num_questions"])
        correct += int(ok)
        rows.append({"id": case["id"], "expected": case["expected_top_chosen"],
                     "got": top["chosen"], "confidence": top["confidence"],
                     "plan_score": report["plan_score"],
                     "num_questions": report["num_questions"], "ok": ok})
    total = len(cases)
    return {"total": total, "correct": correct,
            "accuracy": round(correct / total, 3) if total else 0.0,
            "mismatches": [r for r in rows if not r["ok"]], "rows": rows}


def run_batch_scoring_eval(cases: list[dict] | None = None,
                           prefs: dict | None = None) -> dict:
    """Iteration 2026-11-13 eval: the same 10 labelled jobs, as one batch.

    The batch must give the SAME verdicts as one-by-one scoring
    (run_eval) — batching changes the packaging and the price, never
    the decision. Each row checks the choice question's winner
    against the labelled verdict, plus the batch cost saving.
    """
    from .batch_scoring import score_jobs_batch

    cases = cases if cases is not None else load_eval_cases()
    prefs = prefs if prefs is not None else get_preferences()
    jobs = [{k: v for k, v in c.items() if k not in ("expected_verdict", "note")}
            for c in cases]
    report = score_jobs_batch(jobs, prefs)
    rows = []
    correct = 0
    for case, result in zip(cases, report["results"]):
        ok = result["verdict"] == case["expected_verdict"]
        correct += int(ok)
        rows.append({"id": case.get("id", "?"), "company": case.get("company", "?"),
                     "expected": case["expected_verdict"], "got": result["verdict"],
                     "confidence": result["confidence"], "ok": ok})
    total = len(cases)
    return {"total": total, "correct": correct,
            "accuracy": round(correct / total, 3) if total else 0.0,
            "mismatches": [r for r in rows if not r["ok"]], "rows": rows,
            "num_questions": report["num_questions"],
            "batch_score": report["batch_score"],
            "batch_cost_usd": report["cost"]["estimated_cost_usd"],
            "per_job_cost_usd": report["per_job_cost"]["estimated_cost_usd"],
            "saving_factor": report["saving_factor"]}


if __name__ == "__main__":
    report = run_eval()
    for r in report["rows"]:
        mark = "OK " if r["ok"] else "MISS"
        print(f"{mark} {r['id']} {r['company']}: expected={r['expected']} got={r['got']} "
              f"conf={r['confidence']} hard={r['hard_filter_failed']}")
    print(f"\naccuracy: {report['correct']}/{report['total']} = {report['accuracy']:.0%}")
    print("\n--- milestone 4: materials eval (cheap vs mid vs strong extraction) ---")
    m = run_materials_eval()
    for tier, t in m["tiers"].items():
        print(f"{tier}: recall={t['requirement_recall']:.0%} gap_accuracy={t['gap_accuracy']:.0%} "
              f"grounded={t['grounded_rate']:.0%} cost=${t['total_cost_usd']} "
              f"per_point=${t['cost_per_grounded_point_usd']}")
    print("\n--- iteration 2026-11-15: materials cascade eval ---")
    c = run_materials_cascade_eval()
    print(f"cascade (min_requirements={c['min_requirements']}): "
          f"recall={c['requirement_recall']:.0%} gap_accuracy={c['gap_accuracy']:.0%} "
          f"grounded={c['grounded_rate']:.0%} cost=${c['total_cost_usd']} "
          f"per_point=${c['cost_per_grounded_point_usd']} "
          f"escalated={c['escalated_cases']}/{c['cases']}")
    for r in c["rows"]:
        print(f"  {r['id']}: tiers={'+'.join(r['tiers_tried'])} "
              f"missed={r['missed']} cost=${r['cost_usd']}")
    print(f"threshold sensitivity (recall): min=3 -> {c['threshold_sensitivity'][3]:.0%}, "
          f"min=4 -> {c['threshold_sensitivity'][4]:.0%}")

    print("\n--- iteration 2026-11-16: checker eval (solver / checker split) ---")
    k = run_checker_eval()
    print(f"baseline (honest cascade drafts): {k['baseline']['passed']}/{k['baseline']['cases']} passed, "
          f"repairs should be 0")
    print(f"adversarial: detected {k['adversarial']['detected']}/{k['adversarial']['cases']}, "
          f"repaired {k['adversarial']['repaired']}/{k['adversarial']['cases']}")
    for r in k["adversarial"]["rows"]:
        print(f"  {r['id']}: caught={r['caught']} ({r['failures']}) "
              f"repairs={r['repairs']} recheck_passed={r['recheck_passed']}")
    cp = k["checked_pipeline"]
    print(f"checked pipeline: {cp['passed']}/{cp['cases']} passed, repairs_used={cp['repairs_used']}, "
          f"max_steps={cp['max_steps_used']}/{cp['step_cap']}, cost=${cp['total_cost_usd']}")

    print("\n--- iteration 2026-11-17: trace + cost-cap eval ---")
    tr = run_trace_eval()
    print(f"trace eval (cap ${tr['max_cost_usd']}): "
          f"{tr['normal_cap']['ok_runs']}/{tr['cases']} runs ok, "
          f"total cost ${tr['normal_cap']['total_cost_usd']}")
    for r in tr["normal_cap"]["rows"]:
        print(f"  {r['id']}: {r['status']} steps={r['steps']} "
              f"cost=${r['cost_usd']} over_alert={r['over_alert']} "
              f"draft_shipped={r['draft_shipped']}")
    f = tr["forced_over_cap"]
    print(f"forced over-cap (cap ${f['cap_usd']}): over_cap={f['over_cap']} "
          f"needs_human={f['needs_human']} draft_shipped={f['draft_shipped']} "
          f"steps_recorded={f['steps_recorded']} cost_at_trip=${f['cost_at_trip_usd']}")
    print(f"trace summary: {tr['trace_summary']['runs']} runs, "
          f"total ${tr['trace_summary']['total_cost_usd']}, "
          f"over_cap={tr['trace_summary']['over_cap_runs']}, "
          f"most expensive={tr['trace_summary']['most_expensive']}")

    print("\n--- milestone 6: planner eval ---")
    p = run_planner_eval()
    for r in p["rows"]:
        mark = "OK " if r["ok"] else "MISS"
        print(f"{mark} {r['id']}: expected={r['expected']} got={r['got']} conf={r['confidence']}")
    print(f"planner accuracy: {p['correct']}/{p['total']} = {p['accuracy']:.0%}")
    print("\n--- iteration 2026-11-12: decisions batch eval ---")
    d = run_decisions_eval()
    for r in d["rows"]:
        mark = "OK " if r["ok"] else "MISS"
        print(f"{mark} {r['id']}: expected={r['expected']} got={r['got']} conf={r['confidence']} "
              f"plan_score={r['plan_score']} questions={r['num_questions']}")
    print(f"decisions accuracy: {d['correct']}/{d['total']} = {d['accuracy']:.0%}")
    print("\n--- iteration 2026-11-13: batch scoring eval ---")
    b = run_batch_scoring_eval()
    for r in b["rows"]:
        mark = "OK " if r["ok"] else "MISS"
        print(f"{mark} {r['id']}: expected={r['expected']} got={r['got']} conf={r['confidence']}")
    print(f"batch scoring accuracy: {b['correct']}/{b['total']} = {b['accuracy']:.0%} "
          f"questions={b['num_questions']} batch_score={b['batch_score']} "
          f"batch_cost=${b['batch_cost_usd']} vs per-job ${b['per_job_cost_usd']} "
          f"(saving ~{b['saving_factor']}x)")
