"""Post-milestone iteration 2026-11-13: batch scoring — one batch, many jobs.

What today is about
--------------------
Milestone 3 scores jobs ONE LLM CALL AT A TIME: each job re-sends the
shared context (your preferences + the scoring criteria) and gets one
verdict back. Yesterday (2026-11-12) we packed the planner's decisions
into a Decisions-API batch — one shared input, up to 1,000 typed
questions, output free. Today we apply the same batch shape to the
scoring pipeline itself:

  * one `choice` question per job — "这条岗位是 match / no_match /
    needs_review？" — options = the 3 verdicts, each scored; the winner
    is the pipeline's FINAL verdict (after hard rules + threshold),
    its score is the pipeline's confidence. The other two verdicts
    split the remaining probability (1 - confidence) between them.
  * one batch-level `score` question — "这批岗位整体的可信度？" —
    value = mean confidence across jobs. A batch full of
    needs_review scores low — that number tells you the batch needs
    a human, not more automation.

Concepts
--------
- **Batching is a cost lever, not just a speed trick**: the shared
  input (preferences + criteria, ~2,000 tokens) is billed ONCE per
  batch instead of once per job. 10 jobs one-by-one cost
  10 x (2,000 + 400) tokens; the batch costs 2,000 + 10 x 400. The
  bigger the batch, the bigger the saving — the shared part is
  amortised over every question in it. (Same idea as yesterday's
  planner batch; today the questions are jobs, not decisions.)
- **A choice question asks for the FINAL verdict, not the first
  instinct**: the scorer may lean `match`, but if confidence is
  below 0.7 the threshold step already rewrote it to `needs_review`.
  The choice question's winner is that rewritten verdict — the
  threshold is part of the answer, not an afterthought. If you
  packed the pre-threshold lean instead, the batch would quietly
  auto-approve jobs the pipeline had already refused to decide.
- **Scores must still pass validation**: a `choice` question is only
  legal if the chosen option is the highest-scored one. Our scorer's
  confidence never drops below 0.45, so the winner (0.45+) always
  beats the split remainder ((1 - 0.45) / 2 = 0.275). If a future
  scorer ever returned confidence below 1/3, the split remainder
  would out-score the winner and validation would REFUSE to build
  the batch — that is the guardrail doing its job, not a bug.
- **Same contract-first pattern as yesterday**: this module is
  deterministic (heuristic scorer, same shape as the real LLM path).
  A real Batch / Decisions API call would replace only the scoring
  step; the batch shape, validation, and eval stay identical. And
  like every real batch API (Anthropic Message Batches, OpenAI Batch
  API, Gemini Batch API), the trade is latency for price: batches
  are for work that can wait — nightly scoring of a fresh job list
  is exactly that kind of work.

This module proposes only — it scores, it never submits anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .decisions import MAX_QUESTIONS_PER_BATCH, estimate_batch_cost
from .scoring import MatchResult, score_job

VERDICTS = ("match", "no_match", "needs_review")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EVAL_PATH = DATA_DIR / "eval_jobs.json"
STORE_PATH = DATA_DIR / "jobs.json"


@dataclass
class BatchQuestion:
    """One typed question in a scoring batch (Decisions-API shape)."""
    question_id: str
    question_type: str  # "choice" | "score"
    question: str
    choices: list[dict] = field(default_factory=list)  # choice: [{verdict, score}]
    chosen: str = ""          # choice: winning verdict; score: ""
    value: float = 0.0        # score: rubric value 0-1 (mean confidence)
    confidence: float = 0.0   # the pipeline confidence behind this question
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "question": self.question,
            "choices": [dict(c) for c in self.choices],
            "chosen": self.chosen,
            "value": self.value,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


# ------------------------------------------------------------ validation
def validate_batch_question(raw: dict) -> BatchQuestion:
    """Strict validation for one scoring-batch question.

    A malformed question must crash here, never silently become a
    verdict. Same spirit as decisions.validate_typed_decision, but
    the options here are verdicts (match/no_match/needs_review),
    not planner actions — a different question domain, so it gets
    its own validator instead of bending the other one.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"batch question must be an object, got {type(raw).__name__}")
    required = {"question_id", "question_type", "question", "choices",
                "chosen", "value", "confidence", "reasons"}
    if set(raw.keys()) != required:
        raise ValueError(
            f"batch question keys {sorted(raw.keys())} != required {sorted(required)}")
    qtype = raw["question_type"]
    if qtype not in ("choice", "score"):
        raise ValueError(f"invalid question_type: {qtype!r} (have ['choice', 'score'])")
    if not isinstance(raw["question"], str) or not raw["question"].strip():
        raise ValueError("question must be a non-empty string")
    if not 0 <= float(raw["value"]) <= 1:
        raise ValueError(f"value out of range 0-1: {raw['value']}")
    if not 0 <= float(raw["confidence"]) <= 1:
        raise ValueError(f"confidence out of range 0-1: {raw['confidence']}")
    if not isinstance(raw["reasons"], list) or not all(
            isinstance(r, str) for r in raw["reasons"]):
        raise ValueError("reasons must be a list of strings")

    choices = raw["choices"]
    if qtype == "choice":
        if len(choices) != 3:
            raise ValueError(f"scoring choice needs exactly 3 verdicts, got {len(choices)}")
        for c in choices:
            if set(c.keys()) != {"verdict", "score"}:
                raise ValueError(f"choice option must be {{verdict, score}}, got {c}")
            if c["verdict"] not in VERDICTS:
                raise ValueError(f"invalid choice verdict: {c['verdict']!r}")
            if not 0 <= float(c["score"]) <= 1:
                raise ValueError(f"choice score out of range 0-1: {c['score']}")
        if raw["chosen"] not in {c["verdict"] for c in choices}:
            raise ValueError(f"chosen {raw['chosen']!r} is not one of the choices")
        top = max(c["score"] for c in choices)
        chosen_score = next(c["score"] for c in choices if c["verdict"] == raw["chosen"])
        if chosen_score != top:
            raise ValueError(
                f"chosen {raw['chosen']!r} scores {chosen_score}, but the top "
                f"option scores {top} — the winner must be the highest-scored option")
    else:
        if choices:
            raise ValueError(f"score questions carry no choices, got {choices}")
        if raw["chosen"] != "":
            raise ValueError(f"score questions have no chosen verdict, got {raw['chosen']!r}")
    return BatchQuestion(
        question_id=str(raw["question_id"]), question_type=qtype,
        question=raw["question"], choices=[dict(c) for c in choices],
        chosen=raw["chosen"], value=float(raw["value"]),
        confidence=float(raw["confidence"]), reasons=list(raw["reasons"]))


# ------------------------------------------------------------ result -> question
def result_to_choice_scores(result: MatchResult) -> list[dict]:
    """Turn a pipeline verdict into 3 scored options.

    Winner = final verdict, score = confidence. The other two split
    the remainder (1 - confidence) equally. The winner stays on top
    as long as confidence > 1/3 — true for our scorer (min 0.45) —
    and validate_batch_question refuses anything else.
    """
    remainder = round((1.0 - result.confidence) / 2, 3)
    return [{"verdict": v,
             "score": result.confidence if v == result.verdict else remainder}
            for v in VERDICTS]


def job_to_choice_question(job: dict, result: MatchResult, idx: int) -> BatchQuestion:
    """One job -> one `choice` question over the 3 verdicts.

    NOTE: `result` must be the FINAL pipeline result (score_job output,
    threshold already applied) — the question asks for the decision
    we would actually act on, not the scorer's pre-threshold lean.
    """
    company = str(job.get("company", "")).strip() or "（无公司名）"
    title = str(job.get("title", "")).strip() or "（无标题）"
    return BatchQuestion(
        question_id=f"q{idx:02d}-choice-{job.get('id', idx)}",
        question_type="choice",
        question=f"{company} · {title}：这条岗位是 match / no_match / needs_review？",
        choices=result_to_choice_scores(result),
        chosen=result.verdict,
        value=result.confidence,
        confidence=result.confidence,
        reasons=list(result.reasons),
    )


# ------------------------------------------------------------ the batch
def score_jobs_batch(jobs: list[dict], prefs: dict, scorer=None) -> dict:
    """Score a list of jobs as ONE batch of choice questions.

    Per job: hard rules -> scorer -> threshold (same score_job as
    milestone 3), then packed as a choice question. Plus one
    batch-level `score` question (mean confidence). Cost is the
    Decisions-API batch estimate (shared input billed once), plus
    the per-job-call comparison so the saving is visible, not claimed.
    """
    from .scoring import heuristic_scorer  # local import: keeps module import light

    scorer = scorer or heuristic_scorer
    questions: list[BatchQuestion] = []
    results = []
    for i, job in enumerate(jobs, 1):
        result = score_job(job, prefs, scorer=scorer)
        results.append({"id": job.get("id"), "company": job.get("company"),
                        **result.to_dict()})
        questions.append(job_to_choice_question(job, result, i))

    if results:
        mean_conf = round(sum(r["confidence"] for r in results) / len(results), 3)
    else:
        mean_conf = 0.0
    questions.append(BatchQuestion(
        question_id="q00-score-batch", question_type="score",
        question="这批岗位整体的可信度？（0=全要人工，1=全可自动判定）",
        value=mean_conf, confidence=mean_conf,
        reasons=[f"mean confidence over {len(results)} jobs = {mean_conf:.2f}"]))

    if len(questions) > MAX_QUESTIONS_PER_BATCH:
        raise ValueError(
            f"batch has {len(questions)} questions, over the API cap "
            f"{MAX_QUESTIONS_PER_BATCH} — split it before sending")

    validated = [validate_batch_question(q.to_dict()).to_dict() for q in questions]
    cost = estimate_batch_cost(len(validated))
    # Comparison: one LLM call per job, each re-billing the shared input.
    per_job = estimate_batch_cost(1)
    per_job_total = {
        "num_calls": len(jobs),
        "total_input_tokens": per_job["total_input_tokens"] * len(jobs),
        "total_output_tokens": 0,
        "estimated_cost_usd": round(per_job["estimated_cost_usd"] * len(jobs), 6),
    }
    saving = (round(per_job_total["estimated_cost_usd"] / cost["estimated_cost_usd"], 2)
              if cost["estimated_cost_usd"] > 0 else 1.0)
    return {
        "questions": validated,
        "num_questions": len(validated),
        "num_jobs": len(jobs),
        "results": results,
        "batch_score": mean_conf,
        "needs_human_count": sum(1 for r in results if r["verdict"] == "needs_review"),
        "cost": cost,
        "per_job_cost": per_job_total,
        "saving_factor": saving,  # per-job cost / batch cost
    }


def build_batch_from_source(source: str = "eval", prefs: dict | None = None) -> dict:
    """Build a scoring batch from a named source.

    source='eval'  -> the 10 hand-labelled cases (data/eval_jobs.json).
    source='store' -> jobs already in data/jobs.json (no JD/salary, so
                      most will honestly land in needs_review).
    """
    import json

    from .tools import get_preferences

    prefs = prefs if prefs is not None else get_preferences()
    if source == "eval":
        with open(EVAL_PATH, encoding="utf-8") as f:
            cases = json.load(f)
        jobs = [{k: v for k, v in c.items() if k not in ("expected_verdict", "note")}
                for c in cases]
        return {"source": "eval", **score_jobs_batch(jobs, prefs)}
    if source == "store":
        from .jobs import load_store

        return {"source": "store", **score_jobs_batch(load_store(), prefs)}
    raise ValueError(f"unknown source: {source} (use 'eval' or 'store')")


def format_batch(report: dict) -> str:
    lines = [f"批量评分：{report['num_jobs']} 个岗位 → {report['num_questions']} 个问题 "
             f"（每岗 1 个 choice + 1 个 batch score）"]
    for q in report["questions"]:
        if q["question_type"] == "choice":
            opts = ", ".join(f"{c['verdict']}={c['score']:.2f}" for c in q["choices"])
            lines.append(f"  [choice] {q['question']} → {q['chosen']} "
                         f"(conf={q['confidence']:.2f}) 选项：{opts}")
        else:
            lines.append(f"  [score] {q['question']} → {q['value']:.2f}")
    lines.append(f"batch_score={report['batch_score']:.2f} "
                 f"needs_human={report['needs_human_count']} "
                 f"batch_cost=${report['cost']['estimated_cost_usd']} "
                 f"vs 逐岗 ${report['per_job_cost']['estimated_cost_usd']} "
                 f"（省约 {report['saving_factor']} 倍）")
    return "\n".join(lines)


if __name__ == "__main__":
    report = build_batch_from_source("eval")
    print(format_batch(report))
