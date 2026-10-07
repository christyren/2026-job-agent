"""Milestone 6: planning + long-term memory + planner eval.

Concepts:
- Planning (vs reacting): a reactive agent decides one step, acts, then
  decides again — flexible, but hard to audit and easy to drift. A
  planner first turns the goal ("what should I do today?") into an
  ORDERED list of typed decisions, and only then does anything execute.
  You can read, diff, and eval a plan before a single action runs.
- Typed decision: the planner may ONLY emit actions from the ACTIONS
  enum, each with target_id / confidence / reasons / requires_human,
  validated against PLAN_DECISION_SCHEMA. Free-text plans ("maybe
  follow up with someone soon-ish") cannot be checked, sorted, or
  tested — typed plans can. Same lesson as milestone 3's structured
  output, one level up: there the *score* was typed, here the *next
  action* is typed.
- Confidence threshold, again: confidence < PLAN_THRESHOLD (0.7) never
  auto-executes. The decision is rewritten to action="needs_review"
  (original_action preserved) and a human decides. Identical pattern
  to scoring.apply_threshold — deliberately: one guardrail, everywhere.
- Memory has three layers; only one belongs in a file:
  * working memory  = the current conversation / LLM context (volatile)
  * preferences     = data/preferences.json (milestone 1, slow-changing)
  * long-term memory = data/agent_memory.json (this milestone):
    decisions a human made on earlier days that must still bind today
    (e.g. "PauseCo is paused — do not push it"). It is durable state in
    the milestone-5 sense: re-read from disk on every call, append-only,
    each entry carries source + created_at. The LLM never "remembers"
    these — the program loads them and lets them change the plan.
  A memory conflict does not silently win or lose: it drags the
  decision's confidence below the threshold, so a HUMAN resolves it.
- Planner eval: data/eval_planner.json holds labelled scenarios
  (store snapshot + memory snapshot + expected top action). Changing
  priorities, rules, or the threshold must re-run it — eyeballing one
  morning's plan is not an evaluation (same rule as milestones 3-4).

Nothing here executes the plan. Planning only PROPOSES; a human
approves outward actions (follow-ups, submissions) exactly as before.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MEMORY_PATH = DATA_DIR / "agent_memory.json"

PLAN_THRESHOLD = 0.7  # below this, a decision never auto-executes

ACTIONS = (
    "clear_needs_review",  # a human clears an application parked in needs_review
    "prep_interview",      # prepare for an application in interview
    "follow_up_stale",     # follow up an applied/replied application gone quiet
    "review_materials",    # human reviews a draft and decides whether to apply
    "draft_materials",     # milestone 4: draft points for a scored job
    "score_new_jobs",      # milestone 3: score freshly fetched jobs
    "needs_review",        # planner itself is unsure -> hand the decision to a human
    "wait",                # nothing actionable; do nothing today
)

# Lower sorts first. needs_review (post-threshold) keeps its original
# priority for sorting, so a downgraded urgent item still surfaces early.
PRIORITY = {
    "clear_needs_review": 1,
    "prep_interview": 2,
    "follow_up_stale": 3,
    "review_materials": 4,
    "draft_materials": 5,
    "score_new_jobs": 6,
    "wait": 8,
}

PLAN_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "target_id": {"type": "string"},
        "company": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "requires_human": {"type": "boolean"},
        "original_action": {"type": ["string", "null"]},
    },
    "required": ["action", "target_id", "company", "confidence",
                 "reasons", "requires_human", "original_action"],
    "additionalProperties": False,
}


@dataclass
class PlanDecision:
    action: str
    target_id: str = ""
    company: str = ""
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    requires_human: bool = False
    original_action: str | None = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "target_id": self.target_id,
            "company": self.company,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "requires_human": self.requires_human,
            "original_action": self.original_action,
        }


# ------------------------------------------------------- schema + threshold
def validate_plan_decision(raw: dict) -> PlanDecision:
    """Strict validation, same style as scoring.validate_match_result:
    a malformed plan must crash here, never silently become an action."""
    if not isinstance(raw, dict):
        raise ValueError(f"plan decision must be an object, got {type(raw).__name__}")
    required = set(PLAN_DECISION_SCHEMA["required"])
    if set(raw.keys()) != required:
        raise ValueError(
            f"plan decision keys {sorted(raw.keys())} != required {sorted(required)}")
    if raw["action"] not in ACTIONS:
        raise ValueError(f"invalid action: {raw['action']!r} (have {list(ACTIONS)})")
    confidence = raw["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("confidence must be a number")
    if not 0 <= float(confidence) <= 1:
        raise ValueError(f"confidence out of range 0-1: {confidence}")
    if not isinstance(raw["reasons"], list) or not all(
            isinstance(r, str) for r in raw["reasons"]):
        raise ValueError("reasons must be a list of strings")
    if not isinstance(raw["requires_human"], bool):
        raise ValueError("requires_human must be a boolean")
    if raw["original_action"] is not None and raw["original_action"] not in ACTIONS:
        raise ValueError(f"invalid original_action: {raw['original_action']!r}")
    return PlanDecision(
        action=raw["action"], target_id=str(raw["target_id"]),
        company=str(raw["company"]), confidence=float(confidence),
        reasons=list(raw["reasons"]), requires_human=raw["requires_human"],
        original_action=raw["original_action"])


def apply_plan_threshold(decision: PlanDecision,
                         threshold: float = PLAN_THRESHOLD) -> PlanDecision:
    """Low confidence never auto-executes: rewrite to needs_review,
    preserving what the planner originally wanted in original_action."""
    if decision.action != "needs_review" and decision.confidence < threshold:
        return replace(
            decision, action="needs_review", original_action=decision.action,
            requires_human=True,
            reasons=decision.reasons + [
                f"confidence {decision.confidence:.2f} < threshold {threshold} "
                f"-> needs_review (planner leaned {decision.action}, "
                "evidence/conflict means a human decides)"])
    return decision


# ------------------------------------------------------------ long-term memory
MEMORY_KINDS = ("paused_company", "decision", "preference", "fact")


def load_memory(path: Path = MEMORY_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_memory(entries: list[dict], path: Path = MEMORY_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def remember(entry: dict, path: Path = MEMORY_PATH) -> dict:
    """Append one entry and persist. Append-only, like tracking history:
    an old decision is superseded by a newer entry, never edited away."""
    if entry.get("kind") not in MEMORY_KINDS:
        raise ValueError(f"unknown memory kind: {entry.get('kind')!r} (have {list(MEMORY_KINDS)})")
    entries = load_memory(path)
    entries.append(entry)
    save_memory(entries, path)
    return entry


def paused_companies(entries: list[dict]) -> set[str]:
    """Companies a human decided to pause. Planner must respect these."""
    return {str(e.get("company", "")).lower()
            for e in entries if e.get("kind") == "paused_company" and e.get("company")}


# ------------------------------------------------------------------- planner
def _days_since(iso_ts: str, today: date) -> int:
    try:
        return (today - datetime.fromisoformat(iso_ts).date()).days
    except (ValueError, TypeError):
        return 0


def _candidate(app: dict, today: date, stale_days: int) -> PlanDecision | None:
    """One deterministic rule per status -> at most one candidate.
    Returns None when the application needs nothing today."""
    status = app.get("status", "")
    base = {"target_id": app.get("id", ""), "company": app.get("company", "")}
    days = _days_since(app.get("updated_at", ""), today)
    if status == "needs_review":
        return PlanDecision(action="clear_needs_review", confidence=0.95,
                            requires_human=True, **base,
                            reasons=["status is needs_review: a human parked it, "
                                     "only a human can clear it"])
    if status == "interview":
        return PlanDecision(action="prep_interview", confidence=0.90, **base,
                            reasons=["status is interview: preparation has a deadline"])
    if status in ("applied", "replied") and days >= stale_days:
        return PlanDecision(action="follow_up_stale",
                            confidence=0.85 if days >= 10 else 0.80,
                            requires_human=True, **base,
                            reasons=[f"status is {status} and quiet for {days} days "
                                     f"(>= stale threshold {stale_days})"])
    if status == "materials_ready":
        return PlanDecision(action="review_materials", confidence=0.85,
                            requires_human=True, **base,
                            reasons=["draft exists: the submit decision is always human"])
    if status == "scored":
        return PlanDecision(action="draft_materials", confidence=0.80, **base,
                            reasons=["scored match with no draft yet"])
    if status == "new":
        return PlanDecision(action="score_new_jobs", confidence=0.90, **base,
                            reasons=["freshly fetched, not scored yet"])
    return None  # fresh applied/replied (waiting is correct) or terminal


def plan_today(apps: list[dict] | None = None, today: str | None = None,
               memory_entries: list[dict] | None = None, stale_days: int = 7,
               apps_path=None, memory_path: Path = MEMORY_PATH) -> list[dict]:
    """Build today's ordered plan from durable state + long-term memory.

    Both inputs are re-read from disk when not passed in — the plan is
    a pure function of files, never of what an LLM happens to recall.
    Returns validated, thresholded decisions sorted by priority.
    """
    if apps is None:
        from .tracking import load_applications
        apps = load_applications(apps_path) if apps_path else load_applications()
    entries = memory_entries if memory_entries is not None else load_memory(memory_path)
    day = date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    paused = paused_companies(entries)

    candidates: list[PlanDecision] = []
    for app in apps:
        cand = _candidate(app, day, stale_days)
        if cand is None:
            continue
        if cand.company.lower() in paused:
            cand = replace(cand, confidence=0.50, reasons=cand.reasons + [
                f"long-term memory: {cand.company} is paused by a human decision "
                "— planner must not push it forward on its own"])
        if not cand.company or not app.get("title"):
            cand = replace(cand, confidence=round(cand.confidence - 0.30, 2),
                           reasons=cand.reasons + ["record is missing company/title — weak evidence"])
        candidates.append(cand)

    if not candidates:
        candidates = [PlanDecision(
            action="wait", confidence=0.90,
            reasons=["no application needs action today "
                     "(all fresh, waiting, or terminal) — that is a finding, not a gap"])]

    candidates.sort(key=lambda d: (PRIORITY.get(d.original_action or d.action, 8),
                                   PRIORITY.get(d.action, 8), -d.confidence))
    plan = [apply_plan_threshold(validate_plan_decision(d.to_dict()))
            for d in candidates]
    # Sort again after thresholding, keeping a downgraded item near its
    # original priority so urgent-but-uncertain work still surfaces early.
    plan.sort(key=lambda d: (PRIORITY.get(d.original_action or d.action, 8),
                             -d.confidence))
    return [d.to_dict() for d in plan]


def build_planner_prompt(apps: list[dict], memory_entries: list[dict]) -> str:
    """The prompt a real LLM planner would get. Kept as code so the
    contract is readable: emit ONLY decisions matching the schema."""
    return (
        "Plan today's job-search actions. Return ONLY a JSON array of "
        "objects matching this schema, no prose:\n"
        f"{json.dumps(PLAN_DECISION_SCHEMA)}\n"
        "Rules: action must be one of the enum; anything you are unsure "
        "about gets confidence < 0.7 (it will be handed to a human); "
        "never propose submitting an application.\n"
        f"Applications: {json.dumps(apps, ensure_ascii=False)}\n"
        f"Long-term memory (human decisions that still bind): "
        f"{json.dumps(memory_entries, ensure_ascii=False)}"
    )


def format_plan(plan: list[dict], day: str = "") -> str:
    lines = [f"今日计划 {day}：{len(plan)} 条决定"]
    for i, d in enumerate(plan, 1):
        human = "（需人工）" if d["requires_human"] else ""
        orig = f" [原建议 {d['original_action']}]" if d["original_action"] else ""
        lines.append(f"  {i}. {d['action']} {d['company']}{human} "
                     f"conf={d['confidence']:.2f}{orig} — {d['reasons'][0] if d['reasons'] else ''}")
    return "\n".join(lines)
