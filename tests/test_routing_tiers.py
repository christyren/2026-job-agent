"""Iteration 2026-11-14: the mid (Hydrogen) routing tier.

Three tiers now, not two: cheap = exact hits only, mid = exact hits +
the three most common paraphrases, strong = exact hits + the full
synonym map. The eval set grew from 4 to 5 cases with eval-m05, a JD
written entirely in rare paraphrases (stream processing / decoupled
services / containerised) — the case that separates mid from strong.
Expected recall on the 5-case set (20 requirements): cheap 14/20,
mid 17/20, strong 20/20.
"""
import unittest

from job_agent.evaluate import load_material_cases, run_materials_eval
from job_agent.materials import (
    MID_SYNONYM_MAP, MODEL_TIERS, SYNONYM_MAP, extract_requirements,
    generate_materials, load_resume_chunks,
)
from job_agent.tools import TOOL_SCHEMAS, draft_materials

M05 = {"company": "StreamWorks", "title": "Backend Engineer",
       "description": ("Backend engineer for our real-time platform. You will build "
                       "stream processing pipelines over our event bus, design "
                       "decoupled services that scale independently, and ship "
                       "containerised workloads to production.")}
M01 = {"company": "RoutingCloud", "title": "Backend Engineer",
       "description": ("Backend engineer with Java building distributed systems. "
                       "You will own our event streaming platform and message queue "
                       "consumers, do API design for public endpoints, and deploy on "
                       "cloud infrastructure (AWS).")}


class TestMidTierPricing(unittest.TestCase):
    def test_mid_tier_is_hydrogen_priced(self):
        # Google blog 2026-11-12: $1.50 in / $7.50 out per million, flat
        # across the whole 1M-token context window.
        self.assertEqual(MODEL_TIERS["mid"]["input_price_per_million"], 1.5)
        self.assertEqual(MODEL_TIERS["mid"]["output_price_per_million"], 7.5)
        self.assertIn("Hydrogen", MODEL_TIERS["mid"]["model_example"])

    def test_mid_price_between_cheap_and_strong(self):
        cheap = MODEL_TIERS["cheap"]["input_price_per_million"]
        mid = MODEL_TIERS["mid"]["input_price_per_million"]
        strong = MODEL_TIERS["strong"]["input_price_per_million"]
        self.assertLess(cheap, mid)
        self.assertLess(mid, strong)


class TestMidSynonymSubset(unittest.TestCase):
    def test_mid_map_is_a_strict_subset_of_strong_map(self):
        self.assertTrue(set(MID_SYNONYM_MAP).issubset(set(SYNONYM_MAP)))
        self.assertLess(len(MID_SYNONYM_MAP), len(SYNONYM_MAP))
        for phrase, term in MID_SYNONYM_MAP.items():
            self.assertEqual(SYNONYM_MAP[phrase], term)

    def test_mid_knows_common_paraphrases(self):
        reqs = extract_requirements(M01, tier="mid")
        self.assertIn("Kafka", reqs)      # via "event streaming"
        self.assertIn("REST API", reqs)   # via "api design"

    def test_mid_misses_rare_paraphrases_strong_knows(self):
        mid = extract_requirements(M05, tier="mid")
        strong = extract_requirements(M05, tier="strong")
        self.assertEqual(mid, [])  # stream processing / decoupled / containerised
        self.assertEqual(sorted(strong), ["Docker", "Kafka", "microservices"])

    def test_cheap_still_misses_everything_paraphrased(self):
        self.assertEqual(extract_requirements(M05, tier="cheap"), [])
        self.assertEqual(extract_requirements(M01, tier="cheap"),
                         ["Java", "distributed systems", "AWS"])

    def test_unknown_tier_still_rejected(self):
        with self.assertRaises(ValueError):
            extract_requirements(M01, tier="ultra")


class TestMidBudgetLogging(unittest.TestCase):
    def test_budget_logs_the_tier_that_actually_ran(self):
        # Regression: the old code logged "strong" for anything not cheap,
        # which would have billed mid extraction at strong prices.
        chunks = load_resume_chunks()
        draft = generate_materials(M01, chunks=chunks, extract_tier="mid")
        self.assertEqual(draft["extract_tier"], "mid")
        self.assertEqual(draft["token_log"][0]["tier"], "mid")
        self.assertEqual(draft["token_log"][0]["name"], "extract_requirements")

    def test_mid_draft_finds_all_five_m01_requirements(self):
        chunks = load_resume_chunks()
        draft = generate_materials(M01, chunks=chunks, extract_tier="mid")
        self.assertEqual(len(draft["points"]), 5)
        self.assertEqual(draft["gaps"], [])


class TestThreeTierEval(unittest.TestCase):
    def test_recall_gradient_cheap_mid_strong(self):
        report = run_materials_eval()
        self.assertEqual(set(report["tiers"]), {"cheap", "mid", "strong"})
        self.assertEqual(report["cases"], 5)
        self.assertEqual(report["tiers"]["cheap"]["requirement_recall"], 0.7)
        self.assertEqual(report["tiers"]["mid"]["requirement_recall"], 0.85)
        self.assertEqual(report["tiers"]["strong"]["requirement_recall"], 1.0)

    def test_grounded_rate_still_perfect_on_all_tiers(self):
        report = run_materials_eval()
        for tier in ("cheap", "mid", "strong"):
            self.assertEqual(report["tiers"][tier]["grounded_rate"], 1.0)

    def test_gap_accuracy_cannot_see_silent_misses(self):
        # eval-m05 is missed entirely by cheap and mid, yet their gaps
        # are still "correct" (empty == expected empty) — only recall
        # catches a silent miss. gap_accuracy stays 1.0 on all tiers.
        report = run_materials_eval()
        for tier in ("cheap", "mid", "strong"):
            self.assertEqual(report["tiers"][tier]["gap_accuracy"], 1.0)
        m05_mid = [r for r in report["tiers"]["mid"]["rows"] if r["id"] == "eval-m05"][0]
        self.assertEqual(m05_mid["missed"], ["Kafka", "microservices", "Docker"])

    def test_mid_cost_between_cheap_and_strong(self):
        report = run_materials_eval()
        cheap = report["tiers"]["cheap"]["total_cost_usd"]
        mid = report["tiers"]["mid"]["total_cost_usd"]
        strong = report["tiers"]["strong"]["total_cost_usd"]
        self.assertLessEqual(cheap, mid)
        self.assertLessEqual(mid, strong)


class TestDraftMaterialsTool(unittest.TestCase):
    def test_tool_accepts_extract_tier(self):
        draft = draft_materials(case_id="eval-m05", extract_tier="strong")
        self.assertEqual(len(draft["points"]), 3)
        self.assertEqual(draft["gaps"], [])

    def test_tool_mid_misses_m05_silently(self):
        draft = draft_materials(case_id="eval-m05", extract_tier="mid")
        self.assertEqual(draft["points"], [])
        self.assertEqual(draft["gaps"], [])  # silent miss: nothing to report

    def test_schema_advertises_three_tiers(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "draft_materials")
        self.assertEqual(schema["input_schema"]["properties"]["extract_tier"]["enum"],
                         ["cheap", "mid", "strong"])


if __name__ == "__main__":
    unittest.main()
