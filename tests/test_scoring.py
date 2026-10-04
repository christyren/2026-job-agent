"""Milestone 3 tests: hard rules, structured-output validation, threshold, eval.
Run with: python3 -m unittest discover -s tests -v  (no API key, no network)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.evaluate import load_eval_cases, run_eval
from job_agent.scoring import (
    MATCH_RESULT_SCHEMA,
    apply_threshold,
    hard_filter,
    heuristic_scorer,
    parse_llm_response,
    score_job,
    validate_match_result,
)
from job_agent.tools import get_preferences

PREFS = get_preferences()
GOOD_JOB = {
    "company": "ExampleCloud", "title": "Software Engineer II, Backend",
    "location": "Seattle, WA", "role_type": "backend", "years_required": 3,
    "base_salary_usd": 175000,
    "description": "Backend distributed systems in Java and Python. Kafka, REST API, AWS. We sponsor visas.",
}


class TestHardFilter(unittest.TestCase):
    def test_excluded_role_type_is_hard_failure(self):
        r = hard_filter({"company": "X", "title": "Frontend Engineer", "role_type": "frontend-only"}, PREFS)
        self.assertEqual((r.verdict, r.hard_filter_failed), ("no_match", "exclude_role_types"))

    def test_years_over_max_is_hard_failure(self):
        r = hard_filter({"company": "X", "title": "Backend Engineer", "years_required": 7}, PREFS)
        self.assertEqual(r.hard_filter_failed, "max_years_required")

    def test_known_low_salary_is_hard_failure(self):
        r = hard_filter({"company": "X", "title": "Backend Engineer", "base_salary_usd": 120000}, PREFS)
        self.assertEqual(r.hard_filter_failed, "min_base_usd")

    def test_unknown_salary_is_not_hard_failure(self):
        # Unknown != too low. This must pass through to soft scoring / review.
        self.assertIsNone(hard_filter(
            {"company": "X", "title": "Backend Engineer", "base_salary_usd": None}, PREFS))


class TestStructuredOutput(unittest.TestCase):
    def test_valid_result_passes(self):
        r = validate_match_result({"verdict": "match", "confidence": 0.9,
                                     "reasons": ["ok"], "hard_filter_failed": None})
        self.assertEqual(r.verdict, "match")
        self.assertEqual(set(MATCH_RESULT_SCHEMA["required"]),
                         {"verdict", "confidence", "reasons", "hard_filter_failed"})

    def test_bad_verdict_and_bad_confidence_raise(self):
        with self.assertRaises(ValueError):
            validate_match_result({"verdict": "maybe", "confidence": 0.9,
                                   "reasons": [], "hard_filter_failed": None})
        with self.assertRaises(ValueError):
            validate_match_result({"verdict": "match", "confidence": 1.5,
                                   "reasons": [], "hard_filter_failed": None})

    def test_missing_key_and_extra_key_raise(self):
        with self.assertRaises(ValueError):
            validate_match_result({"verdict": "match", "confidence": 0.9, "reasons": []})
        with self.assertRaises(ValueError):
            validate_match_result({"verdict": "match", "confidence": 0.9, "reasons": [],
                                   "hard_filter_failed": None, "surprise": 1})

    def test_parse_llm_response_roundtrip(self):
        r = parse_llm_response('{"verdict": "match", "confidence": 0.9, '
                               '"reasons": ["fits"], "hard_filter_failed": null}')
        self.assertEqual(r.verdict, "match")


class TestThresholdAndPipeline(unittest.TestCase):
    def test_low_confidence_is_downgraded_to_needs_review(self):
        from job_agent.scoring import MatchResult
        r = apply_threshold(MatchResult(verdict="match", confidence=0.5, reasons=["lean match"]))
        self.assertEqual(r.verdict, "needs_review")

    def test_good_job_scores_match(self):
        r = score_job(GOOD_JOB, PREFS)
        self.assertEqual(r.verdict, "match")
        self.assertGreaterEqual(r.confidence, 0.7)

    def test_thin_jd_goes_to_review_not_match(self):
        thin = dict(GOOD_JOB, description="Backend role. Java.",
                    base_salary_usd=None, years_required=None)
        self.assertEqual(score_job(thin, PREFS).verdict, "needs_review")

    def test_heuristic_output_always_matches_schema(self):
        validate_match_result(heuristic_scorer(GOOD_JOB, PREFS))  # must not raise


class TestEvalSet(unittest.TestCase):
    def test_eval_set_has_10_labelled_cases(self):
        cases = load_eval_cases()
        self.assertEqual(len(cases), 10)
        self.assertTrue(all("expected_verdict" in c and "note" in c for c in cases))

    def test_eval_accuracy_is_perfect_on_toy_set(self):
        report = run_eval(prefs=PREFS)
        self.assertEqual(report["accuracy"], 1.0, f"mismatches: {report['mismatches']}")


if __name__ == "__main__":
    unittest.main()
