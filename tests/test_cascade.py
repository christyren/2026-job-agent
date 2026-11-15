"""Iteration 2026-11-15: cascade extraction (FrugalGPT-style).

Start cheap, escalate to mid, then strong, ONLY while fewer than
MIN_REQUIREMENTS (4) requirements have been found. Expected behaviour on
the 5-case eval set (20 requirements):
- eval-m04 (5 direct hits): cheap only, no escalation.
- eval-m01 (cheap 3 -> mid 5): stops at mid, 5/5.
- eval-m03 (cheap 3 -> mid 4): stops at mid, 4/4.
- eval-m02 (3/3/3) and eval-m05 (0/0/3): escalate all the way to strong.
Cascade recall should equal strong recall (100%) at min_requirements=4 —
but at 3 it must NOT: eval-m01 looks 'fine' with 3 direct hits and the
cascade would stop before ever seeing Kafka / REST API. That is the
lesson: the trigger is a heuristic, and the threshold is a parameter
the eval measures, not a constant you trust.
"""
import unittest

from job_agent.agent import _mock_decide
from job_agent.evaluate import (load_material_cases,
                                run_materials_cascade_eval, run_materials_eval)
from job_agent.materials import (CASCADE_TIERS, MIN_REQUIREMENTS,
                                 RunBudget, extract_requirements,
                                 extract_requirements_cascade,
                                 generate_materials_cascade,
                                 load_resume_chunks)
from job_agent.tools import TOOL_SCHEMAS, dispatch_tool, draft_materials_cascade

M01 = {"company": "RoutingCloud", "title": "Backend Engineer",
       "description": ("Backend engineer with Java building distributed systems. "
                       "You will own our event streaming platform and message queue "
                       "consumers, do API design for public endpoints, and deploy on "
                       "cloud infrastructure (AWS).")}
M04 = {"company": "WebStack", "title": "Software Engineer II",
       "description": ("Software engineer with TypeScript and Angular on the "
                       "frontend, ASP.NET Core and C# services on the backend, "
                       "SQL Server data layer.")}
M05 = {"company": "StreamWorks", "title": "Backend Engineer",
       "description": ("Backend engineer for our real-time platform. You will build "
                       "stream processing pipelines over our event bus, design "
                       "decoupled services that scale independently, and ship "
                       "containerised workloads to production.")}


class TestCascadeTrigger(unittest.TestCase):
    def test_plain_jd_never_escalates(self):
        got = extract_requirements_cascade(M04)
        self.assertEqual(got["tiers_tried"], ["cheap"])
        self.assertEqual(got["final_tier"], "cheap")
        self.assertFalse(got["escalated"])
        self.assertEqual(len(got["requirements"]), 5)

    def test_common_paraphrases_stop_at_mid(self):
        got = extract_requirements_cascade(M01)
        self.assertEqual(got["tiers_tried"], ["cheap", "mid"])
        self.assertEqual(got["final_tier"], "mid")
        self.assertTrue(got["escalated"])
        self.assertEqual(len(got["requirements"]), 5)

    def test_rare_paraphrases_escalate_to_strong(self):
        got = extract_requirements_cascade(M05)
        self.assertEqual(got["tiers_tried"], ["cheap", "mid", "strong"])
        self.assertEqual(got["final_tier"], "strong")
        self.assertEqual(sorted(got["requirements"]),
                         ["Docker", "Kafka", "microservices"])

    def test_threshold_3_stops_too_early_on_m01(self):
        # The trigger is a heuristic: at min_requirements=3, m01's 3 direct
        # hits look "enough" and the cascade never sees Kafka / REST API.
        got = extract_requirements_cascade(M01, min_requirements=3)
        self.assertEqual(got["tiers_tried"], ["cheap"])
        self.assertNotIn("Kafka", got["requirements"])
        self.assertNotIn("REST API", got["requirements"])

    def test_threshold_1_never_escalates_when_anything_found(self):
        got = extract_requirements_cascade(M01, min_requirements=1)
        self.assertEqual(got["tiers_tried"], ["cheap"])

    def test_empty_extraction_still_escalates(self):
        # min_requirements=1 and zero found: even the weakest trigger fires.
        got = extract_requirements_cascade(M05, min_requirements=1)
        self.assertEqual(got["tiers_tried"], ["cheap", "mid", "strong"])


class TestCascadeMonotonicity(unittest.TestCase):
    def test_escalating_never_loses_a_requirement(self):
        # Tiers are nested (cheap synonyms subset of mid subset of strong),
        # so a later tier's list is always a superset of the earlier one.
        for case in load_material_cases():
            job = {k: v for k, v in case.items()
                   if k not in ("expected_requirements", "expected_gaps", "note")}
            prev = set()
            for tier in CASCADE_TIERS:
                found = set(extract_requirements(job, tier=tier))
                self.assertTrue(prev.issubset(found),
                                f"{case['id']}: {tier} lost {prev - found}")
                prev = found


class TestCascadeDraft(unittest.TestCase):
    def test_draft_carries_audit_trail(self):
        draft = generate_materials_cascade(M05, chunks=load_resume_chunks())
        self.assertEqual(draft["extract_tier"], "strong")
        self.assertEqual(draft["cascade"]["tiers_tried"],
                         ["cheap", "mid", "strong"])
        self.assertEqual(len(draft["points"]), 3)
        self.assertEqual(draft["gaps"], [])

    def test_worst_case_still_inside_step_cap(self):
        # 3 extract attempts + generate + verify = 5 steps <= MAX_STEPS_PER_TASK.
        budget = RunBudget()
        draft = generate_materials_cascade(M05, chunks=load_resume_chunks(),
                                           budget=budget)
        self.assertLessEqual(draft["steps_used"], 6)
        self.assertEqual(len(budget.log), draft["steps_used"])
        extract_steps = [e for e in budget.log if e["name"] == "extract_requirements"]
        self.assertEqual([e["tier"] for e in extract_steps],
                         ["cheap", "mid", "strong"])

    def test_budget_logs_each_attempt_at_its_own_tier(self):
        # The 2026-11-14 bug was billing non-cheap tiers at strong prices;
        # a cascade bills cheap attempts at cheap prices.
        budget = RunBudget()
        generate_materials_cascade(M01, chunks=load_resume_chunks(), budget=budget)
        extract_steps = [e for e in budget.log if e["name"] == "extract_requirements"]
        self.assertEqual([e["tier"] for e in extract_steps], ["cheap", "mid"])
        self.assertLess(extract_steps[0]["cost_usd"], extract_steps[1]["cost_usd"])


class TestCascadeEval(unittest.TestCase):
    def test_cascade_recall_equals_strong_at_min_4(self):
        report = run_materials_cascade_eval()
        self.assertEqual(report["requirement_recall"], 1.0)
        self.assertEqual(report["gap_accuracy"], 1.0)
        self.assertEqual(report["grounded_rate"], 1.0)
        self.assertEqual(report["escalated_cases"], 4)  # only m04 stays cheap

    def test_threshold_sensitivity_is_measured_not_assumed(self):
        report = run_materials_cascade_eval()
        self.assertEqual(report["threshold_sensitivity"][4], 1.0)
        # min=3: m01 stops at cheap (3/5) -> 17/20 = 0.85, same as mid tier.
        self.assertEqual(report["threshold_sensitivity"][3], 0.85)

    def test_cascade_cost_near_strong_not_near_cheap(self):
        # On this deliberately paraphrase-heavy eval set, 4 of 5 cases
        # escalate, so the cascade pays almost strong prices — the saving
        # shows up on easy JDs (m04), not here. Honest accounting: the
        # cascade buys strong recall, it does not promise cheap prices.
        cascade = run_materials_cascade_eval()
        strong = run_materials_eval()["tiers"]["strong"]
        self.assertLessEqual(cascade["total_cost_usd"],
                             strong["total_cost_usd"] * 1.2)
        self.assertGreater(cascade["total_cost_usd"],
                           run_materials_eval()["tiers"]["cheap"]["total_cost_usd"])


class TestCascadeTool(unittest.TestCase):
    def test_tool_eval_shape(self):
        report = draft_materials_cascade(source="eval")
        self.assertEqual(report["requirement_recall"], 1.0)
        self.assertEqual(report["min_requirements"], MIN_REQUIREMENTS)

    def test_tool_single_case(self):
        draft = draft_materials_cascade(case_id="eval-m05")
        self.assertEqual(draft["case_id"], "eval-m05")
        self.assertEqual(draft["cascade"]["final_tier"], "strong")

    def test_tool_unknown_case_rejected(self):
        with self.assertRaises(ValueError):
            draft_materials_cascade(case_id="eval-nope")

    def test_tool_registered_and_dispatched(self):
        names = [s["name"] for s in TOOL_SCHEMAS]
        self.assertIn("draft_materials_cascade", names)
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "draft_materials_cascade")
        self.assertIn("min_requirements", schema["input_schema"]["properties"])
        report = dispatch_tool("draft_materials_cascade", {"source": "eval"})
        self.assertEqual(report["requirement_recall"], 1.0)

    def test_mock_routes_cascade_keyword(self):
        tool, args = _mock_decide("帮我用级联抽取生成材料")
        self.assertEqual(tool, "draft_materials_cascade")
        self.assertEqual(args, {"source": "eval"})


if __name__ == "__main__":
    unittest.main()
