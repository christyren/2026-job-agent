"""Milestone 4: tailored application materials — RAG over the resume,
model routing (cheap extraction / strong generation), step cap + token log.

Pipeline (the order is the lesson, again):
1. EXTRACT (routed tier). Pull the JD's requirements out as a list.
   This is classification/extraction: bounded, repetitive, verifiable —
   exactly the work a cheap model is good enough for. Offline we simulate
   three tiers: cheap = exact keyword hits only; mid = exact hits PLUS
   the three most common paraphrases; strong = exact hits PLUS the full
   synonym map ("event streaming" -> Kafka). The eval below measures
   the recall gap instead of us guessing which tier is "good enough".
2. RETRIEVE (no LLM at all). For each requirement, find resume chunks
   whose keyword list covers it. This is the R in RAG: the facts come
   from data/resume_chunks.json (built from her real resume PDF),
   never from the model's memory.
3. GENERATE (strong tier). Build application points ONLY from retrieved
   chunks. Every point must carry evidence_chunk_id + an evidence_quote
   that is a verbatim substring of that chunk. A requirement with no
   retrieved chunk goes to `gaps` — the system says "resume has no
   evidence for this" instead of inventing experience. That grounding
   check is deterministic code (verify_grounding), not a prompt plea.
4. BUDGET. Every pipeline run goes through RunBudget: a hard max_steps
   cap (a runaway loop cannot burn money) and a token/cost log per step,
   so cost is computed per *successful task* (grounded points produced),
   not just per token — the routing question is "what does one usable
   draft cost on each tier?", and the eval answers it with numbers.

Prices below are TEACHING EXAMPLES in the same style as tools.estimate_cost
— re-check the vendor pricing page before quoting them anywhere real.
Nothing here submits an application; output is a draft for human review.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ------------------------------------------------------------- model routing
# Static routing: task type -> tier. Extraction is cheap-tier work;
# generation (the prose a recruiter actually reads) is strong-tier work.
MODEL_TIERS = {
    "cheap": {  # extraction / classification / formatting
        "model_example": "small/fast model (e.g. Haiku-class)",
        "input_price_per_million": 1.0,   # teaching example, re-check vendor page
        "output_price_per_million": 5.0,  # teaching example, re-check vendor page
    },
    # Iteration 2026-11-14: the middle tier. Gemini 4 Hydrogen (2026-11-12)
    # is Google's fast executor tier: $1.50 in / $7.50 out per million
    # tokens, flat across the whole 1M-token context window, 2.5x faster
    # than Gemini 3.8 Flash, and in Cursor + Devin on launch day.
    # It sits between cheap and strong on price — the question this
    # iteration answers is whether it also sits between them on recall,
    # and what that middle is actually worth per grounded point.
    "mid": {  # fast executor tier: common paraphrases, not the rare ones
        "model_example": "Gemini 4 Hydrogen (fast executor tier)",
        "input_price_per_million": 1.5,   # Google blog 2026-11-12, flat over 1M ctx
        "output_price_per_million": 7.5,  # same source
    },
    "strong": {  # generation / ambiguous reasoning
        "model_example": "large model (e.g. Opus-class)",
        "input_price_per_million": 5.0,   # teaching example, re-check vendor page
        "output_price_per_million": 25.0,  # teaching example, re-check vendor page
    },
}
TASK_ROUTING = {"extract_requirements": "cheap", "generate_points": "strong"}

MAX_STEPS_PER_TASK = 6  # hard cap: extract + retrieve + generate + verify <= this

# Canonical requirements we know how to look for, lower-cased for matching.
REQUIREMENT_VOCAB = [
    "Java", "Python", "C#", "TypeScript", "Angular", "distributed systems",
    "REST API", "microservices", "Kafka", "AWS", "Azure", "SQL", "Docker",
    "CI/CD", "Spark", "ASP.NET Core", "Spring Boot", "Go", "Kubernetes",
    "GraphQL", "React", "machine learning",
]
# Strong tier only: phrases a JD uses that mean a vocab term without naming it.
SYNONYM_MAP = {
    "event streaming": "Kafka",
    "message queue": "Kafka",
    "stream processing": "Kafka",
    "api design": "REST API",
    "cloud infrastructure": "AWS",
    "relational database": "SQL",
    "containerization": "Docker",
    "containerised": "Docker",
    "decoupled services": "microservices",
}
# Mid tier only: the three most common paraphrases — a strict subset of
# SYNONYM_MAP. The mid tier is "cheap + the paraphrases you see every
# day"; the rarer ones (message queue / stream processing / decoupled
# services / containerised ...) stay strong-only. That is the whole
# bet of a middle tier: most JDs phrase things the common way, so most
# of the recall gain is already in these three lines — and the eval
# below measures exactly how much of the gap they close.
MID_SYNONYM_MAP = {
    "event streaming": "Kafka",
    "api design": "REST API",
    "relational database": "SQL",
}

MATERIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "job_company": {"type": "string"},
        "points": {"type": "array", "items": {"type": "object"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["job_company", "points", "gaps"],
    "additionalProperties": False,
}


def estimate_tokens(text: str) -> int:
    """Rough offline token estimate (~4 chars/token for English). Good enough
    to compare tiers; a real integration reads usage from the API response."""
    return max(1, math.ceil(len(text) / 4))


def tier_cost_usd(tier: str, input_tokens: int, output_tokens: int) -> float:
    t = MODEL_TIERS[tier]
    return round(
        input_tokens / 1_000_000 * t["input_price_per_million"]
        + output_tokens / 1_000_000 * t["output_price_per_million"], 6)


@dataclass
class RunBudget:
    """Step cap + token log for one task run. The agent-loop max_steps idea
    from milestone 1, applied per pipeline task, with accounting attached."""
    max_steps: int = MAX_STEPS_PER_TASK
    steps: int = 0
    log: list[dict] = field(default_factory=list)

    def step(self, name: str, tier: str, input_text: str, output_text: str) -> dict:
        self.steps += 1
        if self.steps > self.max_steps:
            raise RuntimeError(
                f"step cap exceeded: {self.steps} > {self.max_steps} "
                f"(refusing to keep spending on step {name!r})")
        entry = {
            "step": self.steps, "name": name, "tier": tier,
            "input_tokens": estimate_tokens(input_text),
            "output_tokens": estimate_tokens(output_text),
        }
        entry["cost_usd"] = tier_cost_usd(tier, entry["input_tokens"], entry["output_tokens"])
        self.log.append(entry)
        return entry

    @property
    def total_cost_usd(self) -> float:
        return round(sum(e["cost_usd"] for e in self.log), 6)


# ------------------------------------------------------------------- resume
@dataclass
class ResumeChunk:
    id: str
    section: str
    text: str
    keywords: list[str]

    def covers(self, requirement: str) -> bool:
        r = requirement.lower()
        return any(r == k.lower() or r in k.lower() for k in self.keywords)


def load_resume_chunks(path: Path = DATA_DIR / "resume_chunks.json") -> list[ResumeChunk]:
    with open(path, encoding="utf-8") as f:
        return [ResumeChunk(**c) for c in json.load(f)]


def retrieve(requirement: str, chunks: list[ResumeChunk]) -> ResumeChunk | None:
    """Keyword retrieval: first chunk whose keywords cover the requirement.
    Deliberately simple — at 8 chunks, embeddings would be ceremony.
    The interface (requirement -> chunk | None) is what a vector store
    would later replace without touching the generation code."""
    for chunk in chunks:
        if chunk.covers(requirement):
            return chunk
    return None


# ---------------------------------------------------------------- extraction
def extract_requirements(job: dict, tier: str = "cheap") -> list[str]:
    """Pull requirements from the JD text. tier='cheap': exact vocab hits
    only. tier='mid': exact hits PLUS the three common paraphrases in
    MID_SYNONYM_MAP. tier='strong': exact hits PLUS the full SYNONYM_MAP.
    Order follows the vocab list so results are deterministic and
    diff-able in eval."""
    if tier not in MODEL_TIERS:
        raise ValueError(f"unknown tier: {tier}")
    text = (str(job.get("title", "")) + " " + str(job.get("description", ""))).lower()
    found: list[str] = []
    for term in REQUIREMENT_VOCAB:
        if term.lower() in text:
            found.append(term)
    synonym_map = {"mid": MID_SYNONYM_MAP, "strong": SYNONYM_MAP}.get(tier, {})
    for phrase, term in synonym_map.items():
        if phrase in text and term not in found:
            found.append(term)
    return found


# ---------------------------------------------------------------- generation
def verify_grounding(points: list[dict], chunks: list[ResumeChunk]) -> list[dict]:
    """Deterministic faithfulness check: every point must cite a real chunk
    and quote it verbatim. Returns the list of violations (empty = grounded).
    This runs AFTER generation and can veto it — a prompt saying 'do not
    hallucinate' cannot veto anything."""
    by_id = {c.id: c for c in chunks}
    violations = []
    for p in points:
        chunk = by_id.get(p.get("evidence_chunk_id", ""))
        if chunk is None:
            violations.append({"point": p.get("requirement"), "why": "unknown chunk id"})
        elif p.get("evidence_quote", "") not in chunk.text:
            violations.append({"point": p.get("requirement"), "why": "quote not in chunk"})
    return violations


def validate_materials(raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw.keys()) != set(MATERIAL_SCHEMA["required"]):
        raise ValueError("materials must have exactly: job_company, points, gaps")
    if not isinstance(raw["points"], list) or not isinstance(raw["gaps"], list):
        raise ValueError("points and gaps must be lists")
    for p in raw["points"]:
        if set(p.keys()) != {"requirement", "evidence_chunk_id", "evidence_quote", "point"}:
            raise ValueError(f"bad point shape: {sorted(p.keys())}")
    return raw


def generate_materials(job: dict, chunks: list[ResumeChunk] | None = None,
                       extract_tier: str = "cheap",
                       budget: RunBudget | None = None) -> dict:
    """Full milestone-4 pipeline for one job. Returns the validated draft
    plus the budget log (steps, tokens, cost) for inspection."""
    chunks = chunks if chunks is not None else load_resume_chunks()
    budget = budget if budget is not None else RunBudget()
    jd_text = str(job.get("description", ""))

    # Step 1 (routed tier): extraction. The budget must log the tier that
    # actually ran — earlier code logged "strong" for anything not cheap,
    # which would have billed the new mid tier at strong prices.
    requirements = extract_requirements(job, tier=extract_tier)
    budget.step("extract_requirements", extract_tier,
                jd_text, json.dumps(requirements))

    # Step 2 (no LLM): retrieval + grounded generation.
    # Offline, the 'strong model' is simulated by composing each point
    # directly from the retrieved chunk text — which is also exactly the
    # constraint a real strong-model prompt would carry.
    points, gaps = [], []
    for req in requirements:
        chunk = retrieve(req, chunks)
        if chunk is None:
            gaps.append(req)  # no evidence in resume -> say so, never invent
        else:
            points.append({
                "requirement": req,
                "evidence_chunk_id": chunk.id,
                "evidence_quote": chunk.text,
                "point": f"For {req}: {chunk.text}",
            })
    budget.step("retrieve_and_generate", TASK_ROUTING["generate_points"],
                " ".join(p["evidence_quote"] for p in points) or "(no chunks)",
                " ".join(p["point"] for p in points) or "(no points)")

    # Step 3 (no LLM): grounding veto
    violations = verify_grounding(points, chunks)
    budget.step("verify_grounding", "cheap", json.dumps(points), json.dumps(violations))
    if violations:
        raise ValueError(f"ungrounded points slipped through: {violations}")

    draft = validate_materials({
        "job_company": str(job.get("company", "?")), "points": points, "gaps": gaps})
    return {**draft, "extract_tier": extract_tier,
            "steps_used": budget.steps, "token_log": budget.log,
            "total_cost_usd": budget.total_cost_usd,
            "cost_per_grounded_point_usd": (
                round(budget.total_cost_usd / len(points), 6) if points else None)}
