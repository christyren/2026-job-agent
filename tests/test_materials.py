"""Milestone 4 tests: RAG retrieval, grounding veto, gaps, routing, budget, eval.
Run with: python3 -m unittest discover -s tests -v  (no API key, no network)"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.checker import check_materials, repair_materials
from job_agent.evaluate import load_material_cases, run_materials_eval
from job_agent.materials import (
    MODEL_TIERS,
    RunBudget,
    _adaptive_window,
    _anchor_pos,
    _kwic_snippet,
    _minimal_quote,
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


class TestMinimalQuotesInGeneration(unittest.TestCase):
    """Iteration 2026-11-21: the generator quotes minimally too.

    Before this iteration the generator pasted the whole ~345-char
    skills chunk into every point while the repair path (since 11/20)
    quoted one sentence — two paths, two shapes for the same point.
    """

    def test_generated_points_quote_one_sentence_not_the_whole_chunk(self):
        draft = generate_materials(SYNONYM_JOB, chunks=CHUNKS, extract_tier="strong")
        by_id = {c.id: c for c in CHUNKS}
        self.assertTrue(draft["points"])
        for p in draft["points"]:
            chunk = by_id[p["evidence_chunk_id"]]
            # Still verbatim (grounding veto must pass) but trimmed:
            # the quote is a proper substring, not the whole chunk.
            self.assertIn(p["evidence_quote"], chunk.text)
            self.assertLess(len(p["evidence_quote"]), len(chunk.text))
        kafka = next(p for p in draft["points"] if p["requirement"] == "Kafka")
        self.assertIn("Kafka", kafka["evidence_quote"])

    def test_generated_and_repaired_points_use_the_same_quote(self):
        # The point of sharing _minimal_quote: a point built by
        # generation and the same point rebuilt by repair must be
        # identical — otherwise the checker would keep "fixing" a
        # draft the generator just produced.
        draft = generate_materials(SYNONYM_JOB, chunks=CHUNKS, extract_tier="strong")
        generated_kafka = next(p for p in draft["points"]
                               if p["requirement"] == "Kafka")
        broken = {"job_company": draft["job_company"],
                  "points": [p for p in draft["points"]
                             if p["requirement"] != "Kafka"],
                  "gaps": draft["gaps"] + ["Kafka"]}  # fake gap: resume has Kafka
        report = check_materials(SYNONYM_JOB, broken, chunks=CHUNKS)
        self.assertIn("gap_is_real", [f["check"] for f in report["failures"]])
        repaired = repair_materials(SYNONYM_JOB, broken, report["failures"],
                                    chunks=CHUNKS)["draft"]
        repaired_kafka = next(p for p in repaired["points"]
                              if p["requirement"] == "Kafka")
        self.assertEqual(repaired_kafka, generated_kafka)

    def test_minimal_quote_falls_back_to_full_chunk(self):
        skills = next(c for c in CHUNKS if c.id == "resume-skills")
        # No sentence in the skills chunk mentions GraphQL at all.
        self.assertEqual(_minimal_quote(skills, "GraphQL"), skills.text)


class TestKwicSnippets(unittest.TestCase):
    """Iteration 2026-11-22: sentence-level quotes still carry unrelated
    list items, so _minimal_quote now returns a keyword-in-context
    snippet (term + ~30 chars each side, cut at separators)."""

    def test_kafka_quote_is_a_snippet_not_the_whole_sentence(self):
        skills = next(c for c in CHUNKS if c.id == "resume-skills")
        quote = _minimal_quote(skills, "Kafka")
        sentence = ("Data and messaging: SQL Server, MySQL, NoSQL, S3, Kafka, "
                    "Azure Service Bus and Event Hubs, SQS and SNS.")
        self.assertEqual(quote, "MySQL, NoSQL, S3, Kafka")
        self.assertIn(quote, skills.text)          # still verbatim
        self.assertLess(len(quote), len(sentence))  # head of list trimmed
        self.assertNotIn("SQL Server", quote)

    def test_rest_api_snippet_cuts_at_a_separator_not_mid_phrase(self):
        skills = next(c for c in CHUNKS if c.id == "resume-skills")
        quote = _minimal_quote(skills, "REST API")
        self.assertEqual(quote, "distributed systems, REST APIs, microservices")
        # The cut after "microservices" is at a ", " boundary: the next
        # list item is gone whole, not half-kept ("ASP.NET" without " Core").
        self.assertIn("microservices", quote)
        self.assertNotIn("Spring Boot", quote)

    def test_spark_quote_comes_from_the_data_ai_chunk(self):
        dai = next(c for c in CHUNKS if c.id == "microsoft-data-ai")
        quote = _minimal_quote(dai, "Spark")
        self.assertEqual(quote, "data pipelines using Spark, PySpark, Hadoop, APIs")
        self.assertIn(quote, dai.text)
        self.assertNotIn("Contributed to", quote)  # sentence head trimmed

    def test_sql_anchors_on_sql_server_not_inside_mysql(self):
        # "SQL" occurs inside "MySQL" first in reading order? No — but
        # the anchor must prefer a word-boundary hit either way.
        self.assertEqual(_anchor_pos("x mysql sql server", "sql"), 8)
        skills = next(c for c in CHUNKS if c.id == "resume-skills")
        quote = _minimal_quote(skills, "SQL")
        self.assertIn("SQL Server", quote)

    def test_short_sentence_comes_back_unchanged(self):
        self.assertEqual(_kwic_snippet("Short sentence with Kafka here.",
                                       ["Kafka"]),
                         "Short sentence with Kafka here.")

    def test_no_anchor_returns_the_sentence(self):
        self.assertEqual(_kwic_snippet("Nothing relevant here.", ["Kafka"]),
                         "Nothing relevant here.")


class TestAdaptiveWindow(unittest.TestCase):
    """Iteration 2026-11-23: one fixed 30-char window is too generous
    for 300-char sentences and too eager to cut 60-char ones — the
    window now scales with sentence length."""

    def test_window_bands_and_boundaries(self):
        self.assertEqual(_adaptive_window(50), 50)    # short: never cut
        self.assertEqual(_adaptive_window(80), 80)    # boundary: still whole
        self.assertEqual(_adaptive_window(81), 30)    # medium: usual 30
        self.assertEqual(_adaptive_window(150), 30)   # boundary: still 30
        self.assertEqual(_adaptive_window(151), 20)   # long: tighter 20
        self.assertEqual(_adaptive_window(345), 20)

    def test_short_sentence_never_cut_even_with_anchor_in_the_middle(self):
        # 44 chars, anchor in the middle: the old fixed window would
        # have trimmed the ends; the adaptive window keeps it whole.
        sent = "Java, C#, Python, Go and Rust are all here."
        self.assertEqual(_kwic_snippet(sent, ["Python"]), sent)

    def test_long_sentence_snippet_is_tighter_than_fixed_30(self):
        integ = next(c for c in CHUNKS if c.id == "microsoft-integration")
        long_sent = [s for s in re.split(
            r"(?<=[.!?])\s+", integ.text.strip()) if len(s) > 300][0]
        fixed = _kwic_snippet(long_sent, ["Substrate"], window=30)
        adaptive = _kwic_snippet(long_sent, ["Substrate"])
        self.assertIn("Substrate", adaptive)
        self.assertIn(adaptive, long_sent)          # still verbatim
        self.assertLessEqual(len(adaptive), len(fixed))

    def test_medium_sentence_quotes_unchanged(self):
        # Regression pins from iteration 16: sentences of 103-135 chars
        # stay in the 30-char band, so these three quotes must not move.
        skills = next(c for c in CHUNKS if c.id == "resume-skills")
        self.assertEqual(_minimal_quote(skills, "Kafka"),
                         "MySQL, NoSQL, S3, Kafka")
        self.assertEqual(_minimal_quote(skills, "REST API"),
                         "distributed systems, REST APIs, microservices")
        dai = next(c for c in CHUNKS if c.id == "microsoft-data-ai")
        self.assertEqual(_minimal_quote(dai, "Spark"),
                         "data pipelines using Spark, PySpark, Hadoop, APIs")


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
        # 5 since 2026-11-14: eval-m05 (rare paraphrases only) separates
        # the new mid tier from strong.
        self.assertEqual(len(cases), 5)
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
