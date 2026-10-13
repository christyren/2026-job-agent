import tempfile
import unittest
from pathlib import Path

from job_agent.verify import (
    STATUSES,
    can_claim,
    load_claims,
    make_claim,
    record_evidence,
    record_in_store,
    summary,
    upsert_claim,
)


class TestVerifyLedger(unittest.TestCase):
    def test_new_claim_starts_unknown(self):
        claim = make_claim("x", "Did X happen?", now="2026-10-13T00:00:00+00:00")
        self.assertEqual(claim.status, "unknown")
        self.assertIn(claim.status, STATUSES)
        self.assertFalse(can_claim(claim))

    def test_confirmed_without_evidence_raises_and_leaves_claim_unchanged(self):
        claim = make_claim("x", "Did X happen?")
        with self.assertRaises(ValueError):
            record_evidence(claim, "confirmed", evidence="", source="gmail")
        self.assertEqual(claim.status, "unknown")
        self.assertEqual(len(claim.history), 1)  # only the creation entry

    def test_confirmed_without_source_raises(self):
        claim = make_claim("x", "Did X happen?")
        with self.assertRaises(ValueError):
            record_evidence(claim, "confirmed",
                            evidence="page showed Submitted", source="")

    def test_confirmed_with_evidence_and_source(self):
        claim = make_claim("x", "Did X happen?")
        record_evidence(claim, "confirmed",
                        evidence="HackerRank page showed Submitted",
                        source="hackerrank-invite-page",
                        now="2026-10-13T01:00:00+00:00")
        self.assertEqual(claim.status, "confirmed")
        self.assertTrue(can_claim(claim))
        self.assertEqual(claim.history[-1]["evidence"],
                         "HackerRank page showed Submitted")

    def test_refuted_is_not_can_claim_but_is_not_unknown(self):
        claim = make_claim("x", "Is X still advancing?")
        record_evidence(claim, "refuted",
                        evidence="Rejection email received",
                        source="gmail")
        self.assertFalse(can_claim(claim))
        self.assertEqual(claim.status, "refuted")

    def test_correction_can_return_to_unknown_without_evidence(self):
        claim = make_claim("x", "Did X happen?")
        record_evidence(claim, "confirmed",
                        evidence="thought I saw a confirmation",
                        source="memory")
        # The "evidence" turns out to be a different role's email:
        # correct back to unknown -- doubt needs no permission.
        record_evidence(claim, "unknown", actor="human")
        self.assertEqual(claim.status, "unknown")
        self.assertEqual([h["to"] for h in claim.history],
                         ["unknown", "confirmed", "unknown"])

    def test_unknown_verdict_raises(self):
        claim = make_claim("x", "Did X happen?")
        with self.assertRaises(ValueError):
            record_evidence(claim, "maybe", evidence="e", source="s")

    def test_store_roundtrip_and_summary_lists_unknown_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "verifications.json"
            upsert_claim(make_claim("stripe-oa-submission", "Submitted?"),
                         path=path)
            done = make_claim("tests-green", "Tests green?")
            record_evidence(done, "confirmed", evidence="97 tests OK",
                            source="local-test-run")
            upsert_claim(done, path=path)

            rows = load_claims(path)
            self.assertEqual(len(rows), 2)
            result = summary(rows)
            self.assertEqual(result["by_status"],
                             {"unknown": 1, "confirmed": 1})
            self.assertTrue(result["needs_human"])
            self.assertEqual(result["unknown"][0]["id"],
                             "stripe-oa-submission")

            updated = record_in_store(
                "stripe-oa-submission", "confirmed",
                evidence="Invite page showed Submitted",
                source="hackerrank-invite-page", path=path)
            self.assertEqual(updated["status"], "confirmed")
            self.assertFalse(summary(load_claims(path))["needs_human"])

    def test_seeded_ledger_keeps_stripe_unknown(self):
        # The real seed file is the lesson: no confirmation email
        # in Gmail is NOT evidence of submission or of expiry.
        rows = load_claims()
        stripe = [r for r in rows if r["id"] == "stripe-oa-submission"]
        self.assertEqual(len(stripe), 1)
        self.assertEqual(stripe[0]["status"], "unknown")
        self.assertFalse(can_claim(stripe[0]))


if __name__ == "__main__":
    unittest.main()
