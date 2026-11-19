"""Tests for iteration 2026-11-16: independent checker step (solver/checker split).

The checker is tested the way a verifier should be tested: not by
showing it honest drafts (those must pass silently), but by feeding it
seven bad drafts that each break exactly one rule and demanding it
catch the right one — and then proving one repair + one re-check
clears each of them.
"""
import unittest

from job_agent.checker import (MAX_REPAIRS, MAX_STEPS_CHECKED,
                               check_and_repair, check_materials,
                               generate_materials_checked, repair_materials)
from job_agent.evaluate import (build_adversarial_drafts, load_material_cases,
                                run_checker_eval)
from job_agent.materials import (RunBudget, generate_materials_cascade,
                                 load_resume_chunks)


class TestCheckerRules(unittest.TestCase):
    def setUp(self):
        self.chunks = load_resume_chunks()
        self.adversarials = {a["id"]: a for a in build_adversarial_drafts(self.chunks)}

    def _report(self, adv_id):
        adv = self.adversarials[adv_id]
        return check_materials(adv["job"], adv["draft"], chunks=self.chunks), adv

    def test_fabricated_quote_caught(self):
        report, adv = self._report("adv-01-fabricated-quote")
        self.assertFalse(report["passed"])
        self.assertIn("evidence_verbatim",
                      [f["check"] for f in report["failures"]])

    def test_requirement_not_in_jd_caught(self):
        report, adv = self._report("adv-02-requirement-not-in-jd")
        self.assertFalse(report["passed"])
        self.assertIn("requirement_in_jd",
                      [f["check"] for f in report["failures"]])

    def test_wrong_chunk_caught(self):
        report, adv = self._report("adv-03-wrong-chunk")
        self.assertFalse(report["passed"])
        self.assertIn("evidence_supports_requirement",
                      [f["check"] for f in report["failures"]])

    def test_wrong_chunk_education_caught(self):
        # 2026-11-18 (adv-06): the Java point cites the education chunk
        # with a VERBATIM quote, so evidence_verbatim must pass — but
        # the chunk is about degrees, not Java, so the supports check
        # must fail, and it must be the ONLY failure.
        report, adv = self._report("adv-06-wrong-chunk-education")
        self.assertFalse(report["passed"])
        failing = [f["check"] for f in report["failures"]]
        self.assertIn("evidence_supports_requirement", failing)
        self.assertNotIn("evidence_verbatim", failing)
        self.assertNotIn("requirement_in_jd", failing)
        self.assertNotIn("no_orphan_requirements", failing)
        self.assertEqual(len(report["failures"]), 1)

    def test_wrong_chunk_education_repairs_to_skills_chunk(self):
        # The repair must re-cite the Java point from resume-skills,
        # not just drop it.
        adv = self.adversarials["adv-06-wrong-chunk-education"]
        report = check_materials(adv["job"], adv["draft"], chunks=self.chunks)
        fixed = repair_materials(adv["job"], adv["draft"], report["failures"],
                                 chunks=self.chunks)
        java = [p for p in fixed["draft"]["points"]
                if p["requirement"] == "Java"]
        self.assertEqual(len(java), 1)
        self.assertEqual(java[0]["evidence_chunk_id"], "resume-skills")
        self.assertEqual(len(fixed["draft"]["points"]), 5)

    def test_fake_gap_caught(self):
        report, adv = self._report("adv-04-fake-gap")
        self.assertFalse(report["passed"])
        self.assertIn("gap_is_real", [f["check"] for f in report["failures"]])

    def test_fake_gap_rest_api_caught(self):
        # 2026-11-19 (adv-07): "REST API" is listed as a gap even
        # though resume-skills covers it. gap_is_real must be the
        # ONLY failure — the other four points are honest, and REST
        # API is accounted for (as a gap), so no_orphan must pass.
        report, adv = self._report("adv-07-fake-gap-rest-api")
        self.assertFalse(report["passed"])
        failing = [f["check"] for f in report["failures"]]
        self.assertIn("gap_is_real", failing)
        self.assertNotIn("no_orphan_requirements", failing)
        self.assertNotIn("evidence_supports_requirement", failing)
        self.assertEqual(len(report["failures"]), 1)

    def test_orphan_requirement_caught(self):
        report, adv = self._report("adv-05-orphan-requirement")
        self.assertFalse(report["passed"])
        self.assertIn("no_orphan_requirements",
                      [f["check"] for f in report["failures"]])

    def test_honest_draft_passes_all_checks(self):
        cases = load_material_cases()
        for case in cases:
            job = {k: v for k, v in case.items()
                   if k not in ("expected_requirements", "expected_gaps", "note")}
            draft = generate_materials_cascade(job, chunks=self.chunks)
            report = check_materials(job, draft, chunks=self.chunks)
            self.assertTrue(report["passed"], f"{case['id']}: {report['failures']}")
            self.assertEqual(report["num_failed"], 0)

    def test_checker_report_shape(self):
        report, _ = self._report("adv-01-fabricated-quote")
        self.assertEqual(set(report.keys()),
                         {"passed", "checks", "failures", "num_checks", "num_failed"})
        self.assertEqual(report["num_checks"], len(report["checks"]))
        self.assertEqual(report["num_failed"], len(report["failures"]))
        self.assertGreater(report["num_checks"], 0)


class TestRepair(unittest.TestCase):
    def setUp(self):
        self.chunks = load_resume_chunks()
        self.adversarials = {a["id"]: a for a in build_adversarial_drafts(self.chunks)}

    def test_each_adversarial_repairs_then_rechecks_clean(self):
        for adv_id, adv in self.adversarials.items():
            report = check_materials(adv["job"], adv["draft"], chunks=self.chunks)
            fixed = repair_materials(adv["job"], adv["draft"], report["failures"],
                                     chunks=self.chunks)
            self.assertGreater(len(fixed["repairs"]), 0, adv_id)
            recheck = check_materials(adv["job"], fixed["draft"], chunks=self.chunks)
            self.assertTrue(recheck["passed"], f"{adv_id}: {recheck['failures']}")

    def test_repair_drops_requirement_not_in_jd(self):
        adv = self.adversarials["adv-02-requirement-not-in-jd"]
        report = check_materials(adv["job"], adv["draft"], chunks=self.chunks)
        fixed = repair_materials(adv["job"], adv["draft"], report["failures"],
                                 chunks=self.chunks)
        reqs = [p["requirement"] for p in fixed["draft"]["points"]]
        self.assertNotIn("Spark", reqs)

    def test_repair_turns_fake_gap_into_point(self):
        adv = self.adversarials["adv-04-fake-gap"]
        report = check_materials(adv["job"], adv["draft"], chunks=self.chunks)
        fixed = repair_materials(adv["job"], adv["draft"], report["failures"],
                                 chunks=self.chunks)
        self.assertNotIn("Kafka", fixed["draft"]["gaps"])
        self.assertIn("Kafka", [p["requirement"] for p in fixed["draft"]["points"]])

    def test_repair_turns_fake_gap_rest_api_into_point(self):
        # 2026-11-19 (adv-07): the repair must turn the fake "REST
        # API" gap into a point cited from resume-skills — retrieve()
        # hits resume-skills first, because it is the first chunk
        # whose keywords contain "rest api" — and the draft must be
        # whole again: 5 points, no gaps left.
        adv = self.adversarials["adv-07-fake-gap-rest-api"]
        report = check_materials(adv["job"], adv["draft"], chunks=self.chunks)
        fixed = repair_materials(adv["job"], adv["draft"], report["failures"],
                                 chunks=self.chunks)
        self.assertNotIn("REST API", fixed["draft"]["gaps"])
        rest = [p for p in fixed["draft"]["points"]
                if p["requirement"] == "REST API"]
        self.assertEqual(len(rest), 1)
        self.assertEqual(rest[0]["evidence_chunk_id"], "resume-skills")
        self.assertEqual(len(fixed["draft"]["points"]), 5)
        self.assertEqual(fixed["draft"]["gaps"], [])

    def test_repair_adds_orphan_as_point(self):
        adv = self.adversarials["adv-05-orphan-requirement"]
        report = check_materials(adv["job"], adv["draft"], chunks=self.chunks)
        fixed = repair_materials(adv["job"], adv["draft"], report["failures"],
                                 chunks=self.chunks)
        self.assertIn("AWS", [p["requirement"] for p in fixed["draft"]["points"]])

    def test_no_repair_means_flagged_for_human(self):
        adv = self.adversarials["adv-01-fabricated-quote"]
        result = check_and_repair(adv["job"], adv["draft"], chunks=self.chunks,
                                  budget=RunBudget(max_steps=MAX_STEPS_CHECKED),
                                  max_repairs=0)
        self.assertFalse(result["checker_passed"])
        self.assertTrue(result["needs_human"])
        self.assertEqual(result["repairs"], [])


class TestCheckedPipeline(unittest.TestCase):
    def test_checked_pipeline_passes_with_zero_repairs(self):
        chunks = load_resume_chunks()
        for case in load_material_cases():
            job = {k: v for k, v in case.items()
                   if k not in ("expected_requirements", "expected_gaps", "note")}
            result = generate_materials_checked(job, chunks=chunks)
            self.assertTrue(result["checker_passed"], case["id"])
            self.assertFalse(result["needs_human"])
            self.assertEqual(result["repairs"], [])
            self.assertLessEqual(result["steps_used"], MAX_STEPS_CHECKED)

    def test_checked_pipeline_carries_cascade_and_checker(self):
        chunks = load_resume_chunks()
        case = load_material_cases()[0]
        job = {k: v for k, v in case.items()
               if k not in ("expected_requirements", "expected_gaps", "note")}
        result = generate_materials_checked(job, chunks=chunks)
        self.assertIn("cascade", result)
        self.assertIn("checker", result)
        self.assertIn("tiers_tried", result["cascade"])

    def test_checker_eval_all_green(self):
        report = run_checker_eval()
        self.assertEqual(report["baseline"]["passed"], report["baseline"]["cases"])
        self.assertEqual(report["adversarial"]["detected"],
                         report["adversarial"]["cases"])
        self.assertEqual(report["adversarial"]["repaired"],
                         report["adversarial"]["cases"])
        self.assertEqual(report["checked_pipeline"]["passed"],
                         report["checked_pipeline"]["cases"])
        self.assertEqual(report["checked_pipeline"]["repairs_used"], 0)
        self.assertLessEqual(report["checked_pipeline"]["max_steps_used"],
                             MAX_STEPS_CHECKED)


class TestCheckerTool(unittest.TestCase):
    def test_tool_eval_runs(self):
        from job_agent.tools import draft_materials_checked
        report = draft_materials_checked(source="eval")
        self.assertEqual(report["adversarial"]["detected"], 7)
        self.assertEqual(report["adversarial"]["repaired"], 7)

    def test_tool_single_case(self):
        from job_agent.tools import draft_materials_checked
        draft = draft_materials_checked(case_id="eval-m01")
        self.assertTrue(draft["checker_passed"])
        self.assertEqual(draft["case_id"], "eval-m01")

    def test_tool_unknown_case_raises(self):
        from job_agent.tools import draft_materials_checked
        with self.assertRaises(ValueError):
            draft_materials_checked(case_id="eval-nope")

    def test_mock_routes_checker_keywords(self):
        from job_agent.agent import _mock_decide
        tool, _ = _mock_decide("帮我核对申请材料")
        self.assertEqual(tool, "draft_materials_checked")
        tool, _ = _mock_decide("用 checker 检查一下这份草稿")
        self.assertEqual(tool, "draft_materials_checked")


if __name__ == "__main__":
    unittest.main()
