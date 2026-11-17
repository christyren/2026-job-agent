"""Iteration 2026-11-17: run traces + cost-cap governance.

So far every pipeline keeps its own cost in a RunBudget log — but the
log lives only in the return value of that one call. If a run goes
wrong (or expensive) overnight, there is nothing to open afterwards
and ask "what happened, step by step, and what did it cost?". That
question is what observability answers, and LangSmith's tracing is
the reference shape for it: every run gets a trace id, every step is
a span with its own tokens / cost / latency, traces are stored so a
human can audit them later, and a cost alert fires when one run gets
too expensive (LangSmith write-ups commonly use $0.05 per trace as
the alert line — our default cap below is set just above it).

This module is the project's own minimal version of that shape:

- RunTrace: one run = one trace_id, a list of steps (name, tier,
  input/output tokens, cost, cumulative cost), a status, and a
  needs_human flag. Saved append-only to data/traces.jsonl, so the
  audit trail survives the process that produced it (same "durable
  state, not conversation memory" rule as milestone 5).
- CostCappedBudget: a RunBudget that also enforces a HARD per-run
  cost ceiling. The step cap from milestone 1 stops runaway loops;
  the cost cap stops runaway SPENDING — a loop that stays under the
  step cap can still burn money if every step is strong-tier. When
  a step pushes the running total over the cap, it raises
  BudgetExceeded *after* recording that step, so the trace shows
  exactly which step crossed the line.
- run_traced_checked: the checked materials pipeline (milestone 4
  cascade + 2026-11-16 checker) wrapped in a trace. On success it
  returns the draft plus its trace; on over-cap it returns NO draft
  at all — a run that blew its budget does not get to ship a half-
  finished draft quietly — plus over_cap=True and needs_human=True.
- trace_summary: the daily rollup over saved traces (runs, total
  cost, over-cap runs, most expensive run) — the same "one line of
  accounting, over-cap flagged for a human" shape as the milestone-5
  daily summary, but over runs instead of applications.

Nothing here submits an application; a traced draft is still a draft
for human review — the trace only makes the cost of producing it
visible and bounded.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .checker import MAX_STEPS_CHECKED, generate_materials_checked
from .materials import RunBudget

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRACE_PATH = DATA_DIR / "traces.jsonl"

# Default per-run cost ceiling. A normal checked run costs ~$0.055
# (2026-11-16 measurement), so $0.10 is ~2x a healthy run: it lets
# honest work through and only trips on something genuinely wrong
# (a repair loop, a tier misconfiguration, a runaway cascade).
# The $0.05 "alert" line mirrors the per-trace cost alert used in
# LangSmith write-ups; the cap is a hard stop, not just an alert.
DEFAULT_MAX_RUN_COST_USD = 0.10
TRACE_COST_ALERT_USD = 0.05


class BudgetExceeded(RuntimeError):
    """Raised when one run's cumulative cost crosses its hard cap.

    The step that crossed is already recorded in the budget log, so
    the trace can show exactly where the money went.
    """


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CostCappedBudget(RunBudget):
    """RunBudget + a hard per-run cost ceiling.

    Same step cap as the parent (a runaway loop still stops), plus:
    after every step, if total cost > max_cost_usd, raise
    BudgetExceeded. The check happens AFTER the step is logged, so
    the offending step stays in the log for the audit trail.
    """

    def __init__(self, max_cost_usd: float = DEFAULT_MAX_RUN_COST_USD,
                 max_steps: int = MAX_STEPS_CHECKED):
        super().__init__(max_steps=max_steps)
        self.max_cost_usd = max_cost_usd

    def step(self, name: str, tier: str, input_text: str, output_text: str) -> dict:
        entry = super().step(name, tier, input_text, output_text)
        if self.total_cost_usd > self.max_cost_usd:
            raise BudgetExceeded(
                f"run cost ${self.total_cost_usd:.6f} exceeded cap "
                f"${self.max_cost_usd:.6f} at step {entry['step']} ({name!r})")
        return entry


@dataclass
class RunTrace:
    """One traced run: id, task, steps, status, cost.

    Status is one of: "ok" (finished inside the cap), "over_cap"
    (stopped by the cost ceiling — draft NOT shipped), "error"
    (any other failure). A trace is only ever appended to, never
    edited after it is saved — the same append-only rule as the
    milestone-5 application history.
    """
    task: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = ""
    status: str = "ok"
    error: str = ""
    steps: list[dict] = field(default_factory=list)
    needs_human: bool = False

    @property
    def total_cost_usd(self) -> float:
        return round(sum(s["cost_usd"] for s in self.steps), 6)

    @property
    def num_steps(self) -> int:
        return len(self.steps)

    @property
    def over_alert(self) -> bool:
        """Cost above the $0.05 alert line, even if still under the cap."""
        return self.total_cost_usd > TRACE_COST_ALERT_USD

    def record_budget(self, budget: RunBudget) -> None:
        """Mirror a RunBudget log into trace steps, with running totals."""
        cumulative = 0.0
        for entry in budget.log:
            cumulative = round(cumulative + entry["cost_usd"], 6)
            self.steps.append({
                "step": entry["step"], "name": entry["name"],
                "tier": entry["tier"],
                "input_tokens": entry["input_tokens"],
                "output_tokens": entry["output_tokens"],
                "cost_usd": entry["cost_usd"],
                "cumulative_cost_usd": cumulative,
            })

    def finish(self, status: str, error: str = "") -> None:
        self.status = status
        self.error = error
        self.finished_at = utc_now_iso()
        self.needs_human = status in ("over_cap", "error")

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id, "task": self.task,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "status": self.status, "error": self.error,
            "steps": self.steps, "num_steps": self.num_steps,
            "total_cost_usd": self.total_cost_usd,
            "over_alert": self.over_alert, "needs_human": self.needs_human,
        }

    def save(self, path: Path = TRACE_PATH) -> Path:
        """Append this trace as one JSON line. Creates the file on first use."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")
        return path


def load_traces(path: Path = TRACE_PATH) -> list[dict]:
    """Read all saved traces (oldest first). Missing file = no runs yet."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def trace_summary(traces: list[dict] | None = None,
                  path: Path = TRACE_PATH) -> dict:
    """Daily rollup over saved traces: runs, cost, over-cap, most expensive.

    Mirrors the milestone-5 daily summary shape (counts + one cost
    line + an explicit over-cap flag for a human), but over runs.
    """
    traces = traces if traces is not None else load_traces(path)
    total_cost = round(sum(t["total_cost_usd"] for t in traces), 6)
    over_cap = [t for t in traces if t["status"] == "over_cap"]
    most_expensive = max(traces, key=lambda t: t["total_cost_usd"], default=None)
    return {
        "runs": len(traces),
        "total_cost_usd": total_cost,
        "over_cap_runs": len(over_cap),
        "needs_human_runs": sum(1 for t in traces if t["needs_human"]),
        "over_cap_trace_ids": [t["trace_id"] for t in over_cap],
        "most_expensive": (
            {"trace_id": most_expensive["trace_id"],
             "task": most_expensive["task"],
             "cost_usd": most_expensive["total_cost_usd"],
             "status": most_expensive["status"]}
            if most_expensive else None),
        "avg_cost_usd": round(total_cost / len(traces), 6) if traces else 0.0,
    }


def run_traced_checked(job: dict, chunks=None,
                       max_cost_usd: float = DEFAULT_MAX_RUN_COST_USD,
                       trace_path: Path = TRACE_PATH,
                       save: bool = True) -> dict:
    """Run the checked materials pipeline inside one trace.

    Returns {"trace": ..., "draft": result | None, "over_cap": bool,
    "needs_human": bool}. On over-cap the draft is None: a run that
    blew its budget ships nothing, and the trace (saved to disk)
    shows which step crossed the cap.
    """
    trace = RunTrace(task=f"checked materials for {job.get('company', '?')}")
    budget = CostCappedBudget(max_cost_usd=max_cost_usd)
    try:
        result = generate_materials_checked(job, chunks=chunks, budget=budget)
    except BudgetExceeded as exc:
        trace.record_budget(budget)
        trace.finish("over_cap", error=str(exc))
        if save:
            trace.save(trace_path)
        return {"trace": trace.to_dict(), "draft": None,
                "over_cap": True, "needs_human": True, "error": str(exc)}
    except Exception as exc:  # any other failure: trace it, ship nothing
        trace.record_budget(budget)
        trace.finish("error", error=f"{type(exc).__name__}: {exc}")
        if save:
            trace.save(trace_path)
        return {"trace": trace.to_dict(), "draft": None,
                "over_cap": False, "needs_human": True,
                "error": f"{type(exc).__name__}: {exc}"}
    trace.record_budget(budget)
    trace.finish("ok")
    if save:
        trace.save(trace_path)
    return {"trace": trace.to_dict(), "draft": result,
            "over_cap": False, "needs_human": False, "error": ""}
