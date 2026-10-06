"""Milestone 5 tests: FSM legality, durable store, daily summary, cost cap.
Run with: python3 -m unittest discover -s tests -v  (no API key, no network)"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.tools import dispatch_tool
from job_agent.tracking import (
    MAX_COST_USD_PER_DAY,
    can_transition,
    daily_summary,
    load_applications,
    make_application,
    save_applications,
    transition,
    transition_in_store,
    upsert_application,
)

NOW = "2026-10-06T20:10:00+00:00"


def _app(status_path=("new",)):
    app = make_application("Acme", "Backend Engineer", job_id="j1", now=NOW)
    for to in status_path[1:]:
        transition(app, to, now=NOW)
    return app


class TestStateMachine(unittest.TestCase):
    def test_happy_path_full_lifecycle(self):
        app = _app()
        for to in ("scored", "materials_ready", "applied", "replied",
                   "interview", "offer"):
            transition(app, to, now=NOW)
        self.assertEqual(app.status, "offer")
        self.assertEqual(len(app.history), 7)  # created + 6 moves

    def test_illegal_jump_is_refused(self):
        app = _app()
        with self.assertRaises(ValueError):
            transition(app, "interview")  # new cannot skip to interview
        self.assertEqual(app.status, "new")  # refused move changes nothing

    def test_terminal_states_have_no_exits(self):
        app = _app(("new", "scored", "materials_ready", "applied", "rejected"))
        self.assertFalse(can_transition("rejected", "interview"))
        with self.assertRaises(ValueError):
            transition(app, "interview", now=NOW)

    def test_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            transition(_app(), "hired", now=NOW)

    def test_needs_review_can_return_to_flow(self):
        # The human-in-the-loop loop: low confidence parks the application,
        # a human decision sends it back into the normal flow.
        app = _app(("new", "needs_review", "scored"))
        self.assertEqual(app.status, "scored")


class TestDurableStore(unittest.TestCase):
    def test_state_survives_reload_from_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "applications.json"
            app = _app(("new", "scored"))
            upsert_application(app, path=path)
            reloaded = load_applications(path)
            self.assertEqual(reloaded[0]["status"], "scored")
            self.assertEqual(len(reloaded[0]["history"]), 2)

    def test_transition_in_store_rereads_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "applications.json"
            upsert_application(_app(), path=path)
            row = transition_in_store("app:j1", "needs_review", now=NOW, path=path)
            self.assertEqual(row["status"], "needs_review")
            self.assertEqual(load_applications(path)[0]["status"], "needs_review")

    def test_transition_in_store_unknown_id_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "applications.json"
            save_applications([], path)
            with self.assertRaises(KeyError):
                transition_in_store("app:nope", "scored", path=path)


class TestDailySummary(unittest.TestCase):
    def _apps(self):
        return [
            {"id": "a1", "company": "Old", "status": "applied",
             "updated_at": "2026-09-28T20:00:00+00:00"},
            {"id": "a2", "company": "Parked", "status": "needs_review",
             "updated_at": "2026-10-06T20:00:00+00:00"},
            {"id": "a3", "company": "Live", "status": "interview",
             "updated_at": "2026-10-06T20:00:00+00:00"},
            {"id": "a4", "company": "Fresh", "status": "applied",
             "updated_at": "2026-10-05T20:00:00+00:00"},
        ]

    def test_counts_stale_and_review(self):
        s = daily_summary(self._apps(), today="2026-10-06")
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["by_status"]["applied"], 2)
        self.assertEqual([x["id"] for x in s["stale"]], ["a1"])  # 8 days, not a4 (1 day)
        self.assertEqual([x["id"] for x in s["needs_review"]], ["a2"])
        self.assertTrue(s["needs_human"])

    def test_cost_line_and_over_cap_flag(self):
        ok = daily_summary(self._apps(), today="2026-10-06",
                           run_stats={"tasks": 3, "steps": 9, "cost_usd": 0.12})
        self.assertFalse(ok["over_cap"])
        self.assertIn("3", ok["cost_line"])
        over = daily_summary(self._apps(), today="2026-10-06",
                             run_stats={"tasks": 3, "steps": 9,
                                        "cost_usd": MAX_COST_USD_PER_DAY + 1})
        self.assertTrue(over["over_cap"])
        self.assertTrue(over["needs_human"])
        self.assertIn("待人工", over["cost_line"])

    def test_tool_returns_summary_for_real_store(self):
        result = dispatch_tool("track_applications", {"action": "summary"})
        self.assertGreaterEqual(result["total"], 4)
        self.assertIn("cost_line", result)


if __name__ == "__main__":
    unittest.main()
