"""Tests for iteration 2026-11-12: batch typed decisions (Decisions-API style)."""
import unittest

from job_agent.decisions import (
    MAX_QUESTIONS_PER_BATCH,
    estimate_batch_cost,
    plan_to_decisions,
    validate_typed_decision,
)
from job_agent.evaluate import run_decisions_eval
from job_agent.planning import plan_today
from job_agent.tools import dispatch_tool


def _fake_decision(action="clear_needs_review", confidence=0.95,
                   company="Northwind", target_id="a1", requires_human=True):
    return {"action": action, "target_id": target_id, "company": company,
            "confidence": confidence, "reasons": ["test reason"],
            "requires_human": requires_human, "original_action": None}


class TestChoiceQuestion(unittest.TestCase):
    def test_choice_has_all_eight_actions_and_winner_is_max(self):
        batch = plan_to_decisions([_fake_decision()])
        choice = next(q for q in batch if q["question_type"] == "choice")
        self.assertEqual(len(choice["choices"]), 8)
        self.assertEqual(choice["chosen"], "clear_needs_review")
        scores = {c["action"]: c["score"] for c in choice["choices"]}
        self.assertEqual(scores["clear_needs_review"], 0.95)
        self.assertEqual(max(scores.values()), 0.95)
        self.assertTrue(all(s == 0.0 for a, s in scores.items()
                            if a != "clear_needs_review"))

    def test_noul_probability_is_the_confidence(self):
        batch = plan_to_decisions([_fake_decision(confidence=0.5)])
        noul = next(q for q in batch if q["question_type"] == "noul")
        self.assertEqual(noul["value"], 0.5)
        self.assertTrue(noul["requires_human"])

    def test_score_question_is_mean_confidence(self):
        batch = plan_to_decisions([_fake_decision(confidence=0.95),
                                   _fake_decision(action="prep_interview",
                                                  confidence=0.90, company="LiveCo",
                                                  target_id="a2", requires_human=False)])
        score = next(q for q in batch if q["question_type"] == "score")
        self.assertEqual(score["value"], 0.925)
        self.assertEqual(len(batch), 5)  # 2 per decision + 1 batch score

    def test_empty_plan_is_one_zero_score_question(self):
        batch = plan_to_decisions([])
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["question_type"], "score")
        self.assertEqual(batch[0]["value"], 0.0)

    def test_wait_decision_still_becomes_a_choice(self):
        plan = plan_today(apps=[{"company": "ClosedCo", "id": "a1",
                                 "status": "rejected", "title": "Backend Engineer",
                                 "updated_at": "2026-10-01T20:00:00+00:00"}],
                          today="2026-10-07", memory_entries=[])
        batch = plan_to_decisions(plan)
        choice = next(q for q in batch if q["question_type"] == "choice")
        self.assertEqual(choice["chosen"], "wait")
        self.assertEqual(choice["confidence"], 0.9)


class TestValidation(unittest.TestCase):
    def _valid_choice(self):
        return plan_to_decisions([_fake_decision()])[0]

    def test_rejects_unknown_question_type(self):
        raw = self._valid_choice()
        raw["question_type"] = "essay"
        with self.assertRaises(ValueError):
            validate_typed_decision(raw)

    def test_rejects_chosen_not_in_choices(self):
        raw = self._valid_choice()
        raw["chosen"] = "wait"
        raw["choices"] = [c for c in raw["choices"]
                          if c["action"] != "wait"]
        with self.assertRaises(ValueError):
            validate_typed_decision(raw)

    def test_rejects_chosen_not_highest_scored(self):
        raw = self._valid_choice()
        raw["chosen"] = "wait"  # scores 0.0 while clear_needs_review scores 0.95
        with self.assertRaises(ValueError):
            validate_typed_decision(raw)

    def test_rejects_score_out_of_range(self):
        raw = self._valid_choice()
        raw["value"] = 1.5
        with self.assertRaises(ValueError):
            validate_typed_decision(raw)

    def test_rejects_noul_carrying_choices(self):
        raw = plan_to_decisions([_fake_decision()])[1]  # the noul question
        raw["choices"] = [{"action": "wait", "score": 0.5}]
        with self.assertRaises(ValueError):
            validate_typed_decision(raw)


class TestBatchCost(unittest.TestCase):
    def test_output_is_always_free(self):
        cost = estimate_batch_cost(100)
        self.assertEqual(cost["total_output_tokens"], 0)
        self.assertEqual(cost["output_cost_usd"], 0.0)

    def test_shared_input_billed_once(self):
        # 100 questions in one batch: shared 2000 tokens counted once.
        batch100 = estimate_batch_cost(100)
        self.assertEqual(batch100["total_input_tokens"], 2000 + 100 * 400)
        self.assertAlmostEqual(batch100["estimated_cost_usd"], 0.0042)
        # 100 separate one-question batches would re-bill the shared input
        # every time: 100 * (2000 + 400) tokens — far more expensive.
        single = estimate_batch_cost(1)
        self.assertLess(batch100["estimated_cost_usd"], 100 * single["estimated_cost_usd"])

    def test_batch_cap_enforced(self):
        plan = [_fake_decision(target_id=f"a{i}", company=f"Co{i}")
                for i in range(MAX_QUESTIONS_PER_BATCH)]
        with self.assertRaises(ValueError):
            plan_to_decisions(plan)


class TestDecisionsEvalAndTool(unittest.TestCase):
    def test_decisions_eval_3_of_3(self):
        report = run_decisions_eval()
        self.assertEqual(report["correct"], 3)
        self.assertEqual(report["accuracy"], 1.0)

    def test_tool_dispatch_returns_batch(self):
        result = dispatch_tool("typed_decisions", {})
        self.assertIn("questions", result)
        self.assertEqual(result["num_questions"], len(result["questions"]))
        self.assertIn("cost", result)
        self.assertEqual(result["cost"]["total_output_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
