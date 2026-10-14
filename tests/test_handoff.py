import tempfile
import unittest
from pathlib import Path

from job_agent.handoff import (
    build_snapshot,
    format_snapshot,
    load_snapshot,
    save_snapshot,
    validate_snapshot,
)

APPS = [
    {"id": "app:a", "company": "ExampleCloud", "title": "Backend",
     "status": "materials_ready", "updated_at": "2026-10-05T20:00:00+00:00"},
    {"id": "app:b", "company": "Northwind", "title": "Backend",
     "status": "needs_review", "updated_at": "2026-10-04T20:00:00+00:00"},
    {"id": "app:c", "company": "DataFlow", "title": "Backend",
     "status": "applied", "updated_at": "2026-09-28T20:00:00+00:00"},
]
CLAIMS = [
    {"id": "stripe-oa-submission", "question": "Submitted?",
     "status": "unknown", "history": []},
    {"id": "tests-green", "question": "Tests green?",
     "status": "confirmed", "history": []},
]
MEMORY = [
    {"id": "mem-001", "kind": "paused_company", "company": "Snowflake",
     "content": "paused", "source": "human"},
    {"id": "mem-002", "kind": "preference", "company": "",
     "content": "never auto-submit", "source": "human"},
]


class TestHandoffSnapshot(unittest.TestCase):
    def snap(self):
        return build_snapshot(today="2026-10-14", apps=list(APPS),
                              claims=list(CLAIMS), memory_entries=list(MEMORY))

    def test_snapshot_has_all_sections_and_boundary_date(self):
        s = self.snap()
        validate_snapshot(s)
        self.assertEqual(s["date"], "2026-10-14")
        self.assertEqual(s["departure_date"], "2026-10-15")
        self.assertEqual(s["applications"]["total"], 3)
        self.assertEqual(s["verifications"]["total"], 2)
        self.assertEqual(s["memory_entries"], 2)

    def test_unknown_travels_with_snapshot_unresolved(self):
        s = self.snap()
        self.assertEqual([u["id"] for u in s["verifications"]["unknown"]],
                         ["stripe-oa-submission"])
        self.assertTrue(s["needs_human_on_resume"])
        self.assertIn("unknown", s["resume_actions"][0])

    def test_resume_actions_order_unknown_then_review_then_stale(self):
        s = self.snap()
        joined = "\n".join(s["resume_actions"])
        self.assertLess(joined.index("unknown"), joined.index("needs_review"))
        self.assertLess(joined.index("needs_review"), joined.index("stale"))

    def test_paused_companies_sorted_from_memory(self):
        self.assertEqual(self.snap()["paused_companies"], ["snowflake"])

    def test_clean_stores_produce_single_calm_resume_action(self):
        s = build_snapshot(
            today="2026-10-14",
            apps=[{"id": "app:x", "company": "DoneCo", "title": "Backend",
                   "status": "rejected",
                   "updated_at": "2026-10-01T20:00:00+00:00"}],
            claims=[{"id": "c1", "question": "Q?", "status": "confirmed",
                     "history": []}],
            memory_entries=[])
        self.assertFalse(s["needs_human_on_resume"])
        self.assertEqual(len(s["resume_actions"]), 1)
        self.assertIn("没有积压", s["resume_actions"][0])

    def test_build_snapshot_does_not_mutate_inputs(self):
        apps, claims = list(APPS), list(CLAIMS)
        build_snapshot(today="2026-10-14", apps=apps, claims=claims,
                       memory_entries=list(MEMORY))
        self.assertEqual(apps, APPS)
        self.assertEqual(claims, CLAIMS)

    def test_validate_rejects_missing_section(self):
        s = self.snap()
        del s["verifications"]
        with self.assertRaises(ValueError):
            validate_snapshot(s)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "handoff_snapshot.json"
            self.assertIsNone(load_snapshot(path))
            save_snapshot(self.snap(), path=path)
            loaded = load_snapshot(path)
            self.assertEqual(loaded["date"], "2026-10-14")
            self.assertIn("stripe-oa-submission",
                          format_snapshot(loaded))

    def test_seeded_stores_snapshot_keeps_stripe_unknown_first(self):
        # Real files on departure eve: Stripe must still be unknown,
        # and it must be the first resume action -- not buried.
        s = build_snapshot(today="2026-10-14")
        unknown_ids = [u["id"] for u in s["verifications"]["unknown"]]
        self.assertIn("stripe-oa-submission", unknown_ids)
        self.assertIn("stripe-oa-submission", s["resume_actions"][0])


if __name__ == "__main__":
    unittest.main()
