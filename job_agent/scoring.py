"""Milestone 3 preview: scoring interface — defined today, implemented next time.

Today's idea (from this morning's 'decision model' reading): do NOT let the LLM
judge everything. Split scoring into two layers:

1. Hard rules first (deterministic, free, explainable): excluded company,
   role type, years required. If a hard rule fails -> verdict=no_match,
   confidence=1.0, no LLM call needed, no money spent.
2. LLM second (only for jobs that pass hard rules): returns a STRUCTURED
   result — verdict + confidence — not free text. If confidence < threshold,
   we do not auto-decide; we mark needs_review for a human.

This file only defines the contract (schema + threshold + a hard-rule stub)
so milestone 2's pipeline already stores jobs in a shape milestone 3 can score.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Below this confidence, never auto-decide — hand it to a human.
REVIEW_THRESHOLD = 0.7

# JSON Schema for the LLM's structured output in milestone 3.
MATCH_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["match", "no_match", "needs_review"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "hard_filter_failed": {"type": ["string", "null"]},
    },
    "required": ["verdict", "confidence", "reasons", "hard_filter_failed"],
}


@dataclass
class MatchResult:
    verdict: str  # "match" | "no_match" | "needs_review"
    confidence: float
    reasons: list[str] = field(default_factory=list)
    hard_filter_failed: str | None = None


def needs_review(confidence: float, threshold: float = REVIEW_THRESHOLD) -> bool:
    return confidence < threshold


def hard_filter(job: dict, prefs: dict) -> MatchResult | None:
    """Deterministic pre-filter. Returns a MatchResult if a hard rule decides
    the job outright, or None if it should go on to LLM scoring (milestone 3)."""
    company = str(job.get("company", ""))
    for excluded in prefs.get("exclude_companies", []):
        if excluded.lower() == company.lower():
            return MatchResult(
                verdict="no_match", confidence=1.0,
                reasons=[f"excluded company: {excluded}"],
                hard_filter_failed="exclude_companies",
            )
    return None
