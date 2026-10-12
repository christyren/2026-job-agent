"""Day-of formal attempt mode (2026-10-12 iteration).

Why this exists:
  Milestones 1-6 are done, and 2026-10-09/10/11 were practice /
  health-check days. TODAY (2026-10-12) is different: it is the
  formal Stripe HackerRank day, deadline about 2026-10-13 morning.
  The old sim_drill instruction -- "do NOT press the real Start
  today, formal stays on 2026-10-12" -- is now stale. Reusing it
  blindly today would give exactly the wrong instruction.

  This module teaches one distinction:

  1. MODE -- same checks, different day, different instruction.
     Practice mode and formal mode share run_preflight(); they
     differ only in what the human is told to do. The mode is
     explicit data (an argument), never guessed from the clock
     inside the check itself. This is the same idea as a feature
     flag / environment switch in production code.

  2. VERIFICATION BOUNDARY -- code can verify code, it cannot
     verify a human's room. run_preflight() proves precision /
     duplicate / empty / timebox against the real template. It
     cannot prove the cat was fed, the phone is silent, or Chrome
     is incognito. Those live in HUMAN_CHECKS as an explicit list:
     can_start() returns the missing ones by name instead of
     pretending "all_ok" covers them. Never let an automated
     green light silently claim a manual check it did not do.

  Start gate: start only when BOTH hold --
    code_ok  (deterministic preflight all_ok)
    AND every HUMAN_CHECKS id is confirmed by the human.
  The gate is a pure function so it can be unit-tested without
  touching the real assessment. This module never touches the
  real assessment itself.
"""
from __future__ import annotations

from job_agent.sim_drill import RETRO_QUESTIONS, format_timebox, run_preflight

FORMAL_DATE = "2026-10-12"
DEADLINE_NOTE = "about 2026-10-13 morning; leave a buffer, do not start at the deadline"

# (id, label) -- ids are what can_start() consumes; labels are for humans.
# Source: goals/1-sde2/files/stripe-hackerrank-dayof-2026-10-12.md
HUMAN_CHECKS: tuple[tuple[str, str], ...] = (
    ("cat_fed_noon", "Cat fed the noon portion first, so feeding does not interrupt the run"),
    ("lunch_and_rest", "Lunch done: low-carb, high-protein, familiar food only; rested 20-30 min after"),
    ("chrome_clean", "Chrome incognito, third-party dark-mode / extensions off, only the exam window open"),
    ("phone_silent", "Phone on silent / do-not-disturb"),
    ("desk_ready", "Water and charger in place; 60 uninterrupted minutes protected"),
    ("faq_scanned", "Candidate FAQ from the invitation email scanned before pressing Start"),
    ("handwritten", "Ready to write everything by hand; no pasting external code blocks"),
)

HUMAN_CHECK_IDS: tuple[str, ...] = tuple(cid for cid, _ in HUMAN_CHECKS)


def missing_human_checks(confirmed) -> list[str]:
    """Which required human checks are NOT confirmed yet?"""
    done = set(confirmed or [])
    return [cid for cid in HUMAN_CHECK_IDS if cid not in done]


def can_start(code_ok: bool, confirmed) -> dict:
    """Pure start gate. Returns go / missing lists, never raises.

    go is True only if code preflight is green AND no human
    check is missing. If code is red, 'code_preflight' is listed
    first in missing -- fix the template outside the timer.
    """
    missing = missing_human_checks(confirmed)
    if not code_ok:
        missing = ["code_preflight"] + missing
    return {"go": not missing, "missing": missing}


def run_dayof(confirmed=None) -> dict:
    """Formal-day bundle: reused code preflight + human gate.

    Deliberately does NOT reuse sim_drill's instruction string:
    that string was written for practice days. Today's
    instruction is built here, from FORMAL_DATE.
    """
    preflight = run_preflight()
    gate = can_start(preflight["all_ok"], confirmed or [])
    return {
        "date": FORMAL_DATE,
        "mode": "formal",
        "code_preflight": preflight,
        "human_checks": [
            {"id": cid, "label": label, "confirmed": cid in set(confirmed or [])}
            for cid, label in HUMAN_CHECKS
        ],
        "gate": gate,
        "instruction": (
            f"Today ({FORMAL_DATE}) IS the formal attempt day. "
            "Start only in a good-state window (after lunch + rest, "
            "with buffer before the deadline), and only when the "
            "gate below says go. Once Start is pressed, the 60 "
            "minutes cannot be paused."
        ),
        "deadline_note": DEADLINE_NOTE,
        "retro_questions": list(RETRO_QUESTIONS),
    }


def format_dayof(result: dict) -> str:
    lines = [f"day-of mode: formal attempt on {result['date']}"]
    lines.append(f"  {result['instruction']}")
    lines.append(f"  deadline: {result['deadline_note']}")
    lines.append("code preflight (automated, verified by code):")
    for name, ok in result["code_preflight"]["checks"].items():
        lines.append(f"  [{'OK' if ok else 'FAIL'}] {name}")
    lines.append(f"  all_ok: {result['code_preflight']['all_ok']}")
    lines.append("human checks (NOT verified by code -- confirm each by name):")
    for check in result["human_checks"]:
        mark = "x" if check["confirmed"] else " "
        lines.append(f"  [{mark}] {check['id']}: {check['label']}")
    gate = result["gate"]
    if gate["go"]:
        lines.append("gate: GO -- code green and all human checks confirmed")
    else:
        lines.append(f"gate: NOT YET -- missing: {', '.join(gate['missing'])}")
    lines.append("")
    lines.append(format_timebox())
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_dayof(run_dayof()))
