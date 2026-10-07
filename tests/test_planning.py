"""Milestone 6 tests: typed plan decisions, threshold, memory, planner eval.
Run with: python3 -m unittest discover -s tests -v  (no API key, no network)"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.evaluate import run_planner_eval
from job_agent.planning import (
    apply_plan_threshold,
    load_memory,
    paused_companies,
    plan_today,
    remember,
    save_memory,
    validate_plan_decision,
    PlanDecision,
)
from job_agent.tools import dispatch_tool

TODAY = "2026-10-07"


def _app(status, days_ago=1, company="Acme", app_id="a1"):
    from datetime import date, timedelta
    day = date(2026, 10, 7) - timedelta(days=days_ago)
    return {"id": app_id, "company": company, "title": "Backend Engineer",
            "status": status, "updated_at": f"{day.isoformat()}T20:00:00+00:00"}


class TestTypedDecision(unittest.TestCase):
    def _raw(self, **kw):
        raw = {"action": "wait", "target_id": "", "company": "",
               "confidence": 0.9, "reasons": ["x"], "requires_human": False,
               "original_action": None}
        raw.update(kw)
        return raw

    def test_valid_decision_passes(self):
        d = validate_plan_decision(self._raw(action="prep_interview"))
        self.assertEqual(d.action, "prep_interview")

    def test_free_text_action_is_refused(self):
        with self.assertRaises(ValueError):
            validate_plan_decision(self._raw(action="maybe follow up sometime"))

    def test_extra_key_is_refused(self):
        with self.assertRaises(ValueError):
            validate_plan_decision(self._raw(surprise=1))

    def test_confidence_out_of_range_is_refused(self):
        with self.assertRaises(ValueError):
            validate_plan_decision(self._raw(confidence=1.5))


class TestThreshold(unittest.TestCase):
    def test_low_confidence_becomes_needs_review_keeping_original(self):
        d = PlanDecision(action="review_materials", confidence=0.50)
        out = apply_plan_threshold(d)
        self.assertEqual(out.action, "needs_review")
        self.assertEqual(out.original_action, "review_materials")
        self.assertTrue(out.requires_human)

    def test_high_confidence_is_untouched(self):
        d = PlanDecision(action="prep_interview", confidence=0.90)
        self.assertEqual(apply_plan_threshold(d).action, "prep_interview")


class TestPlannerRules(unittest.TestCase):
    def test_needs_review_outranks_interview(self):
        plan = plan_today(apps=[_app("interview", app_id="a2"),
                                _app("needs_review", app_id="a1")],
                          today=TODAY, memory_entries=[])
        self.assertEqual(plan[0]["action"], "clear_needs_review")

    def test_stale_applied_gets_follow_up(self):
        plan = plan_today(apps=[_app("applied", days_ago=9)], today=TODAY,
                          memory_entries=[])
        self.assertEqual(plan[0]["action"], "follow_up_stale")
        self.assertTrue(plan[0]["requires_human"])

    def test_fresh_applied_waits(self):
        plan = plan_today(apps=[_app("applied", days_ago=1)], today=TODAY,
                          memory_entries=[])
        self.assertEqual(plan[0]["action"], "wait")

    def test_terminal_only_waits_with_high_confidence(self):
        plan = plan_today(apps=[_app("rejected", days_ago=6)], today=TODAY,
                          memory_entries=[])
        self.assertEqual(plan[0]["action"], "wait")
        self.assertGreaterEqual(plan[0]["confidence"], 0.7)

    def test_new_job_gets_scored_first(self):
        plan = plan_today(apps=[_app("new", days_ago=0)], today=TODAY,
                          memory_entries=[])
        self.assertEqual(plan[0]["action"], "score_new_jobs")

    def test_materials_ready_needs_human(self):
        plan = plan_today(apps=[_app("materials_ready", days_ago=2)],
                          today=TODAY, memory_entries=[])
        self.assertEqual(plan[0]["action"], "review_materials")
        self.assertTrue(plan[0]["requires_human"])


class TestMemoryChangesPlan(unittest.TestCase):
    def test_paused_company_downgrades_decision(self):
        memory = [{"kind": "paused_company", "company": "Acme",
                   "content": "paused by human", "source": "human",
                   "created_at": "2026-10-06T20:00:00+00:00", "id": "m1"}]
        plan = plan_today(apps=[_app("materials_ready", days_ago=2)],
                          today=TODAY, memory_entries=memory)
        self.assertEqual(plan[0]["action"], "needs_review")
        self.assertEqual(plan[0]["original_action"], "review_materials")
        self.assertLess(plan[0]["confidence"], 0.7)

    def test_memory_survives_disk_reload_and_is_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "agent_memory.json"
            save_memory([], path)
            remember({"kind": "fact", "company": "", "content": "x",
                      "source": "human", "created_at": TODAY, "id": "m1"}, path=path)
            remember({"kind": "paused_company", "company": "Acme",
                      "content": "y", "source": "human",
                      "created_at": TODAY, "id": "m2"}, path=path)
            entries = load_memory(path)
            self.assertEqual(len(entries), 2)  # appended, not overwritten
            self.assertEqual(paused_companies(entries), {"acme"})

    def test_unknown_memory_kind_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "agent_memory.json"
            with self.assertRaises(ValueError):
                remember({"kind": "vibe", "content": "x"}, path=path)


class TestPlannerEvalAndTool(unittest.TestCase):
    def test_planner_eval_is_perfect(self):
        report = run_planner_eval()
        self.assertEqual(report["total"], 7)
        self.assertEqual(report["accuracy"], 1.0, report["mismatches"])

    def test_tool_dispatch_returns_typed_plan(self):
        out = dispatch_tool("plan_today", {})
        self.assertIn(out["top_action"],
                      ("clear_needs_review", "prep_interview", "follow_up_stale",
                       "review_materials", "draft_materials", "score_new_jobs",
                       "needs_review", "wait"))
        for d in out["decisions"]:
            validate_plan_decision(d)  # every emitted decision is schema-valid


if __name__ == "__main__":
    unittest.main()
