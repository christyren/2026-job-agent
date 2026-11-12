"""Post-milestone iteration 2026-11-12: batch typed decisions (Decisions-API style).

What today is about
-------------------
Yesterday (2026-11-11) Google shipped the Gemini Decisions API, powered by
Gemini Decision 1: instead of generating text token by token, it SCORES
candidate answers in a single forward pass and returns probabilities +
confidence scores — up to 1,000 typed questions against ONE shared input,
150-250 ms end to end, $0.10 per 1M input tokens, $0 output tokens billed.
Three question types: `noul` (yes/no probability), `choice` (pick one of
2-255 options, every option gets a probability), `score` (rubric grade,
a continuous weighted value).

Our planner (milestone 6) already emits typed decisions — action enum +
confidence + reasons — but one decision at a time, each built by its own
rule. Today we map that planner onto the Decisions API shape:

  * `choice`  — one question per application: "这条申请今天先做什么？"
    options = the 8 planner ACTIONS, each scored; the winner is the
    planner's action, its score is the planner's confidence.
  * `noul`    — one yes/no question per decision: "这条决定能自动执行吗？"
    P(yes) = the decision's confidence. (Low confidence = probably not —
    exactly the threshold rule from milestone 3/6, now as a probability.)
  * `score`   — one rubric question per BATCH: "这份计划整体有多可执行？"
    value = mean confidence across decisions. A plan full of
    needs_review scores low — that is the number telling you the morning
    needs a human, not more automation.

Concepts
--------
- **Closed-set decision vs open generation**: when the answer must be one
  of N known options, do not generate prose and parse it back — score the
  options directly. Scoring is cheaper, faster, and calibrated (the model
  tells you how sure it is). This is the same lesson as milestone 3's
  structured output, pushed one step further: there the *format* was
  typed, here the *decision itself* is a number.
- **Batching over a shared input**: one request carries the shared context
  (applications + memory) once, plus up to 1,000 independent questions.
  The shared input is billed once no matter how many questions ride on
  it — the per-question cost is only its own short instruction tokens.
  Compare with calling the LLM once per question (milestone 1 pricing):
  the shared context gets re-billed every single time.
- **Questions are independent**: adding or removing one question does not
  change the others' answers. That is what makes a batch testable — the
  eval can check each question on its own, exactly like our planner eval
  checks the top action only.
- **A probability is still a threshold in disguise**: `noul` P(yes) below
  0.7 means the same thing as milestone 6's confidence < 0.7 — hand it
  to a human. The number changed shape (probability instead of a rule),
  the guardrail did not move.

This module is deterministic (same planner input -> same batch), exactly
like the rule planner it wraps. A real Decisions API call would replace
`plan_to_decisions`' scoring step with one API request; the batch shape,
validation, and eval stay identical — that is the point of typing the
contract first.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .planning import ACTIONS, PLAN_THRESHOLD, plan_today

# --- Decisions API facts (Google blog, 2026-11-11; re-check before quoting
#     as current pricing — these are launch numbers, not permanent ones)
DECISION_TYPES = ("noul", "choice", "score")
MAX_QUESTIONS_PER_BATCH = 1000
DECISION_INPUT_PRICE_PER_MILLION = 0.10  # $0.10 / 1M input tokens
DECISION_OUTPUT_PRICE_PER_MILLION = 0.0  # output tokens are never billed


@dataclass
class TypedDecision:
    """One typed question + its scored answer, Decisions-API shaped."""
    question_id: str
    question_type: str  # "noul" | "choice" | "score"
    question: str
    choices: list[dict] = field(default_factory=list)  # choice: [{action, score}]
    chosen: str = ""          # choice: winning action; noul/score: "" (see value)
    value: float = 0.0        # noul: P(yes); score: rubric value 0-1
    confidence: float = 0.0   # the planner confidence behind this question
    requires_human: bool = False
    reasons: list[str] = field(default_factory=list)
    original_action: str | None = None

    def to_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "question": self.question,
            "choices": [dict(c) for c in self.choices],
            "chosen": self.chosen,
            "value": self.value,
            "confidence": self.confidence,
            "requires_human": self.requires_human,
            "reasons": list(self.reasons),
            "original_action": self.original_action,
        }


# ------------------------------------------------------------ schema + checks
def validate_typed_decision(raw: dict) -> TypedDecision:
    """Strict validation: a malformed decision must crash here, never
    silently become an action. Mirrors planning.validate_plan_decision."""
    if not isinstance(raw, dict):
        raise ValueError(f"typed decision must be an object, got {type(raw).__name__}")
    required = {"question_id", "question_type", "question", "choices", "chosen",
                "value", "confidence", "requires_human", "reasons", "original_action"}
    if set(raw.keys()) != required:
        raise ValueError(
            f"typed decision keys {sorted(raw.keys())} != required {sorted(required)}")
    qtype = raw["question_type"]
    if qtype not in DECISION_TYPES:
        raise ValueError(f"invalid question_type: {qtype!r} (have {list(DECISION_TYPES)})")
    if not isinstance(raw["question"], str) or not raw["question"].strip():
        raise ValueError("question must be a non-empty string")
    if not 0 <= float(raw["value"]) <= 1:
        raise ValueError(f"value out of range 0-1: {raw['value']}")
    if not 0 <= float(raw["confidence"]) <= 1:
        raise ValueError(f"confidence out of range 0-1: {raw['confidence']}")
    if not isinstance(raw["requires_human"], bool):
        raise ValueError("requires_human must be a boolean")
    if not isinstance(raw["reasons"], list) or not all(
            isinstance(r, str) for r in raw["reasons"]):
        raise ValueError("reasons must be a list of strings")
    if raw["original_action"] is not None and raw["original_action"] not in ACTIONS:
        raise ValueError(f"invalid original_action: {raw['original_action']!r}")

    choices = raw["choices"]
    if qtype == "choice":
        if not (2 <= len(choices) <= 255):
            raise ValueError(
                f"choice needs 2-255 options, got {len(choices)}")
        for c in choices:
            if set(c.keys()) != {"action", "score"}:
                raise ValueError(f"choice option must be {{action, score}}, got {c}")
            if c["action"] not in ACTIONS:
                raise ValueError(f"invalid choice action: {c['action']!r}")
            if not 0 <= float(c["score"]) <= 1:
                raise ValueError(f"choice score out of range 0-1: {c['score']}")
        if raw["chosen"] not in {c["action"] for c in choices}:
            raise ValueError(f"chosen {raw['chosen']!r} is not one of the choices")
        top = max(c["score"] for c in choices)
        chosen_score = next(c["score"] for c in choices if c["action"] == raw["chosen"])
        if chosen_score != top:
            raise ValueError(
                f"chosen {raw['chosen']!r} scores {chosen_score}, but the top "
                f"option scores {top} — the winner must be the highest-scored option")
    else:
        if choices:
            raise ValueError(f"{qtype} questions carry no choices, got {choices}")
        if raw["chosen"] != "":
            raise ValueError(f"{qtype} questions have no chosen action, got {raw['chosen']!r}")
    return TypedDecision(
        question_id=str(raw["question_id"]), question_type=qtype,
        question=raw["question"], choices=[dict(c) for c in choices],
        chosen=raw["chosen"], value=float(raw["value"]),
        confidence=float(raw["confidence"]), requires_human=raw["requires_human"],
        reasons=list(raw["reasons"]), original_action=raw["original_action"])


# ------------------------------------------------------------ planner -> batch
def _choice_question(decision: dict, idx: int) -> TypedDecision:
    """One application -> one `choice` question over the 8 planner actions."""
    company = decision["company"] or "（无公司名）"
    target = decision["target_id"] or "today"
    question = f"{company}（{target}）这条申请今天先做什么？"
    choices = [{"action": a,
                "score": decision["confidence"] if a == decision["action"] else 0.0}
               for a in ACTIONS]
    return TypedDecision(
        question_id=f"q{idx:02d}-choice-{target}", question_type="choice",
        question=question, choices=choices, chosen=decision["action"],
        value=decision["confidence"], confidence=decision["confidence"],
        requires_human=decision["requires_human"],
        reasons=list(decision["reasons"]),
        original_action=decision["original_action"])


def _noul_question(decision: dict, idx: int) -> TypedDecision:
    """One decision -> one `noul` question: can it auto-execute?

    P(yes) = the decision's confidence. Below PLAN_THRESHOLD the planner
    already rewrote the action to needs_review — the probability says the
    same thing in the API's language.
    """
    company = decision["company"] or "这条决定"
    return TypedDecision(
        question_id=f"q{idx:02d}-noul-{decision['target_id'] or 'today'}",
        question_type="noul",
        question=f"{company} 的决定（{decision['action']}）能自动执行、不需要人工吗？",
        value=decision["confidence"], confidence=decision["confidence"],
        requires_human=decision["requires_human"],
        reasons=list(decision["reasons"])
        + [f"P(yes) = confidence {decision['confidence']:.2f}; "
           f"below {PLAN_THRESHOLD} it goes to a human"],
        original_action=decision["original_action"])


def plan_to_decisions(plan: list[dict]) -> list[dict]:
    """Convert a planner's ordered decisions into a Decisions-API batch.

    Per decision: one `choice` + one `noul`. Plus one batch-level `score`
    question grading the whole plan (mean confidence). The batch is capped
    at MAX_QUESTIONS_PER_BATCH — the API's own limit, enforced here so a
    runaway plan can never build an oversized request.
    """
    batch: list[TypedDecision] = []
    for i, d in enumerate(plan, 1):
        batch.append(_choice_question(d, i))
        batch.append(_noul_question(d, i))
    if plan:
        mean_conf = round(sum(d["confidence"] for d in plan) / len(plan), 3)
    else:
        mean_conf = 0.0
    batch.append(TypedDecision(
        question_id="q00-score-plan", question_type="score",
        question="这份今日计划整体有多可执行？（0=全要人工，1=全可自动执行）",
        value=mean_conf, confidence=mean_conf,
        requires_human=any(d["requires_human"] for d in plan),
        reasons=[f"mean confidence over {len(plan)} decisions = {mean_conf:.2f}"]))
    if len(batch) > MAX_QUESTIONS_PER_BATCH:
        raise ValueError(
            f"batch has {len(batch)} questions, over the API cap "
            f"{MAX_QUESTIONS_PER_BATCH} — split it before sending")
    return [validate_typed_decision(d.to_dict()).to_dict() for d in batch]


# ------------------------------------------------------------ batch cost
def estimate_batch_cost(num_questions: int, tokens_per_question: int = 400,
                        shared_input_tokens: int = 2000,
                        input_price_per_million: float = DECISION_INPUT_PRICE_PER_MILLION,
                        output_price_per_million: float = DECISION_OUTPUT_PRICE_PER_MILLION,
                        ) -> dict:
    """Cost of one Decisions-API batch: shared input billed ONCE, each
    question adds only its own tokens, output is always $0.

    Compare with one LLM call per question (milestone 1 pricing): there
    the shared context is re-billed on every call. That is the whole
    economic point of batching.
    """
    if num_questions < 0:
        raise ValueError("num_questions must be >= 0")
    total_input = shared_input_tokens + num_questions * tokens_per_question
    cost = total_input / 1_000_000 * input_price_per_million
    return {
        "num_questions": num_questions,
        "shared_input_tokens": shared_input_tokens,
        "tokens_per_question": tokens_per_question,
        "total_input_tokens": total_input,
        "total_output_tokens": 0,  # decisions return scores, not text
        "estimated_cost_usd": round(cost, 6),
        "output_cost_usd": 0.0,
    }


def build_decisions_batch(apps=None, today=None, memory_entries=None, **kw) -> dict:
    """Today's plan as a typed-decision batch + its cost estimate.

    Reads the same durable stores as plan_today (milestone 5/6) — the
    batch is a pure function of files, never of what an LLM recalls.
    Proposes only; nothing here executes, submits, or messages anyone.
    """
    plan = plan_today(apps=apps, today=today, memory_entries=memory_entries, **kw)
    batch = plan_to_decisions(plan)
    n_questions = len(batch)
    return {
        "questions": batch,
        "num_questions": n_questions,
        "top_action": plan[0]["action"] if plan else "wait",
        "plan_score": batch[-1]["value"] if batch else 0.0,
        "needs_human_count": sum(1 for d in plan if d["requires_human"]),
        "cost": estimate_batch_cost(n_questions),
    }


def format_decisions(batch: list[dict]) -> str:
    lines = [f"类型化决策批：{len(batch)} 个问题（一份共享输入 + 每题独立打分）"]
    for d in batch:
        if d["question_type"] == "choice":
            opts = ", ".join(f"{c['action']}={c['score']:.2f}"
                             for c in d["choices"] if c["score"] > 0)
            lines.append(f"  [choice] {d['question']} → {d['chosen']} "
                         f"(conf={d['confidence']:.2f}) 选项：{opts}")
        elif d["question_type"] == "noul":
            lines.append(f"  [noul] {d['question']} → P(yes)={d['value']:.2f}"
                         + ("（需人工）" if d["requires_human"] else ""))
        else:
            lines.append(f"  [score] {d['question']} → {d['value']:.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    report = build_decisions_batch()
    print(format_decisions(report["questions"]))
    print(f"\nplan_score={report['plan_score']:.2f} "
          f"needs_human={report['needs_human_count']} "
          f"batch_cost=${report['cost']['estimated_cost_usd']}")
