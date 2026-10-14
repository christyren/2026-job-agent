"""Post-milestone iteration 2026-10-14: departure checkpoint / handoff snapshot.

Why this exists:
  Tomorrow (2026-10-15) the human leaves for Guangzhou; the next
  normal study day is after the return in early December. Milestones
  1-6 and iterations 1-4 left state in FOUR durable files. If resume
  relies on "what do I remember from October?", it will drift.

Concepts:
  1. CHECKPOINT / SNAPSHOT -- at a chosen boundary (here: departure
     eve), freeze a deterministic summary of all durable stores into
     ONE snapshot file. Resume loads the snapshot first, then re-reads
     the live stores and reconciles -- it does not replay chat history.
     Checkpointing saves state at boundaries; the recovery unit is
     "the next action after the boundary", not every step inside it.

  2. SNAPSHOT IS NOT A NEW SOURCE OF TRUTH -- applications.json,
     verifications.json and agent_memory.json stay authoritative.
     The snapshot is a dated, read-only-at-creation view: it says
     "this is exactly what we believed on 2026-10-14". If a live store
     later disagrees, that disagreement is a finding to reconcile,
     not a reason to silently overwrite either side.

  3. UNKNOWN TRAVELS WITH THE SNAPSHOT -- an unknown claim (Stripe OA)
     is copied into the snapshot AS unknown, with its resume action
     first in the list. A pause must never launder unknown into
     confirmed / refuted just because time passed. Time passing is
     not evidence (same rule as the verification ledger).

  4. RESUME ACTIONS ARE DETERMINISTIC AND ORDERED -- unknowns first,
     then needs_review, then stale, then interviews. The order is
     code, not an LLM essay, so the same stores always produce the
     same resume list and tests can pin it.

Nothing here sends, submits, or changes any application / claim /
memory entry. build_snapshot() only reads; save_snapshot() only
writes the snapshot file itself.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_PATH = DATA_DIR / "handoff_snapshot.json"

SNAPSHOT_VERSION = 1
# The boundary this snapshot was designed for; stored as data, not
# guessed from the clock, so a later resume can see it verbatim.
DEPARTURE_DATE = "2026-10-15"


def build_snapshot(today: str | None = None, apps: list[dict] | None = None,
                   claims: list[dict] | None = None,
                   memory_entries: list[dict] | None = None) -> dict:
    """Read all durable stores (or use injected rows) -> one snapshot.

    Pure over its inputs: same rows + same date = same snapshot,
    apart from nothing. No clock, no LLM, no mutation of inputs.
    """
    from .planning import paused_companies
    from .tracking import daily_summary, load_applications
    from .verify import load_claims, summary as verify_summary
    from .planning import load_memory

    day = today or datetime.now(timezone.utc).date().isoformat()
    apps = apps if apps is not None else load_applications()
    claims = claims if claims is not None else load_claims()
    memory_entries = (
        memory_entries if memory_entries is not None else load_memory())

    track = daily_summary(apps, today=day)
    verif = verify_summary(claims)
    paused = sorted(paused_companies(memory_entries))

    resume_actions: list[str] = []
    if verif["unknown"]:
        ids = ", ".join(item["id"] for item in verif["unknown"])
        resume_actions.append(
            f"先核实 unknown（{len(verif['unknown'])} 条）：{ids} — "
            "没有新证据前不许改成 confirmed / refuted，时间过去不算证据")
    if track["needs_review"]:
        resume_actions.append(
            f"清 needs_review（{len(track['needs_review'])} 条）："
            "停车位只能由人清，planner 不能代清")
    if track["stale"]:
        resume_actions.append(
            f"跟进 stale（{len(track['stale'])} 条）：先确认还值不值得跟，"
            "再决定跟不跟，不要回来第一天就群发")
    if track["in_interview"]:
        resume_actions.append(
            f"面试中（{len(track['in_interview'])} 条）：先看邮箱有没有新进展，"
            "状态变了先入台账、再改 applications.json")
    if not resume_actions:
        resume_actions.append("没有积压：从 planner 的当日计划重新开始即可")

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "date": day,
        "departure_date": DEPARTURE_DATE,
        "note": (
            "Departure checkpoint — read-only view at creation. "
            "Live stores stay authoritative; reconcile any drift, "
            "do not silently overwrite either side."),
        "applications": {
            "total": track["total"],
            "by_status": track["by_status"],
            "needs_review": track["needs_review"],
            "stale": track["stale"],
            "in_interview": track["in_interview"],
        },
        "verifications": {
            "total": verif["total"],
            "by_status": verif["by_status"],
            "unknown": verif["unknown"],
        },
        "paused_companies": paused,
        "memory_entries": len(memory_entries),
        "resume_actions": resume_actions,
        "needs_human_on_resume": bool(verif["unknown"] or track["needs_review"]),
    }


def validate_snapshot(snap: dict) -> dict:
    """A snapshot missing a section must fail loudly on resume,
    not produce a half-empty plan that looks complete."""
    required = {"snapshot_version", "date", "departure_date",
                "applications", "verifications", "paused_companies",
                "resume_actions", "needs_human_on_resume"}
    missing = required - set(snap.keys())
    if missing:
        raise ValueError(f"snapshot missing keys: {sorted(missing)}")
    if snap["snapshot_version"] != SNAPSHOT_VERSION:
        raise ValueError(
            f"unsupported snapshot_version: {snap['snapshot_version']!r}")
    if not isinstance(snap["resume_actions"], list) or not snap["resume_actions"]:
        raise ValueError("snapshot resume_actions must be a non-empty list")
    return snap


def save_snapshot(snap: dict, path: Path = SNAPSHOT_PATH) -> dict:
    validate_snapshot(snap)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return snap


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict | None:
    if not Path(path).exists():
        return None
    with open(path, encoding="utf-8") as f:
        return validate_snapshot(json.load(f))


def format_snapshot(snap: dict) -> str:
    lines = [
        f"出发前快照 {snap['date']}（出发日 {snap['departure_date']}）",
        f"  申请：共 {snap['applications']['total']} 条，"
        f"按状态 {snap['applications']['by_status']}",
        f"  核实：共 {snap['verifications']['total']} 条，"
        f"按状态 {snap['verifications']['by_status']}",
        f"  暂停公司：{snap['paused_companies'] or '无'}；"
        f"长期 memory {snap['memory_entries']} 条",
    ]
    for item in snap["verifications"]["unknown"]:
        lines.append(f"  [unknown 随快照保留] {item['id']}: {item['question']}")
    lines.append("  回来先做（按顺序）：")
    lines += [f"    {i}. {a}" for i, a in enumerate(snap["resume_actions"], 1)]
    if snap["needs_human_on_resume"]:
        lines.append("  ⚠ 回来时有待人工项，先清它们再开新投递")
    return "\n".join(lines)


if __name__ == "__main__":
    snapshot = build_snapshot(today=date.today().isoformat())
    save_snapshot(snapshot)
    print(format_snapshot(snapshot))
    print(f"\n已写入 {SNAPSHOT_PATH}")
