"""Iteration 2026-11-16: independent checker step (solver / checker split).

The pipeline so far generates and then checks its own work with
verify_grounding — but that check lives INSIDE the generator's step,
uses the generator's own requirement list, and can only catch one kind
of failure (a bad quote). Today the check moves OUT of the generator
into a separate, independent step, the way Google's Antigravity
/teamwork splits producers from critics/auditors: the solver proposes,
the checker disposes, and the checker re-derives everything from the
raw inputs (the JD text and the resume chunks) instead of trusting
anything the solver claims.

The checker runs FIVE independent checks on a draft:

1. requirement_in_jd — every point's requirement must actually be a
   requirement of THIS job. The checker re-extracts the JD's
   requirements itself (strong tier: exact hits + full synonym map),
   independently of which tier the generator used. A point about a
   skill the JD never asked for is invented work, even if the resume
   really has that skill.
2. evidence_supports_requirement — the cited chunk must actually cover
   the requirement (chunk.covers). A real chunk + a real quote is not
   enough if the chunk is about something else: citing the Tesla
   chunk for a Kafka point is a wrong-citation bug, not grounding.
3. evidence_verbatim — the quote must be a verbatim substring of the
   cited chunk (the milestone-4 verify_grounding rule, now owned by
   the checker instead of the generator).
4. gap_is_real — every listed gap must genuinely have no resume
   evidence (retrieve returns None). A "fake gap" is the mirror image
   of a hallucination: the system claims you lack a skill you actually
   have, and the point that should exist never gets written.
5. no_orphan_requirements — every requirement the checker extracts
   from the JD must appear as a point OR a gap. This is the completeness
   check: it catches the milestone-4 silent miss (a requirement the
   extractor never found, so nobody ever reviews it) from the other
   side — the checker sees the JD, not the extractor's output.

On failure the checker does not just say no: it returns a structured
failure list (check name + item + reason), and `repair_materials`
fixes each failure deterministically from that list — drop a point
about a skill the JD never asked for, re-cite the right chunk,
turn a fake gap into a point, add an orphan as a point or a gap.
That list is the interface: in a real LLM version the repair would be
a new model call with the failures in the prompt; the shape (failures
in -> fixed draft out -> re-check) stays the same, which is why the
offline version is a faithful simulation and not a toy.

Two rules the checker enforces about itself:

- The check must cost LESS than the work it verifies. The check step
  is billed at the cheap tier (offline it is pure code, so the logged
  cost is a lower bound; a real checker would be a small-model call).
  If verifying cost more than generating, the split would be a loss.
- One repair only (MAX_REPAIRS=1), then re-check once. A checker that
  can loop forever is a runaway loop with a polite name. If the draft
  still fails after the repair, it ships flagged
  (checker_passed=False, needs_human=True) — never silently, never
  auto-fixed a second time. The step cap for the checked pipeline is
  8 (3 extract + generate + verify + check + repair + re-check),
  one step above the plain pipeline's 6, and the cap is what makes
  "one repair" a hard promise instead of a hope.

Nothing here submits an application; a checked draft is still a draft
for human review — the checker only raises the bar for what reaches
the human.
"""
from __future__ import annotations

import json

from .materials import (MAX_STEPS_PER_TASK, RunBudget, extract_requirements,
                        generate_materials_cascade, load_resume_chunks,
                        retrieve, verify_grounding)

# Checked pipeline step cap: plain pipeline worst case is 5 steps
# (3 cascade extracts + generate + verify); the checker adds a check
# step, one repair step, and one re-check step. 8 is the hard ceiling.
MAX_STEPS_CHECKED = MAX_STEPS_PER_TASK + 2
MAX_REPAIRS = 1  # one repair attempt, then re-check once; still failing -> human

CHECK_NAMES = (
    "requirement_in_jd",
    "evidence_supports_requirement",
    "evidence_verbatim",
    "gap_is_real",
    "no_orphan_requirements",
)


def check_materials(job: dict, draft: dict,
                    chunks: list | None = None) -> dict:
    """Independently verify a materials draft against the raw inputs.

    Re-derives the JD's requirements (strong tier) and the resume
    evidence (retrieve over the chunks) from scratch — it never trusts
    the generator's requirement list or its chunk choices. Returns a
    structured report: per-check results plus the flat failure list
    the repair step consumes.
    """
    chunks = chunks if chunks is not None else load_resume_chunks()
    by_id = {c.id: c for c in chunks}
    jd_requirements = extract_requirements(job, tier="strong")
    points = draft.get("points", [])
    gaps = draft.get("gaps", [])

    checks: list[dict] = []
    failures: list[dict] = []

    def _record(check: str, item: str, passed: bool, reason: str) -> None:
        checks.append({"check": check, "item": item,
                       "passed": passed, "reason": reason})
        if not passed:
            failures.append({"check": check, "item": item, "reason": reason})

    # 1. every point's requirement must be a requirement of this JD
    for p in points:
        req = p.get("requirement", "")
        _record("requirement_in_jd", req, req in jd_requirements,
                "requirement not found in JD" if req not in jd_requirements
                else "requirement appears in JD")
    # 2. the cited chunk must actually cover the requirement
    for p in points:
        req, chunk = p.get("requirement", ""), by_id.get(p.get("evidence_chunk_id", ""))
        if chunk is None:
            _record("evidence_supports_requirement", req, False, "unknown chunk id")
        else:
            _record("evidence_supports_requirement", req, chunk.covers(req),
                    "cited chunk does not cover this requirement"
                    if not chunk.covers(req) else "cited chunk covers requirement")
    # 3. the quote must be verbatim in the cited chunk
    for v in verify_grounding(points, chunks):
        _record("evidence_verbatim", v["point"], False, v["why"])
    for p in points:
        req = p.get("requirement", "")
        if not any(f["check"] == "evidence_verbatim" and f["item"] == req
                   for f in failures):
            _record("evidence_verbatim", req, True, "quote is verbatim in chunk")
    # 4. every gap must genuinely lack resume evidence
    for g in gaps:
        found = retrieve(g, chunks)
        _record("gap_is_real", g, found is None,
                f"resume chunk {found.id} covers this requirement"
                if found is not None else "no resume chunk covers it")
    # 5. every JD requirement must appear as a point or a gap
    covered = {p.get("requirement", "") for p in points} | set(gaps)
    for req in jd_requirements:
        _record("no_orphan_requirements", req, req in covered,
                "requirement neither a point nor a gap"
                if req not in covered else "requirement accounted for")

    return {"passed": not failures, "checks": checks, "failures": failures,
            "num_checks": len(checks), "num_failed": len(failures)}


def repair_materials(job: dict, draft: dict, failures: list[dict],
                     chunks: list | None = None) -> dict:
    """Fix a draft from the checker's failure list, one failure at a time.

    Deterministic and minimal: drop invented points, re-cite the right
    chunk (or demote to a gap when no chunk exists), turn fake gaps
    into points, add orphans as points or gaps. Returns the repaired
    draft plus the list of repairs applied, so the audit trail shows
    what the checker changed and why.
    """
    chunks = chunks if chunks is not None else load_resume_chunks()
    points = [dict(p) for p in draft.get("points", [])]
    gaps = list(draft.get("gaps", []))
    repairs: list[dict] = []

    def _point_for(req: str) -> dict | None:
        chunk = retrieve(req, chunks)
        if chunk is None:
            return None
        return {"requirement": req, "evidence_chunk_id": chunk.id,
                "evidence_quote": chunk.text,
                "point": f"For {req}: {chunk.text}"}

    for f in failures:
        check, item = f["check"], f["item"]
        if check == "requirement_in_jd":
            points = [p for p in points if p.get("requirement") != item]
            repairs.append({"failure": check, "item": item, "fix": "dropped point"})
        elif check in ("evidence_supports_requirement", "evidence_verbatim"):
            points = [p for p in points if p.get("requirement") != item]
            fixed = _point_for(item)
            if fixed is not None:
                points.append(fixed)
                repairs.append({"failure": check, "item": item,
                                "fix": f"re-cited chunk {fixed['evidence_chunk_id']}"})
            elif item not in gaps:
                gaps.append(item)
                repairs.append({"failure": check, "item": item,
                                "fix": "no covering chunk, moved to gaps"})
        elif check == "gap_is_real":
            gaps = [g for g in gaps if g != item]
            fixed = _point_for(item)
            if fixed is not None:
                points.append(fixed)
                repairs.append({"failure": check, "item": item,
                                "fix": f"fake gap, made point from {fixed['evidence_chunk_id']}"})
        elif check == "no_orphan_requirements":
            fixed = _point_for(item)
            if fixed is not None:
                points.append(fixed)
                repairs.append({"failure": check, "item": item,
                                "fix": f"added point from {fixed['evidence_chunk_id']}"})
            elif item not in gaps:
                gaps.append(item)
                repairs.append({"failure": check, "item": item,
                                "fix": "added to gaps (no resume evidence)"})
    repaired = {"job_company": draft.get("job_company", str(job.get("company", "?"))),
                "points": points, "gaps": gaps}
    return {"draft": repaired, "repairs": repairs}


def check_and_repair(job: dict, draft: dict, chunks: list | None = None,
                     budget: RunBudget | None = None,
                     max_repairs: int = MAX_REPAIRS) -> dict:
    """One check -> (at most one repair + re-check) round, with budget.

    Returns the draft plus the checker report. If the draft still fails
    after the repair, it is flagged needs_human=True — the checker
    never silently ships a failing draft and never repairs twice.
    """
    chunks = chunks if chunks is not None else load_resume_chunks()
    budget = budget if budget is not None else RunBudget(max_steps=MAX_STEPS_CHECKED)
    report = check_materials(job, draft, chunks=chunks)
    budget.step("check_materials", "cheap",
                json.dumps(draft), json.dumps(report["failures"]))
    repairs: list[dict] = []
    if not report["passed"] and max_repairs > 0:
        fixed = repair_materials(job, draft, report["failures"], chunks=chunks)
        repairs = fixed["repairs"]
        budget.step("repair_materials", "cheap",
                    json.dumps(report["failures"]), json.dumps(repairs))
        draft = fixed["draft"]
        report = check_materials(job, draft, chunks=chunks)
        budget.step("recheck_materials", "cheap",
                    json.dumps(draft), json.dumps(report["failures"]))
    return {"draft": draft, "checker": report, "repairs": repairs,
            "checker_passed": report["passed"],
            "needs_human": not report["passed"],
            "steps_used": budget.steps, "token_log": budget.log,
            "total_cost_usd": budget.total_cost_usd}


def generate_materials_checked(job: dict, chunks: list | None = None,
                               min_requirements: int = 4,
                               max_repairs: int = MAX_REPAIRS,
                               budget: RunBudget | None = None) -> dict:
    """Full checked pipeline: cascade generation, then check-and-repair.

    Worst case steps: 3 cascade extracts + generate + verify (5) +
    check + repair + re-check (3) = 8 = MAX_STEPS_CHECKED. The draft
    carries the cascade audit trail AND the checker report, so a human
    can see both where the extraction stopped and what the checker
    caught or cleared.
    """
    chunks = chunks if chunks is not None else load_resume_chunks()
    budget = budget if budget is not None else RunBudget(max_steps=MAX_STEPS_CHECKED)
    draft = generate_materials_cascade(job, chunks=chunks,
                                       min_requirements=min_requirements,
                                       budget=budget)
    cascade = draft.pop("cascade")
    checked = check_and_repair(job, draft, chunks=chunks,
                               budget=budget, max_repairs=max_repairs)
    result = {**checked["draft"], "cascade": cascade,
              "checker": checked["checker"], "repairs": checked["repairs"],
              "checker_passed": checked["checker_passed"],
              "needs_human": checked["needs_human"],
              "extract_tier": draft.get("extract_tier"),
              "steps_used": budget.steps, "token_log": budget.log,
              "total_cost_usd": budget.total_cost_usd,
              "cost_per_grounded_point_usd": (
                  round(budget.total_cost_usd / len(checked["draft"]["points"]), 6)
                  if checked["draft"]["points"] else None)}
    return result
