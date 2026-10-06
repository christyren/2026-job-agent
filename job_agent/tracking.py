"""Milestone 5: application tracking — finite state machine + durable store
+ daily summary.

Concepts:
- Finite State Machine (FSM): an application is always in exactly ONE
  status, and it may only move along edges listed in TRANSITIONS.
  Illegal moves (e.g. rejected -> interview) raise instead of silently
  corrupting the record. The LLM never decides legality — this dict does.
- Durable state / external memory: the source of truth is
  data/applications.json on disk, re-read on every call. Agent memory
  (the conversation / LLM context) is volatile and forgetful; a file
  survives restarts and can be diffed, backed up, and audited.
  Rule of thumb: if losing it would hurt, it does not live in a prompt.
- History as an append-only log: every transition appends
  {from, to, at, actor, note} and never rewrites old entries. That is
  what makes "when did we apply, and who moved it to interview?" answerable.
- Daily summary is deterministic code, not an LLM summary: counts come
  from Counter over the store, stale = applied/replied with no update
  for >= stale_days, and anything flagged needs_review (low confidence
  in milestone 3, or a run that hit its step/cost cap in milestone 4)
  is listed for a human — never auto-resolved.
- Cost line: one line per day — tasks / steps / estimated cost — reusing
  the milestone-4 idea of accounting per task. If steps or cost exceed
  the caps, the summary marks the run needs_human instead of hiding it.

Nothing here submits, withdraws, or messages anyone. Transitions that
mean real-world action (applied / withdrawn) are recorded only after a
human did or approved that action.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STORE_PATH = DATA_DIR / "applications.json"

# --------------------------------------------------------------------- FSM
STATUSES = (
    "new",              # just ingested, not scored yet
    "scored",           # milestone 3 verdict recorded
    "needs_review",     # low confidence / over cap -> a human must look
    "materials_ready",  # milestone 4 draft exists and was reviewed
    "applied",          # human submitted (recorded after the fact)
    "replied",          # recruiter / company replied
    "interview",        # in interview loop
    "offer",            # offer received (terminal, except withdraw)
    "rejected",         # company said no (terminal)
    "withdrawn",        # we withdrew (terminal)
)
TERMINAL = frozenset({"rejected", "withdrawn"})

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "new": ("scored", "needs_review", "withdrawn"),
    "scored": ("materials_ready", "needs_review", "withdrawn"),
    "needs_review": ("scored", "materials_ready", "withdrawn"),
    "materials_ready": ("applied", "needs_review", "withdrawn"),
    "applied": ("replied", "interview", "rejected", "withdrawn"),
    "replied": ("interview", "rejected", "withdrawn"),
    "interview": ("offer", "rejected", "withdrawn"),
    "offer": ("withdrawn",),
    "rejected": (),
    "withdrawn": (),
}

# Caps for the one-line run accounting in the daily summary.
MAX_STEPS_PER_DAY = 50        # teaching example: tune to real usage
MAX_COST_USD_PER_DAY = 5.00   # teaching example: tune to real budget


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def can_transition(frm: str, to: str) -> bool:
    return to in TRANSITIONS.get(frm, ())


@dataclass
class Application:
    id: str               # stable id, e.g. "app:<job_id>" or a slug
    company: str
    title: str
    status: str = "new"
    job_id: str = ""
    url: str = ""
    created_at: str = ""
    updated_at: str = ""
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def make_application(company: str, title: str, job_id: str = "", url: str = "",
                     app_id: str = "", now: str | None = None) -> Application:
    now = now or utc_now_iso()
    slug = app_id or f"app:{job_id}" if job_id else (
        "app:" + "-".join((company + "-" + title).lower().split())[:60])
    return Application(
        id=slug, company=company, title=title, status="new", job_id=job_id,
        url=url, created_at=now, updated_at=now,
        history=[{"from": None, "to": "new", "at": now,
                  "actor": "system", "note": "created"}])


def transition(app: Application, to: str, note: str = "",
               actor: str = "human", now: str | None = None) -> Application:
    """Move one application along a legal FSM edge, appending history.
    Raises ValueError on an unknown status or an illegal edge — the
    caller must fix the workflow, not force the state."""
    if to not in STATUSES:
        raise ValueError(f"unknown status: {to!r} (have {list(STATUSES)})")
    if not can_transition(app.status, to):
        raise ValueError(
            f"illegal transition: {app.status} -> {to} "
            f"(allowed from {app.status}: {list(TRANSITIONS[app.status]) or 'none — terminal'})")
    now = now or utc_now_iso()
    app.history.append({"from": app.status, "to": to, "at": now,
                        "actor": actor, "note": note})
    app.status = to
    app.updated_at = now
    return app


# ------------------------------------------------------------- durable store
def load_applications(path: Path = STORE_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_applications(apps: list[dict], path: Path = STORE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)


def upsert_application(app: Application, path: Path = STORE_PATH) -> dict:
    """Insert or replace by id, then persist. Returns the stored row."""
    rows = load_applications(path)
    by_id = {r["id"]: r for r in rows}
    by_id[app.id] = app.to_dict()
    save_applications(list(by_id.values()), path)
    return by_id[app.id]


def transition_in_store(app_id: str, to: str, note: str = "",
                        actor: str = "human", path: Path = STORE_PATH,
                        now: str | None = None) -> dict:
    """Load -> transition -> save. The load/save pair is the whole point:
    state is re-read from disk every time, never remembered by the agent."""
    rows = load_applications(path)
    for row in rows:
        if row["id"] == app_id:
            app = Application(**row)
            transition(app, to, note=note, actor=actor, now=now)
            save_applications(
                [app.to_dict() if r["id"] == app_id else r for r in rows], path)
            return app.to_dict()
    raise KeyError(f"unknown application id: {app_id}")


# ------------------------------------------------------------ daily summary
def _days_since(iso_ts: str, today: date) -> int:
    try:
        return (today - datetime.fromisoformat(iso_ts).date()).days
    except (ValueError, TypeError):
        return 0


def daily_summary(apps: list[dict] | None = None, today: str | None = None,
                  stale_days: int = 7, run_stats: dict | None = None,
                  path: Path = STORE_PATH) -> dict:
    """Deterministic daily digest over the durable store.

    run_stats (optional, from milestone-4 style budget logs):
    {"tasks": int, "steps": int, "cost_usd": float}. If steps or cost
    exceed the daily caps, over_cap=True and the day is flagged
    needs_human — an over-budget run is surfaced, never silently absorbed.
    """
    apps = apps if apps is not None else load_applications(path)
    day = date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    by_status = dict(Counter(a.get("status", "?") for a in apps))

    needs_review = [{"id": a["id"], "company": a.get("company", "?")}
                    for a in apps if a.get("status") == "needs_review"]
    stale = [{"id": a["id"], "company": a.get("company", "?"),
              "status": a["status"], "days": _days_since(a.get("updated_at", ""), day)}
             for a in apps
             if a.get("status") in ("applied", "replied")
             and _days_since(a.get("updated_at", ""), day) >= stale_days]
    in_interview = [{"id": a["id"], "company": a.get("company", "?")}
                    for a in apps if a.get("status") == "interview"]

    stats = run_stats or {"tasks": 0, "steps": 0, "cost_usd": 0.0}
    over_cap = (stats.get("steps", 0) > MAX_STEPS_PER_DAY
                or stats.get("cost_usd", 0.0) > MAX_COST_USD_PER_DAY)
    cost_line = (f"今日任务 {stats.get('tasks', 0)} 个 / 步数 {stats.get('steps', 0)}"
                 f" / 估算成本 ${stats.get('cost_usd', 0.0):.4f}"
                 + (" ⚠ 超上限，待人工" if over_cap else ""))

    next_actions: list[str] = []
    if needs_review:
        next_actions.append(f"先清 needs_review：{len(needs_review)} 条待人工决定")
    if stale:
        next_actions.append(f"跟进沉默申请：{len(stale)} 条已 ≥{stale_days} 天无更新")
    if in_interview:
        next_actions.append(f"面试中：{len(in_interview)} 条，优先准备")
    if not next_actions:
        next_actions.append("今天没有待跟进项")

    return {
        "date": day.isoformat(),
        "total": len(apps),
        "by_status": by_status,
        "needs_review": needs_review,
        "stale": stale,
        "in_interview": in_interview,
        "cost_line": cost_line,
        "over_cap": over_cap,
        "needs_human": bool(needs_review) or over_cap,
        "next_actions": next_actions,
    }


def format_summary(summary: dict) -> str:
    lines = [f"每日汇总 {summary['date']}：共 {summary['total']} 条申请",
             f"  按状态：{summary['by_status']}",
             f"  {summary['cost_line']}"]
    lines += [f"  → {a}" for a in summary["next_actions"]]
    return "\n".join(lines)
