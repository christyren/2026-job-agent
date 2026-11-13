"""Tests for iteration 2026-11-13: batch scoring (one batch, many jobs)."""
import unittest

from job_agent.agent import _mock_decide
from job_agent.batch_scoring import (
    job_to_choice_question,
    result_to_choice_scores,
    score_jobs_batch,
    validate_batch_question,
)
from job_agent.evaluate import load_eval_cases, run_batch_scoring_eval
from job_agent.scoring import MatchResult, score_job
from job_agent.tools import dispatch_tool, get_preferences


class TestChoiceScores(unittest.TestCase):
    def test_winner_is_final_verdict_with_confidence(self):
        result = MatchResult(verdict="match", confidence=0.95, reasons=["x"])
        scores = result_to_choice_scores(result)
        self.assertEqual([s["verdict"] for s in scores],
                         ["match", "no_match", "needs_review"])
        self.assertEqual(scores[0]["score"], 0.95)
        self.assertEqual(scores[1]["score"], 0.025)
        self.assertEqual(scores[2]["score"], 0.025)

    def test_remainder_splits_equally(self):
        result = MatchResult(verdict="needs_review", confidence=0.65, reasons=[])
        scores = {s["verdict"]: s["score"] for s in result_to_choice_scores(result)}
        self.assertEqual(scores["needs_review"], 0.65)
        self.assertEqual(scores["match"], 0.175)
        self.assertEqual(scores["no_match"], 0.175)

    def test_hard_filter_no_match_scores_one(self):
        prefs = get_preferences()
        job = {"id": "t-ms", "company": "Microsoft", "title": "Software Engineer II",
               "location": "Seattle", "description": "backend"}
        result = score_job(job, prefs)
        self.assertEqual((result.verdict, result.confidence), ("no_match", 1.0))
        scores = {s["verdict"]: s["score"] for s in result_to_choice_scores(result)}
        self.assertEqual(scores["no_match"], 1.0)
        self.assertEqual(scores["match"], 0.0)


class TestChoiceQuestion(unittest.TestCase):
    def test_question_validates_and_asks_final_verdict(self):
        # eval-06: scorer leans match, but confidence 0.65 < 0.7 threshold,
        # so the FINAL verdict is needs_review — the question must ask that.
        cases = {c["id"]: c for c in load_eval_cases()}
        case = cases["eval-06"]
        job = {k: v for k, v in case.items() if k not in ("expected_verdict", "note")}
        result = score_job(job, get_preferences())
        self.assertEqual(result.verdict, "needs_review")
        q = job_to_choice_question(job, result, 6)
        validated = validate_batch_question(q.to_dict())
        self.assertEqual(validated.chosen, "needs_review")
        self.assertEqual(validated.confidence, 0.65)

    def test_low_confidence_winner_not_top_is_refused(self):
        # A future scorer returning confidence < 1/3 would make the split
        # remainder out-score the winner — validation must refuse the batch.
        result = MatchResult(verdict="match", confidence=0.30, reasons=[])
        q = job_to_choice_question({"id": "t-low", "company": "X", "title": "Y"},
                                   result, 1)
        with self.assertRaises(ValueError):
            validate_batch_question(q.to_dict())

    def test_score_question_cannot_carry_choices(self):
        with self.assertRaises(ValueError):
            validate_batch_question({
                "question_id": "q00", "question_type": "score",
                "question": "batch?", "choices": [{"verdict": "match", "score": 1.0}],
                "chosen": "", "value": 0.5, "confidence": 0.5, "reasons": []})


class TestBatch(unittest.TestCase):
    def setUp(self):
        self.prefs = get_preferences()
        self.cases = load_eval_cases()
        self.jobs = [{k: v for k, v in c.items() if k not in ("expected_verdict", "note")}
                     for c in self.cases]

    def test_batch_verdicts_equal_one_by_one_scoring(self):
        # Batching changes packaging and price, NEVER the decision.
        report = score_jobs_batch(self.jobs, self.prefs)
        for job, row in zip(self.jobs, report["results"]):
            self.assertEqual(row["verdict"], score_job(job, self.prefs).verdict)

    def test_question_count_is_jobs_plus_one_score(self):
        report = score_jobs_batch(self.jobs, self.prefs)
        self.assertEqual(report["num_questions"], len(self.jobs) + 1)
        self.assertEqual(report["questions"][-1]["question_type"], "score")

    def test_batch_score_is_mean_confidence(self):
        report = score_jobs_batch(self.jobs, self.prefs)
        mean = round(sum(r["confidence"] for r in report["results"])
                     / len(report["results"]), 3)
        self.assertEqual(report["batch_score"], mean)
        self.assertEqual(report["questions"][-1]["value"], mean)

    def test_batch_is_cheaper_than_per_job_calls(self):
        report = score_jobs_batch(self.jobs, self.prefs)
        self.assertLess(report["cost"]["estimated_cost_usd"],
                        report["per_job_cost"]["estimated_cost_usd"])
        self.assertGreater(report["saving_factor"], 1.0)

    def test_empty_batch_is_just_the_score_question(self):
        report = score_jobs_batch([], self.prefs)
        self.assertEqual(report["num_questions"], 1)
        self.assertEqual(report["batch_score"], 0.0)
        self.assertEqual(report["questions"][0]["question_type"], "score")

    def test_over_1000_questions_is_refused(self):
        jobs = [{"id": f"j{i}", "company": "C", "title": "Backend Engineer",
                 "location": "Seattle", "description": "backend java"}
                for i in range(1000)]
        with self.assertRaises(ValueError):
            score_jobs_batch(jobs, self.prefs)

    def test_batch_eval_matches_labels(self):
        report = run_batch_scoring_eval()
        self.assertEqual(report["correct"], 10)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["mismatches"], [])


class TestWiring(unittest.TestCase):
    def test_tool_dispatch(self):
        report = dispatch_tool("score_jobs_batch", {"source": "eval"})
        self.assertEqual(report["source"], "eval")
        self.assertEqual(report["num_jobs"], 10)
        self.assertEqual(report["num_questions"], 11)

    def test_mock_routes_batch_scoring_phrase(self):
        tool, args = _mock_decide("帮我批量评分")
        self.assertEqual(tool, "score_jobs_batch")
        self.assertEqual(args, {"source": "eval"})

    def test_mock_still_routes_plain_batch_to_typed_decisions(self):
        tool, _ = _mock_decide("帮我看今天的批量决策")
        self.assertEqual(tool, "typed_decisions")


if __name__ == "__main__":
    unittest.main()
