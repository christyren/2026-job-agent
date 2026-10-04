"""Milestone 3: job matching scoring — hard rules -> LLM (structured output) -> threshold.

Pipeline (remember this order — it is the whole lesson):
1. Hard rules first (deterministic, free, explainable). Excluded company,
   excluded role type, years required, known salary below the floor.
   A hard failure decides outright: verdict=no_match, confidence=1.0,
   and NO LLM call is made (no money, no randomness).
2. Soft scoring second (only for jobs that pass hard rules). In production
   this is an LLM forced to return STRUCTURED output matching
   MATCH_RESULT_SCHEMA — not free text we have to regex. Offline we use
   heuristic_scorer(), a deterministic stand-in with the exact same shape,
   so the whole pipeline is testable with no API key.
3. Threshold last. confidence != fit. Confidence says "how much evidence
   did I actually see?" If confidence < REVIEW_THRESHOLD (0.7), we do NOT
   auto-decide: verdict becomes needs_review for a human — even if the
   scorer leaned match. Low evidence is a failure mode, not a verdict.

Eval note: data/eval_jobs.json holds 10 hand-labelled cases. Any change to
this file, the prompt, or the threshold must re-run the eval
(job_agent/evaluate.py) — eyeballing 2-3 outputs is not an evaluation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

# Below this confidence, never auto-decide — hand it to a human.
REVIEW_THRESHOLD = 0.7

# JSON Schema for the scorer's structured output.
MATCH_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["match", "no_match", "needs_review"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "hard_filter_failed": {"type": ["string", "null"]},
    },
    "required": ["verdict", "confidence", "reasons", "hard_filter_failed"],
    "additionalProperties": False,
}


@dataclass
class MatchResult:
    verdict: str  # "match" | "no_match" | "needs_review"
    confidence: float
    reasons: list[str] = field(default_factory=list)
    hard_filter_failed: str | None = None

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "hard_filter_failed": self.hard_filter_failed,
        }


def needs_review(confidence: float, threshold: float = REVIEW_THRESHOLD) -> bool:
    return confidence < threshold


# ---------------------------------------------------------------- hard rules
def _infer_role_type(job: dict) -> str:
    """Use the explicit role_type if present, else infer from title keywords."""
    explicit = str(job.get("role_type") or "").strip().lower()
    if explicit:
        return explicit
    title = str(job.get("title", "")).lower()
    if "frontend" in title or "front-end" in title or "front end" in title:
        return "frontend-only"
    if "ios" in title or "android" in title or "mobile" in title:
        return "mobile-only"
    if "data scientist" in title or "data science" in title:
        return "data-science-only"
    return "backend"


def hard_filter(job: dict, prefs: dict) -> MatchResult | None:
    """Deterministic pre-filter. Returns a MatchResult if a hard rule decides
    the job outright, or None if it should go on to soft (LLM) scoring."""
    company = str(job.get("company", "")).strip()
    for excluded in prefs.get("exclude_companies", []):
        if excluded.lower() == company.lower():
            return MatchResult(
                verdict="no_match", confidence=1.0,
                reasons=[f"excluded company: {excluded}"],
                hard_filter_failed="exclude_companies",
            )

    role_type = _infer_role_type(job)
    if role_type in prefs.get("exclude_role_types", []):
        return MatchResult(
            verdict="no_match", confidence=1.0,
            reasons=[f"excluded role type: {role_type}"],
            hard_filter_failed="exclude_role_types",
        )

    years = job.get("years_required")
    max_years = prefs.get("max_years_required")
    if isinstance(years, (int, float)) and max_years is not None and years > max_years:
        return MatchResult(
            verdict="no_match", confidence=1.0,
            reasons=[f"requires {years} years > max {max_years} years"],
            hard_filter_failed="max_years_required",
        )

    salary = job.get("base_salary_usd")
    min_base = prefs.get("min_base_usd")
    if isinstance(salary, (int, float)) and min_base is not None and salary < min_base:
        return MatchResult(
            verdict="no_match", confidence=1.0,
            reasons=[f"known base ${salary} < floor ${min_base}"],
            hard_filter_failed="min_base_usd",
        )
    # NOTE: salary is None (JD did not say) is NOT a hard failure.
    # Unknown != too low. It lowers confidence later -> needs_review.
    return None


# ------------------------------------------------------- structured output
def validate_match_result(raw: dict) -> MatchResult:
    """Validate a raw scorer/LLM dict against MATCH_RESULT_SCHEMA.

    Hand-written (no jsonschema dependency) but strict: missing keys,
    extra keys, a verdict outside the enum, or confidence outside 0-1
    all raise ValueError. In production this is the crash you WANT:
    a malformed LLM answer must never silently become a decision.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"match result must be an object, got {type(raw).__name__}")
    required = set(MATCH_RESULT_SCHEMA["required"])
    keys = set(raw.keys())
    if keys != required:
        raise ValueError(f"match result keys {sorted(keys)} != required {sorted(required)}")
    verdict = raw["verdict"]
    if verdict not in MATCH_RESULT_SCHEMA["properties"]["verdict"]["enum"]:
        raise ValueError(f"invalid verdict: {verdict!r}")
    confidence = raw["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("confidence must be a number")
    if not 0 <= float(confidence) <= 1:
        raise ValueError(f"confidence out of range 0-1: {confidence}")
    reasons = raw["reasons"]
    if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
        raise ValueError("reasons must be a list of strings")
    hard = raw["hard_filter_failed"]
    if hard is not None and not isinstance(hard, str):
        raise ValueError("hard_filter_failed must be a string or null")
    return MatchResult(
        verdict=verdict, confidence=float(confidence),
        reasons=list(reasons), hard_filter_failed=hard,
    )


def apply_threshold(result: MatchResult, threshold: float = REVIEW_THRESHOLD) -> MatchResult:
    """Low confidence never auto-decides: downgrade to needs_review."""
    if result.verdict != "needs_review" and needs_review(result.confidence, threshold):
        return replace(
            result, verdict="needs_review",
            reasons=result.reasons + [
                f"confidence {result.confidence:.2f} < threshold {threshold} "
                f"-> needs_review (scorer leaned {result.verdict}, evidence too weak to auto-decide)"
            ],
        )
    return result


def build_scoring_prompt(job: dict, prefs: dict) -> str:
    """The prompt a real LLM call would use. Kept as code so you can read
    exactly what the model is told — and so eval can diff prompt versions."""
    return (
        "Score this job against the candidate's criteria. "
        "Return ONLY a JSON object matching this schema, no prose:\n"
        f"{json.dumps(MATCH_RESULT_SCHEMA)}\n"
        "confidence = how much concrete evidence the JD gives you "
        "(salary stated? sponsorship stated? requirements specific?), NOT how good the fit feels.\n"
        f"Criteria: {json.dumps(prefs, ensure_ascii=False)}\n"
        f"Job: {json.dumps(job, ensure_ascii=False)}"
    )


def parse_llm_response(text: str) -> MatchResult:
    """Parse + validate + threshold a real LLM response. Raises on bad JSON/schema."""
    return apply_threshold(validate_match_result(json.loads(text)))


# ------------------------------------------------------------ soft scoring
def heuristic_scorer(job: dict, prefs: dict) -> dict:
    """Offline stand-in for the LLM scorer. Same output shape, deterministic.

    Two separate quantities — do not merge them:
    - fit: does the job match (target role? skills overlap?)
    - confidence: how much evidence was available to judge at all?
    A perfect-fit JD that hides its salary and says nothing about
    sponsorship gets verdict=match from fit, then the threshold step
    downgrades it to needs_review. That is deliberate.
    """
    title = str(job.get("title", "")).lower()
    text = (title + " " + str(job.get("description", ""))).lower()
    reasons: list[str] = []

    # --- fit ---
    fit = 0.0
    target_hit = any(
        kw in title
        for kw in ("backend", "software engineer ii", "sde2", "sde ii", "software engineer, backend")
    )
    if target_hit:
        fit += 0.55
        reasons.append("title matches a target backend/SDE2 role")
    skills = [str(s).lower() for s in prefs.get("skills", [])]
    overlap = [s for s in skills if s in text]
    fit += min(0.35, 0.09 * len(overlap))
    if overlap:
        reasons.append(f"skills overlap: {', '.join(overlap[:5])} ({len(overlap)} total)")
    location = str(job.get("location", ""))
    if any(p.lower() in location.lower() for p in prefs.get("preferred_locations", [])):
        fit += 0.10
        reasons.append(f"preferred location: {location}")
    verdict = "match" if fit >= 0.55 else "no_match"
    if verdict == "no_match":
        reasons.append(f"fit score {fit:.2f} too low (no target-role hit / weak skills overlap)")

    # --- confidence = evidence strength ---
    # Base 0.45 (not 0.50) on purpose: a long JD alone must not cross the
    # 0.7 threshold. Crossing it needs at least salary or sponsorship stated —
    # eval-06 caught the old 0.50 base sitting exactly on the boundary.
    confidence = 0.45
    description = str(job.get("description", ""))
    if len(description) >= 80:
        confidence += 0.15
    if isinstance(job.get("base_salary_usd"), (int, float)):
        confidence += 0.15
        reasons.append("salary is stated in the JD")
    else:
        reasons.append("salary NOT stated — evidence gap")
    if re.search(r"sponsor|visa|work authorization", text):
        confidence += 0.15
        reasons.append("sponsorship / work authorization mentioned")
    else:
        reasons.append("sponsorship NOT mentioned — evidence gap")
    if isinstance(job.get("years_required"), (int, float)):
        confidence += 0.05
    confidence = round(min(0.95, confidence), 2)

    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasons": reasons,
        "hard_filter_failed": None,
    }


def score_job(job: dict, prefs: dict, scorer=heuristic_scorer) -> MatchResult:
    """Full pipeline for one job: hard rules -> scorer -> validate -> threshold."""
    hard = hard_filter(job, prefs)
    if hard is not None:
        return hard
    return apply_threshold(validate_match_result(scorer(job, prefs)))
