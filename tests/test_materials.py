"""Milestone 4 tests: RAG retrieval, grounding veto, gaps, routing, budget, eval.
Run with: python3 -m unittest discover -s tests -v  (no API key, no network)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.evaluate import load_material_cases, run_materials_eval
from job_agent.materials import (
    MODEL_TIERS,
    RunBudget,
    extract_requirements,
    generate_materials,
    load_resume_chunks,
    retrieve,
    validate_materials,
    verify_grounding,
)
from job_agent.tools import dispatch_tool

CHUNKS = load_resume_chunks()
SYNONYM_JOB = {
    "company": "RoutingCloud", "title": "Backend Engineer",
    "description": "Java backend. Own our event streaming platform and message queue "
                   "consumers, do API design, deploy on cloud infrastructure.",
}
GAP_JOB = {
    "company": "KubeNative", "title": "Backend Engineer",
    "description": "Go services on Kubernetes with a GraphQL API. Go, Kubernetes, GraphQL.",
}


class TestResumeAndRetrieval(unittest.TestCase):
    def test_chunks_load_with_keywords(self):
        self.assertEqual(len(CHUNKS), 8)
        self.assertTrue(all(c.text and c.keywords for c in CHUNKS))

    def test_retrieve_finds_kafka_evidence(self):
        self.assertIsNotNone(retrieve("Kafka", CHUNKS))

    def test_retrieve_returns_none_for_unknown_skill(self):
        # No chunk covers Go -> None -> caller must record a gap, not invent.
        self.assertIsNone(retrieve("Go", CHUNKS))


class TestExtractionTiers(unittest.TestCase):
    def test_cheap_misses_synonyms_strong_catches_them(self):
        cheap = extract_requirements(SYNONYM_JOB, tier="cheap")
        strong = extract_requirements(SYNONYM_JOB, tier="strong")
        self.assertIn("Java", cheap)
        self.assertNotIn("Kafka", cheap)       # JD only said "event streaming"
        self.assertIn("Kafka", strong)
        self.assertIn("REST API", strong)      # via "API design"
        self.assertIn("AWS", strong)           # via "cloud infrastructure"

    def test_unknown_tier_raises(self):
        with self.assertRaises(ValueError):
            extract_requirements(SYNONYM_JOB, tier="huge")


class TestGenerationAndGrounding(unittest.TestCase):
    def test_every_point_is_grounded_and_quotes_verbatim(self):
        draft = generate_materials(SYNONYM_JOB, chunks=CHUNKS, extract_tier="strong")
        self.assertTrue(draft["points"])
        self.assertEqual(verify_grounding(draft["points"], CHUNKS), [])
        validate_materials({k: draft[k] for k in ("job_company", "points", "gaps")})

    def test_fabricated_quote_is_caught_by_verifier(self):
        fake = [{"requirement": "Kafka", "evidence_chunk_id": "resume-skills",
                 "evidence_quote": "10 years of Kafka at planetary scale",
                 "point": "For Kafka: invented"}]
        violations = verify_grounding(fake, CHUNKS)
        self.assertEqual(len(violations), 1)

    def test_unknown_chunk_id_is_caught(self):
        fake = [{"requirement": "Kafka", "evidence_chunk_id": "no-such-chunk",
                 "evidence_quote": "x", "point": "y"}]
        self.assertEqual(len(verify_grounding(fake, CHUNKS)), 1)

    def test_gap_job_produces_only_gaps_no_points(self):
        draft = generate_materials(GAP_JOB, chunks=CHUNKS)
        self.assertEqual(draft["points"], [])
        self.assertEqual(sorted(draft["gaps"]), ["Go", "GraphQL", "Kubernetes"])


class TestRoutingAndBudget(unittest.TestCase):
    def test_generation_step_uses_strong_tier_and_costs_more(self):
        draft = generate_materials(SYNONYM_JOB, chunks=CHUNKS, extract_tier="strong")
        tiers_used = {e["name"]: e["tier"] for e in draft["token_log"]}
        self.assertEqual(tiers_used["retrieve_and_generate"], "strong")
        self.assertGreater(MODEL_TIERS["strong"]["output_price_per_million"],
                           MODEL_TIERS["cheap"]["output_price_per_million"])
        self.assertIsNotNone(draft["cost_per_grounded_point_usd"])

    def test_token_log_records_each_step(self):
        draft = generate_materials(SYNONYM_JOB, chunks=CHUNKS)
        self.assertEqual(draft["steps_used"], 3)
        self.assertTrue(all(e["input_tokens"] > 0 for e in draft["token_log"]))

    def test_step_cap_refuses_to_overspend(self):
        # Pipeline needs 3 steps; a budget capped at 2 must refuse the 3rd
        # instead of silently continuing to spend.
        with self.assertRaises(RuntimeError):
            generate_materials(SYNONYM_JOB, chunks=CHUNKS, budget=RunBudget(max_steps=2))


class TestMaterialsEval(unittest.TestCase):
    def test_eval_cases_are_labelled(self):
        cases = load_material_cases()
        self.assertEqual(len(cases), 4)
        self.assertTrue(all("expected_requirements" in c and "expected_gaps" in c
                            for c in cases))

    def test_strong_beats_cheap_on_recall_and_both_stay_grounded(self):
        report = run_materials_eval()
        cheap, strong = report["tiers"]["cheap"], report["tiers"]["strong"]
        self.assertEqual(strong["requirement_recall"], 1.0)
        self.assertLess(cheap["requirement_recall"], strong["requirement_recall"])
        self.assertEqual(cheap["grounded_rate"], 1.0)
        self.assertEqual(strong["grounded_rate"], 1.0)
        self.assertEqual(strong["gap_accuracy"], 1.0)

    def test_tool_runs_eval_comparison(self):
        result = dispatch_tool("draft_materials", {"source": "eval"})
        self.assertIn("tiers", result)


if __name__ == "__main__":
    unittest.main()
