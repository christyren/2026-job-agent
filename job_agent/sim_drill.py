"""Simulation preflight + timebox drill (2026-10-09 iteration).

Why this exists:
  Milestones 1-6 are done. Today is NOT a new feature day — it is the
  full 60-minute Stripe-style simulation day (practice only, never the
  real Start button). The risk today is not missing knowledge, it is
  process: drifting past a phase, forgetting an edge check, or
  discovering a broken environment two minutes into the timer.

  This module turns the prep pack's timebox into code:

  1. TIMEBOX — the five phases, as data. phase_at(minute) answers
     "what should I be doing right now?" so the clock, not anxiety,
     decides when to move on. This is Planning applied to 60 minutes:
     the plan is written BEFORE execution and checked at checkpoints,
     exactly like milestone 6's planner, only the executor is a human.

  2. run_preflight() — a deterministic environment + template check
     that must be green BEFORE any timed run: Python works, the
     three-stage template imports, and the three edge cases that
     matter today (money precision / duplicate / empty) actually pass
     against the real pipeline. No LLM, no guessing — same rule as
     milestone 5's daily summary: numbers you will act on must be
     recomputable by code.

  3. RETRO_QUESTIONS — the debrief is deliberately capped at two
     questions (did Part 1 leave an extension point? did I misread
     the input format?). A simulation you cannot debrief in five
     minutes teaches nothing; a debrief with ten questions teaches
     nothing either, because none of them stick.

Formal attempt stays scheduled for 2026-10-12; deadline is about
2026-10-13 morning. This module never touches the real assessment.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from job_agent.multistage import run_pipeline

TOTAL_MINUTES = 60
FORMAL_DATE = "2026-10-12"  # preferred day for the real attempt
DEADLINE_NOTE = "about 2026-10-13 morning; do not leave it to that day"


@dataclass(frozen=True)
class Phase:
    name: str
    start: int  # inclusive, minutes from Start
    end: int  # exclusive, except the last phase includes 60
    job: str
    checkpoint: str  # what must be true when this phase ends


TIMEBOX: tuple[Phase, ...] = (
    Phase(
        "read_all", 0, 5,
        "Read every visible part + the input/output format. Fix the "
        "data structure (usually dict + list) before writing code.",
        "I can state the input format and the Part-1 output format "
        "without looking back.",
    ),
    Phase(
        "part1", 5, 25,
        "Finish Part 1 end to end and pass its sample. Write it as "
        "parse / calc / output from the first line, even if it feels "
        "slower — the split IS the extension point for later parts.",
        "Part 1 sample passes and the three stages are separate "
        "functions, not one blob.",
    ),
    Phase(
        "later_parts", 25, 45,
        "Advance the later parts. For each new rule, decide first: "
        "is this a parse change, a calc change, or an output change? "
        "Touch only that stage.",
        "Each finished part still passes its own sample; no earlier "
        "part was rewritten wholesale.",
    ),
    Phase(
        "edge_checks", 45, 55,
        "Run the edge checklist only: empty input, duplicate record, "
        "money precision, sort order, delimiter / case details.",
        "Precision, duplicate, and empty have each been tried at "
        "least once, by hand or by test.",
    ),
    Phase(
        "fix_only", 55, 61,
        "Fix only bugs you can name precisely. No refactoring, no "
        "new features, no 'while I am here' cleanups.",
        "Every finished part is in a submittable state.",
    ),
)

RETRO_QUESTIONS: tuple[str, ...] = (
    "Did Part 1 leave an extension point? (parse / calc / output "
    "split — if a later part forced a rewrite, name the exact line "
    "where the blob started.)",
    "Did I misread the input format anywhere? (delimiter, field "
    "order, amount format, sort key — name the detail, not 'I was "
    "careless'.)",
)


def phase_at(minute: int) -> Phase:
    """Which phase owns this minute? Minutes outside 0..60 clamp to
    the nearest phase, so a nervous glance at minute 61 still gets
    an answer ('fix_only / submit state') instead of an exception."""
    if minute < 0:
        return TIMEBOX[0]
    for phase in TIMEBOX:
        if phase.start <= minute < phase.end:
            return phase
    return TIMEBOX[-1]


def run_preflight() -> dict:
    """Deterministic pre-timer checks. Returns a plain dict so the
    result can be printed, asserted in tests, or pasted into notes.

    Checks (all against the REAL template, not a copy):
    - python_ok: interpreter is Python 3
    - precision: 0.10 + 0.20 renders as $0.30, not $0.30000000000000004
    - duplicate: a repeated txn_id is counted once (first wins)
    - empty: empty input renders the explicit '(no transactions)'
    - timebox: phases cover 0..60 with no gaps
    """
    checks: dict[str, bool] = {}
    checks["python_ok"] = sys.version_info.major == 3
    checks["precision"] = (
        run_pipeline("t1,alice,0.10\nt2,alice,0.20\n") == "alice: $0.30"
    )
    checks["duplicate"] = (
        run_pipeline("t1,bob,1.00\nt1,bob,999.99\n") == "bob: $1.00"
    )
    checks["empty"] = run_pipeline("") == "(no transactions)"
    covered = []
    for phase in TIMEBOX:
        covered.extend(range(phase.start, min(phase.end, TOTAL_MINUTES)))
    checks["timebox_covers_60"] = sorted(set(covered)) == list(range(60))
    return {
        "all_ok": all(checks.values()),
        "checks": checks,
        "formal_date": FORMAL_DATE,
        "deadline_note": DEADLINE_NOTE,
        "instruction": (
            "Practice run only — do NOT press the real Start today. "
            f"Formal attempt stays on {FORMAL_DATE}."
        ),
    }


def format_preflight(result: dict) -> str:
    lines = ["preflight (all must be OK before any timed run):"]
    for name, ok in result["checks"].items():
        lines.append(f"  [{'OK' if ok else 'FAIL'}] {name}")
    lines.append(f"  all_ok: {result['all_ok']}")
    lines.append(f"  {result['instruction']}")
    lines.append(f"  deadline: {result['deadline_note']}")
    return "\n".join(lines)


def format_timebox() -> str:
    lines = [f"timebox ({TOTAL_MINUTES} min, hard stops — move on when the phase ends):"]
    for p in TIMEBOX:
        end = min(p.end, TOTAL_MINUTES)
        lines.append(f"  {p.start:>2}-{end:<2} {p.name}: {p.job}")
        lines.append(f"       checkpoint: {p.checkpoint}")
    lines.append("retro (only these two, five minutes, then stop):")
    for i, q in enumerate(RETRO_QUESTIONS, start=1):
        lines.append(f"  {i}. {q}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_preflight(run_preflight()))
    print()
    print(format_timebox())
