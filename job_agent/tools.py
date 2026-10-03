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


# Registry: name -> python function. The agent loop dispatches through this dict.
TOOL_FUNCTIONS = {
    "get_preferences": get_preferences,
    "estimate_cost": estimate_cost,
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
]


def dispatch_tool(name: str, args: dict):
    """Execute one tool call. Raises KeyError for unknown tools (agent must handle it)."""
    if name not in TOOL_FUNCTIONS:
        raise KeyError(f"unknown tool: {name}")
    return TOOL_FUNCTIONS[name](**args)
