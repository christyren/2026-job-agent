"""Tests for iteration 2026-11-17: run traces + cost-cap governance."""
import json
import tempfile
import unittest
from pathlib import Path

from job_agent.evaluate import load_material_cases, run_trace_eval
from job_agent.materials import RunBudget
from job_agent.tools import draft_materials_traced
from job_agent.tracing import (DEFAULT_MAX_RUN_COST_USD, BudgetExceeded,
                               CostCappedBudget, RunTrace, load_traces,
                               run_traced_checked, trace_summary)


def _m01_job():
    cases = {c["id"]: c for c in load_material_cases()}
    return {k: v for k, v in cases["eval-m01"].items()
            if k not in ("expected_requirements", "expected_gaps", "note")}


class TestCostCappedBudget(unittest.TestCase):
    def test_normal_run_stays_under_cap(self):
        budget = CostCappedBudget(max_cost_usd=DEFAULT_MAX_RUN_COST_USD)
        budget.step("extract_requirements", "cheap", "x" * 400, "[]")
        self.assertLessEqual(budget.total_cost_usd, DEFAULT_MAX_RUN_COST_USD)

    def test_tiny_cap_raises_after_recording_the_step(self):
        budget = CostCappedBudget(max_cost_usd=0.000001)
        with self.assertRaises(BudgetExceeded):
            budget.step("extract_requirements", "strong", "x" * 4000, "y" * 400)
        # the step that crossed the line is still in the log for audit
        self.assertEqual(len(budget.log), 1)
        self.assertEqual(budget.log[0]["name"], "extract_requirements")

    def test_step_cap_still_applies(self):
        budget = CostCappedBudget(max_cost_usd=100.0, max_steps=1)
        budget.step("a", "cheap", "x", "y")
        with self.assertRaises(RuntimeError):
            budget.step("b", "cheap", "x", "y")


class TestRunTrace(unittest.TestCase):
    def test_trace_ids_are_unique(self):
        self.assertNotEqual(RunTrace(task="a").trace_id,
                            RunTrace(task="a").trace_id)

    def test_record_budget_mirrors_log_with_cumulative_cost(self):
        budget = RunBudget()
        budget.step("extract_requirements", "cheap", "x" * 400, "[]")
        budget.step("verify_grounding", "cheap", "[]", "[]")
        trace = RunTrace(task="t")
        trace.record_budget(budget)
        self.assertEqual(trace.num_steps, 2)
        self.assertEqual(trace.steps[1]["cumulative_cost_usd"],
                         trace.total_cost_usd)
        self.assertEqual(trace.steps[0]["name"], "extract_requirements")

    def test_finish_marks_needs_human_only_on_bad_status(self):
        ok = RunTrace(task="t")
        ok.finish("ok")
        self.assertFalse(ok.needs_human)
        bad = RunTrace(task="t")
        bad.finish("over_cap", error="too expensive")
        self.assertTrue(bad.needs_human)
        self.assertEqual(bad.status, "over_cap")

    def test_save_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "traces.jsonl"
            trace = RunTrace(task="t")
            trace.record_budget(RunBudget())
            trace.finish("ok")
            trace.save(path)
            loaded = load_traces(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["trace_id"], trace.trace_id)
            self.assertEqual(loaded[0]["status"], "ok")

    def test_load_missing_file_is_empty_not_error(self):
        self.assertEqual(load_traces(Path("/tmp/does-not-exist-traces.jsonl")), [])


class TestTracedCheckedRun(unittest.TestCase):
    def test_normal_cap_ships_draft_with_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_traced_checked(_m01_job(), trace_path=Path(tmp) / "t.jsonl")
            self.assertFalse(out["over_cap"])
            self.assertFalse(out["needs_human"])
            self.assertIsNotNone(out["draft"])
            self.assertTrue(out["draft"]["checker_passed"])
            self.assertEqual(out["trace"]["status"], "ok")
            self.assertGreater(out["trace"]["num_steps"], 0)
            self.assertEqual(len(load_traces(Path(tmp) / "t.jsonl")), 1)

    def test_forced_over_cap_ships_no_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_traced_checked(_m01_job(), max_cost_usd=0.001,
                                     trace_path=Path(tmp) / "t.jsonl")
            self.assertTrue(out["over_cap"])
            self.assertTrue(out["needs_human"])
            self.assertIsNone(out["draft"])
            self.assertEqual(out["trace"]["status"], "over_cap")
            # the trace still shows the step that crossed the cap
            self.assertGreater(out["trace"]["num_steps"], 0)
            self.assertIn("exceeded cap", out["error"])
            self.assertEqual(len(load_traces(Path(tmp) / "t.jsonl")), 1)

    def test_trace_summary_counts(self):
        traces = [
            {"trace_id": "a", "task": "t", "status": "ok",
             "total_cost_usd": 0.05, "needs_human": False},
            {"trace_id": "b", "task": "t", "status": "over_cap",
             "total_cost_usd": 0.12, "needs_human": True},
        ]
        summary = trace_summary(traces)
        self.assertEqual(summary["runs"], 2)
        self.assertEqual(summary["over_cap_runs"], 1)
        self.assertEqual(summary["over_cap_trace_ids"], ["b"])
        self.assertEqual(summary["most_expensive"]["trace_id"], "b")
        self.assertAlmostEqual(summary["total_cost_usd"], 0.17)


class TestTraceEvalAndTool(unittest.TestCase):
    def test_run_trace_eval(self):
        report = run_trace_eval()
        self.assertEqual(report["normal_cap"]["ok_runs"], report["cases"])
        self.assertGreater(report["normal_cap"]["total_cost_usd"], 0)
        forced = report["forced_over_cap"]
        self.assertTrue(forced["over_cap"])
        self.assertTrue(forced["needs_human"])
        self.assertFalse(forced["draft_shipped"])
        self.assertGreater(forced["steps_recorded"], 0)
        self.assertEqual(report["trace_summary"]["runs"], report["cases"] + 1)
        self.assertEqual(report["trace_summary"]["over_cap_runs"], 1)

    def test_tool_eval_and_summary(self):
        report = draft_materials_traced(source="eval")
        self.assertEqual(report["normal_cap"]["ok_runs"], 5)
        self.assertTrue(report["forced_over_cap"]["over_cap"])
        # summary reads the real file; missing file is a valid empty state
        summary = draft_materials_traced(source="summary")
        self.assertIn("runs", summary)
        self.assertIn("over_cap_runs", summary)

    def test_tool_single_traced_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            # single draft via the tool writes to the real trace file;
            # just assert the shape, then check the tool rejects bad ids
            out = draft_materials_traced(case_id="eval-m01", max_cost_usd=0.10)
            self.assertIn("trace", out)
            self.assertFalse(out["over_cap"])
            self.assertIsNotNone(out["draft"])
        with self.assertRaises(ValueError):
            draft_materials_traced(case_id="eval-nope")


if __name__ == "__main__":
    unittest.main()
