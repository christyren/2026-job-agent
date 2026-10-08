"""Post-milestone iteration (2026-10-08): multi-stage practice template.

Why this exists:
  Stripe-style HackerRank problems are NOT one hard algorithm. They are
  several small parts in a row (Part 1, Part 2, Part 3...), where each
  part adds a rule on top of the last. Candidates fail less on ideas
  than on structure: if Part 1 mixes reading, computing, and printing
  into one blob, Part 2 forces a rewrite of everything.

The template — three stages, strictly separated:
  1. parse_records(text) -> list[Record]
     ONLY turns raw text into typed records. No totals, no printing.
  2. calculate_totals(records) -> dict[user, Decimal]
     ONLY computes. No parsing, no formatting, no rounding to cents
     here except the exact Decimal sum.
  3. format_output(totals) -> str
     ONLY formats. Sorting and quantizing to cents live here, nowhere
     else, so a format change never touches the math.

  run_pipeline(text) is the only function that composes the three.

Money rule (the one that bites in payment problems):
  Amounts are Decimal built from the *string* in the input, never
  float. float('0.1') + float('0.2') != 0.3; Decimal('0.1') +
  Decimal('0.2') == Decimal('0.3'). Quantize to cents only at the
  output boundary, with an explicit rounding mode.

Per-stage self-checks (also in SELF_CHECKS below; run them mentally
after EACH part in a timed practice, not only at the very end):
  parse : empty input? blank lines? duplicate txn_id? malformed line?
  calc  : precision (0.10 + 0.20)? duplicates already removed in
          parse, so calc must NOT silently double-count? empty list?
  output: sorted deterministically? every amount quantized to 0.01?
          empty totals produce an explicit line, not a crash / blank?

Copy this shape tomorrow: keep the three function names, replace the
Record fields and the aggregation key with the problem's own.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")

SELF_CHECKS: dict[str, list[str]] = {
    "parse": [
        "empty input returns [] (not a crash)",
        "blank lines are skipped",
        "duplicate txn_id: first record wins, later ones are skipped",
        "malformed line raises ValueError with the line number",
    ],
    "calc": [
        "amounts are Decimal from strings, never float (0.10 + 0.20 == 0.30)",
        "duplicate txn_ids are already gone, so no double-counting here",
        "empty record list returns {}",
    ],
    "output": [
        "users sorted alphabetically (deterministic output)",
        "every amount quantized to CENT with ROUND_HALF_UP",
        "empty totals return '(no transactions)', not '' or a crash",
    ],
}


@dataclass(frozen=True)
class Record:
    txn_id: str
    user: str
    amount: Decimal


# ---------------------------------------------------------------- stage 1
def parse_records(text: str) -> list[Record]:
    """Parse lines of 'txn_id,user,amount'. ONLY parsing lives here.

    Rules (each one is a self-check above):
    - empty / whitespace-only input -> []
    - blank lines skipped
    - duplicate txn_id: keep the first, skip later duplicates
      (idempotency, same idea as milestone 2's fingerprint dedup)
    - malformed line -> ValueError naming the 1-based line number
    """
    if not text or not text.strip():
        return []
    records: list[Record] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3 or not parts[0] or not parts[1]:
            raise ValueError(f"line {lineno}: expected 'txn_id,user,amount', got {raw!r}")
        txn_id, user, amount_text = parts
        if txn_id in seen:
            continue  # duplicate: first wins
        try:
            amount = Decimal(amount_text)  # from string, never float(...)
        except InvalidOperation as exc:
            raise ValueError(f"line {lineno}: bad amount {amount_text!r}") from exc
        seen.add(txn_id)
        records.append(Record(txn_id=txn_id, user=user, amount=amount))
    return records


# ---------------------------------------------------------------- stage 2
def calculate_totals(records: list[Record]) -> dict[str, Decimal]:
    """Sum amounts per user. ONLY computation lives here.

    No rounding to cents in this stage: keep the exact Decimal sum,
    rounding is an output concern. Empty input -> {}.
    """
    totals: dict[str, Decimal] = {}
    for rec in records:
        totals[rec.user] = totals.get(rec.user, Decimal("0")) + rec.amount
    return totals


# ---------------------------------------------------------------- stage 3
def format_output(totals: dict[str, Decimal]) -> str:
    """Format totals as sorted 'user: $X.XX' lines. ONLY formatting.

    Quantize happens here and only here, with an explicit mode, so
    changing display rules never touches parsing or math.
    """
    if not totals:
        return "(no transactions)"
    lines = []
    for user in sorted(totals):
        amount = totals[user].quantize(CENT, rounding=ROUND_HALF_UP)
        lines.append(f"{user}: ${amount}")
    return "\n".join(lines)


# ---------------------------------------------------------------- compose
def run_pipeline(text: str) -> str:
    """The only place the three stages are composed, in order."""
    return format_output(calculate_totals(parse_records(text)))


_DEMO = "t1,alice,10.10\nt2,bob,0.10\nt3,alice,0.20\nt2,bob,999.99\n"

if __name__ == "__main__":
    print("demo input:")
    print(_DEMO)
    print("demo output (note: duplicate t2 is ignored, 0.10 + ... stays exact):")
    print(run_pipeline(_DEMO))
    print("\nself-checks:")
    for stage, checks in SELF_CHECKS.items():
        print(f"  [{stage}]")
        for check in checks:
            print(f"    - {check}")
