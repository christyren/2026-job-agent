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


# Registry: name -> python function. The agent loop dispatches through this dict.
TOOL_FUNCTIONS = {
    "get_preferences": get_preferences,
    "estimate_cost": estimate_cost,
    "fetch_jobs": fetch_jobs,
    "score_jobs": score_jobs,
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
]


def dispatch_tool(name: str, args: dict):
    """Execute one tool call. Raises KeyError for unknown tools (agent must handle it)."""
    if name not in TOOL_FUNCTIONS:
        raise KeyError(f"unknown tool: {name}")
    return TOOL_FUNCTIONS[name](**args)
