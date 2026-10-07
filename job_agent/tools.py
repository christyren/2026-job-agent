"""Tools for the job-search agent.

Key idea (Tool Use): the LLM does NOT execute code itself.
It only *decides* to call a tool and fills in the arguments.
Our program executes the function and feeds the result back.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_preferences() -> dict:
    """Return the user's job-search criteria (read from a local JSON file)."""
    with open(DATA_DIR / "preferences.json", encoding="utf-8") as f:
        return json.load(f)


def estimate_cost(
    num_jobs: int,
    input_tokens_per_job: int = 3000,
    output_tokens_per_job: int = 500,
    input_price_per_million: float = 2.0,
    output_price_per_million: float = 10.0,
) -> dict:
    """Estimate LLM API cost. Prices are examples — always re-check the vendor page.

    Teaching note: cost = tokens / 1_000_000 * price_per_million.
    The $2 / 1M input tokens figure was today's (2026-10-02) news floor price,
    NOT a permanent price for any specific model.
    """
    total_input = num_jobs * input_tokens_per_job
    total_output = num_jobs * output_tokens_per_job
    cost = (
        total_input / 1_000_000 * input_price_per_million
        + total_output / 1_000_000 * output_price_per_million
    )
    return {
        "num_jobs": num_jobs,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "estimated_cost_usd": round(cost, 4),
    }


def fetch_jobs(source: str = "sample", board: str = "") -> dict:
    """Milestone 2 tool: fetch -> normalize -> deduplicate -> store (idempotent).

    Run it twice with the same source: the second run must report new=0.
    That is the pipeline's idempotency check, not a bug.
    """
    from .fetchers import fetch_greenhouse, fetch_sample
    from .jobs import deduplicate, merge_into_store, normalize_job, utc_now_iso

    if source == "greenhouse":
        if not board:
            raise ValueError("greenhouse source needs a board token, e.g. board='stripe'")
        raw_jobs = fetch_greenhouse(board)
    elif source == "sample":
        raw_jobs = fetch_sample()
    else:
        raise ValueError(f"unknown source: {source} (use 'sample' or 'greenhouse')")

    fetched_at = utc_now_iso()
    normalized = [normalize_job(r, source=source, fetched_at=fetched_at) for r in raw_jobs]
    unique, skipped_in_batch = deduplicate(normalized)
    summary = merge_into_store(unique)
    return {
        "source": source,
        "fetched": len(raw_jobs),
        "duplicates_in_batch": skipped_in_batch,
        **summary,
        "fetched_at": fetched_at,
        "store": "data/jobs.json",
    }


def score_jobs(source: str = "eval") -> dict:
    """Milestone 3 tool: score jobs through hard rules -> structured scorer -> threshold.

    source='eval'  -> score the 10 hand-labelled cases in data/eval_jobs.json
                      and report accuracy (this is the eval run).
    source='store' -> score the jobs already in data/jobs.json. Those records
                      have no JD text / salary, so most will honestly land in
                      needs_review — that is the threshold working, not a bug.
    Nothing here ever submits an application; needs_review means a human decides.
    """
    from collections import Counter

    from .jobs import load_store
    from .scoring import score_job

    prefs = get_preferences()
    if source == "eval":
        from .evaluate import run_eval

        report = run_eval(prefs=prefs)
        return {
            "source": "eval",
            "total": report["total"],
            "accuracy": report["accuracy"],
            "mismatches": report["mismatches"],
            "verdicts": dict(Counter(r["got"] for r in report["rows"])),
        }
    if source == "store":
        rows = [
            {"id": j.get("id"), "company": j.get("company"),
             **score_job(j, prefs).to_dict()}
            for j in load_store()
        ]
        return {"source": "store", "scored": len(rows),
                "verdicts": dict(Counter(r["verdict"] for r in rows)), "rows": rows}
    raise ValueError(f"unknown source: {source} (use 'eval' or 'store')")


def draft_materials(source: str = "eval", case_id: str = "") -> dict:
    """Milestone 4 tool: RAG-tailored application points from the resume.

    source='eval' (no case_id) -> run the cheap-vs-strong extraction-tier
    comparison over data/eval_materials.json and report recall / gaps /
    grounded rate / cost per grounded point for each tier.
    case_id='eval-m01' etc. -> generate one draft for that labelled case:
    points grounded in resume chunks + explicit gaps. Draft only —
    a human reviews and approves before anything is ever submitted.
    """
    from .evaluate import load_material_cases, run_materials_eval
    from .materials import generate_materials

    if case_id:
        cases = {c["id"]: c for c in load_material_cases()}
        if case_id not in cases:
            raise ValueError(f"unknown case_id: {case_id} (have {sorted(cases)})")
        case = cases[case_id]
        job = {k: v for k, v in case.items()
               if k not in ("expected_requirements", "expected_gaps", "note")}
        draft = generate_materials(job)
        return {"case_id": case_id, **draft}
    if source == "eval":
        return {"source": "eval", **run_materials_eval()}
    raise ValueError(f"unknown source: {source} (use 'eval', or pass case_id)")


def track_applications(action: str = "summary", app_id: str = "",
                         to: str = "", note: str = "") -> dict:
    """Milestone 5 tool: application tracking over the durable store.

    action='summary'    -> daily summary (counts, needs_review, stale,
                           interview list, one-line cost accounting).
    action='transition' -> move one application along a legal FSM edge
                           (app_id + to required); illegal moves raise.
    State lives in data/applications.json and is re-read from disk on
    every call — the agent's memory is never the source of truth.
    Nothing here submits or withdraws anything in the real world.
    """
    from .tracking import daily_summary, transition_in_store

    if action == "summary":
        return daily_summary()
    if action == "transition":
        if not app_id or not to:
            raise ValueError("transition needs app_id and to")
        return transition_in_store(app_id, to, note=note)
    raise ValueError(f"unknown action: {action} (use 'summary' or 'transition')")


def plan_today() -> dict:
    """Milestone 6 tool: build today's ordered plan (typed decisions).

    Reads the durable application store (milestone 5) plus long-term
    memory (data/agent_memory.json) from disk on every call, emits an
    ordered list of typed decisions, and downgrades any decision with
    confidence < 0.7 to needs_review for a human. The planner only
    PROPOSES — it executes nothing and never submits anything.
    """
    from .planning import load_memory, plan_today as _build_plan

    plan = _build_plan()
    return {
        "decisions": plan,
        "top_action": plan[0]["action"] if plan else "wait",
        "needs_human_count": sum(1 for d in plan if d["requires_human"]),
        "memory_entries_used": len(load_memory()),
        "store": "data/applications.json + data/agent_memory.json",
    }


# Registry: name -> python function. The agent loop dispatches through this dict.
TOOL_FUNCTIONS = {
    "get_preferences": get_preferences,
    "estimate_cost": estimate_cost,
    "fetch_jobs": fetch_jobs,
    "score_jobs": score_jobs,
    "draft_materials": draft_materials,
    "track_applications": track_applications,
    "plan_today": plan_today,
}

# Schemas in Anthropic Messages API format: name + description + input_schema (JSON Schema).
# The *description* matters a lot: the LLM decides whether/when to call a tool based on it.
TOOL_SCHEMAS = [
    {
        "name": "get_preferences",
        "description": "Get the user's job-search criteria: target roles, locations, minimum base salary, and excluded companies. Call this before judging whether a job is a good fit.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "estimate_cost",
        "description": "Estimate the total LLM API cost for processing a number of job postings, given average input/output tokens per job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "num_jobs": {"type": "integer", "description": "How many jobs to process"},
                "input_tokens_per_job": {"type": "integer"},
                "output_tokens_per_job": {"type": "integer"},
            },
            "required": ["num_jobs"],
        },
    },
    {
        "name": "fetch_jobs",
        "description": "Fetch job postings from a source, deduplicate them, and store them locally in data/jobs.json with source URL and fetch timestamp. Use source='sample' for the offline sample file, or source='greenhouse' with a board token for a company's public Greenhouse board. Safe to run repeatedly: duplicates are skipped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["sample", "greenhouse"], "description": "Where to fetch jobs from"},
                "board": {"type": "string", "description": "Greenhouse board token, required when source='greenhouse'"},
            },
            "required": [],
        },
    },
    {
        "name": "score_jobs",
        "description": "Score job postings against the user's criteria using hard rules first, then structured scoring, downgrading low-confidence results to needs_review for human decision. Use source='eval' to run the labelled evaluation set and report accuracy, or source='store' to score jobs already stored in data/jobs.json. Never submits an application.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["eval", "store"], "description": "Which jobs to score: the labelled eval set, or the stored jobs"},
            },
            "required": [],
        },
    },
    {
        "name": "track_applications",
        "description": "Track job applications in the durable store (data/applications.json) using a finite state machine. Use action='summary' for the daily summary: counts by status, applications parked in needs_review for a human, stale applied/replied applications, interviews in progress, and a one-line tasks/steps/cost accounting that flags over-cap runs for human review. Use action='transition' with app_id and to to record a legal status move; illegal moves are refused. Never submits or withdraws an application in the real world.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["summary", "transition"], "description": "What to do: daily summary, or record one status transition"},
                "app_id": {"type": "string", "description": "Application id, required for action='transition'"},
                "to": {"type": "string", "description": "New status, required for action='transition'"},
                "note": {"type": "string", "description": "Optional note stored in the transition history"},
            },
            "required": [],
        },
    },
    {
        "name": "plan_today",
        "description": "Build today's ordered action plan for the job search as typed decisions (action enum + target + confidence + reasons). Reads the durable application store and long-term memory (human decisions that still bind, e.g. paused companies) from disk; decisions below 0.7 confidence are returned as needs_review for a human. Use when the user asks what to do next, for today's plan, or for priorities. Proposes only — never executes, submits, or messages anyone.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "draft_materials",
        "description": "Draft tailored application points for a job, grounded only in retrieved resume chunks (RAG): every point cites its resume evidence, and requirements with no resume evidence are reported as gaps instead of being invented. With source='eval' and no case_id, compares cheap vs strong extraction tiers (recall, gap accuracy, grounded rate, cost per grounded point). With a case_id like 'eval-m01', generates one draft. Drafts only — never submits an application.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["eval"], "description": "Use 'eval' for the tier-comparison evaluation set"},
                "case_id": {"type": "string", "description": "Optional labelled case id (e.g. 'eval-m01') to generate a single draft for"},
            },
            "required": [],
        },
    },
]


def dispatch_tool(name: str, args: dict):
    """Execute one tool call. Raises KeyError for unknown tools (agent must handle it)."""
    if name not in TOOL_FUNCTIONS:
        raise KeyError(f"unknown tool: {name}")
    return TOOL_FUNCTIONS[name](**args)
