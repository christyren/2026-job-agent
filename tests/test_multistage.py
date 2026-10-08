"""Tests for the multi-stage template (2026-10-08 iteration).

Each test is named after the self-check it proves, so a red test in
timed practice tells you exactly which stage and which check broke.
"""
import unittest
from decimal import Decimal

from job_agent.multistage import (
    SELF_CHECKS,
    calculate_totals,
    format_output,
    parse_records,
    run_pipeline,
)


class TestParseStage(unittest.TestCase):
    def test_empty_input_returns_empty_list(self):
        self.assertEqual(parse_records(""), [])
        self.assertEqual(parse_records("  \n \n"), [])

    def test_blank_lines_are_skipped(self):
        records = parse_records("t1,alice,1.00\n\n  \nt2,bob,2.00\n")
        self.assertEqual([r.txn_id for r in records], ["t1", "t2"])

    def test_duplicate_txn_id_first_wins(self):
        records = parse_records("t1,alice,1.00\nt1,alice,999.99\n")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].amount, Decimal("1.00"))

    def test_malformed_line_raises_with_line_number(self):
        with self.assertRaises(ValueError) as ctx:
            parse_records("t1,alice,1.00\nnot-a-valid-line\n")
        self.assertIn("line 2", str(ctx.exception))

    def test_bad_amount_raises(self):
        with self.assertRaises(ValueError):
            parse_records("t1,alice,not-a-number\n")


class TestCalcStage(unittest.TestCase):
    def test_money_precision_decimal_not_float(self):
        # The payment trap: with float this sum is 0.30000000000000004.
        records = parse_records("t1,alice,0.10\nt2,alice,0.20\n")
        self.assertEqual(calculate_totals(records), {"alice": Decimal("0.30")})

    def test_empty_records_return_empty_totals(self):
        self.assertEqual(calculate_totals([]), {})

    def test_totals_group_by_user(self):
        records = parse_records("t1,alice,1.00\nt2,bob,2.00\nt3,alice,3.00\n")
        self.assertEqual(
            calculate_totals(records),
            {"alice": Decimal("4.00"), "bob": Decimal("2.00")},
        )


class TestOutputStage(unittest.TestCase):
    def test_sorted_and_quantized(self):
        out = format_output({"bob": Decimal("2"), "alice": Decimal("10.005")})
        # sorted by user; 10.005 -> 10.01 with explicit ROUND_HALF_UP
        self.assertEqual(out, "alice: $10.01\nbob: $2.00")

    def test_empty_totals_explicit_line(self):
        self.assertEqual(format_output({}), "(no transactions)")

    def test_pipeline_end_to_end(self):
        text = "t1,alice,10.10\nt2,bob,0.10\nt3,alice,0.20\nt2,bob,999.99\n"
        # duplicate t2 ignored; alice 10.10 + 0.20 = 10.30 exactly
        self.assertEqual(run_pipeline(text), "alice: $10.30\nbob: $0.10")

    def test_pipeline_empty_input(self):
        self.assertEqual(run_pipeline(""), "(no transactions)")

    def test_self_checks_cover_all_three_stages(self):
        self.assertEqual(sorted(SELF_CHECKS), ["calc", "output", "parse"])
        joined = " ".join(c for checks in SELF_CHECKS.values() for c in checks)
        for keyword in ("empty", "duplicate", "Decimal"):
            self.assertIn(keyword, joined)


if __name__ == "__main__":
    unittest.main()
