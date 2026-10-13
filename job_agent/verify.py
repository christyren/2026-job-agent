"""Post-attempt verification ledger (2026-10-13 iteration).

Why this exists:
  Milestones 1-6 are done. Yesterday (2026-10-12) was the formal
  Stripe HackerRank day, deadline about this morning (2026-10-13).
  As of today's 11:55 scan, the submission is STILL UNCONFIRMED:
  no confirmation email in Gmail, no new Stripe mail. The honest
  state is "unknown" -- not submitted, not expired.

  Most beginner code has only two states: True / False. Real
  systems need three:

  1. UNKNOWN AS A FIRST-CLASS STATE -- "no evidence yet" is a
     legitimate, explicit state. It is NOT False (that would mean
     "we checked and it did not happen") and it is NOT True. A
     checker that silently maps missing evidence to either side
     invents a fact. This module refuses to do that.

  2. EVIDENCE REQUIRED TO LEAVE UNKNOWN -- a claim leaves
     `unknown` only when record_evidence() is called with a
     non-empty evidence string (what was seen, where) AND a source
     (who/what produced it). "I think I submitted" is not evidence;
     "HackerRank page showed Submitted at 14:32" is. Empty evidence
     raises instead of quietly recording a guess.

  3. CORRECT, DON'T REWRITE -- history is append-only, exactly
     like milestone 5. If a claim was recorded wrong, you append a
     correction (which can send it back to `unknown`); you never
     edit the old entry. summary() counts the CURRENT verdict per
     claim but keeps the full history auditable.

  can_claim(claim_id) is the guard other code should use before
  saying "X is done": it is True only for `confirmed`. `refuted`
  and `unknown` both return False, but they mean opposite things,
  so callers should read status(), not just the boolean.

Nothing here checks Gmail, HackerRank, or any external system.
It records verdicts a human (or a verified tool) supplies, with
their evidence. It never promotes a claim on its own.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STORE_PATH = DATA_DIR / "verifications.json"

STATUSES = ("unknown", "confirmed", "refuted")
# Leaving `unknown` needs evidence; returning TO `unknown` (a
# correction) does not -- doubt should never need permission.
_EVIDENCE_REQUIRED = frozenset({"confirmed", "refuted"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Claim:
    id: str                     # stable slug, e.g. "stripe-oa-submission"
    question: str               # the yes/no question being verified
    status: str = "unknown"
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def make_claim(claim_id: str, question: str, now: str | None = None) -> Claim:
    now = now or utc_now_iso()
    return Claim(
        id=claim_id, question=question, status="unknown",
        history=[{"from": None, "to": "unknown", "at": now,
                  "actor": "system", "evidence": "", "source": "",
                  "note": "claim created -- no evidence yet"}])


def record_evidence(claim: Claim, verdict: str, evidence: str = "",
                    source: str = "", actor: str = "human",
                    now: str | None = None) -> Claim:
    """Move a claim to a new verdict, appending history.

    Raises ValueError if the verdict is unknown-status, or if the
    target needs evidence and evidence/source is blank. The claim
    is left UNCHANGED when this raises -- a rejected guess must
    not corrupt the ledger.
    """
    if verdict not in STATUSES:
        raise ValueError(f"unknown verdict: {verdict!r} (have {list(STATUSES)})")
    if verdict in _EVIDENCE_REQUIRED:
        if not (evidence or "").strip():
            raise ValueError(
                f"cannot mark {claim.id!r} as {verdict} without evidence: "
                "name what was seen (page, email, timestamp)")
        if not (source or "").strip():
            raise ValueError(
                f"cannot mark {claim.id!r} as {verdict} without a source: "
                "name where the evidence came from")
    now = now or utc_now_iso()
    claim.history.append({"from": claim.status, "to": verdict, "at": now,
                          "actor": actor, "evidence": evidence,
                          "source": source, "note": ""})
    claim.status = verdict
    return claim


def can_claim(claim: Claim | dict) -> bool:
    """True ONLY for confirmed. Unknown and refuted are both False,
    but for opposite reasons -- check status when the reason matters."""
    status = claim["status"] if isinstance(claim, dict) else claim.status
    return status == "confirmed"


# ------------------------------------------------------------- durable store
def load_claims(path: Path = STORE_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_claims(claims: list[dict], path: Path = STORE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(claims, f, ensure_ascii=False, indent=2)


def upsert_claim(claim: Claim, path: Path = STORE_PATH) -> dict:
    rows = load_claims(path)
    by_id = {r["id"]: r for r in rows}
    by_id[claim.id] = claim.to_dict()
    save_claims(list(by_id.values()), path)
    return by_id[claim.id]


def record_in_store(claim_id: str, verdict: str, evidence: str = "",
                    source: str = "", actor: str = "human",
                    path: Path = STORE_PATH, now: str | None = None) -> dict:
    """Load -> record -> save. State is re-read from disk every time,
    never remembered by the agent (same rule as milestone 5)."""
    rows = load_claims(path)
    for row in rows:
        if row["id"] == claim_id:
            claim = Claim(**row)
            record_evidence(claim, verdict, evidence=evidence,
                            source=source, actor=actor, now=now)
            save_claims(
                [claim.to_dict() if r["id"] == claim_id else r for r in rows],
                path)
            return claim.to_dict()
    raise KeyError(f"unknown claim id: {claim_id}")


# ------------------------------------------------------------------ summary
def summary(claims: list[dict] | None = None, path: Path = STORE_PATH) -> dict:
    """Deterministic digest. Unknown claims are listed BY NAME --
    they are the to-do list, not a footnote."""
    claims = claims if claims is not None else load_claims(path)
    by_status = dict(Counter(c.get("status", "?") for c in claims))
    unknown = [{"id": c["id"], "question": c.get("question", "")}
               for c in claims if c.get("status") == "unknown"]
    return {
        "total": len(claims),
        "by_status": by_status,
        "unknown": unknown,
        "needs_human": bool(unknown),
        "next_actions": (
            [f"先核实 unknown：{len(unknown)} 条还没有证据，别默认任何一边"]
            if unknown else ["没有 unknown：每条都有证据支撑的结论"]),
    }


def format_summary(result: dict) -> str:
    lines = [f"核实台账：共 {result['total']} 条，按状态 {result['by_status']}"]
    for item in result["unknown"]:
        lines.append(f"  [unknown] {item['id']}: {item['question']}")
    lines += [f"  → {a}" for a in result["next_actions"]]
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_summary(summary()))
