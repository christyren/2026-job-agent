import unittest

from job_agent.dayof import (
    FORMAL_DATE,
    HUMAN_CHECK_IDS,
    can_start,
    missing_human_checks,
    run_dayof,
)


class TestDayOf(unittest.TestCase):
    def test_formal_date_is_today(self):
        self.assertEqual(FORMAL_DATE, "2026-10-12")
        result = run_dayof()
        self.assertEqual(result["mode"], "formal")
        # Today's instruction must NOT be the stale practice one.
        self.assertIn("IS the formal attempt day", result["instruction"])
        self.assertNotIn("do NOT press", result["instruction"])

    def test_code_preflight_is_reused_and_green(self):
        result = run_dayof()
        self.assertTrue(result["code_preflight"]["all_ok"])
        for name in ("precision", "duplicate", "empty", "timebox_covers_60"):
            self.assertTrue(result["code_preflight"]["checks"][name], name)

    def test_human_checks_cover_the_run_sheet(self):
        # The run sheet's non-code items must all be present by id.
        for cid in ("cat_fed_noon", "chrome_clean", "phone_silent",
                    "faq_scanned", "handwritten"):
            self.assertIn(cid, HUMAN_CHECK_IDS)

    def test_nothing_confirmed_blocks_start(self):
        gate = can_start(code_ok=True, confirmed=[])
        self.assertFalse(gate["go"])
        self.assertEqual(gate["missing"], list(HUMAN_CHECK_IDS))

    def test_all_confirmed_and_code_green_allows_start(self):
        gate = can_start(code_ok=True, confirmed=HUMAN_CHECK_IDS)
        self.assertTrue(gate["go"])
        self.assertEqual(gate["missing"], [])

    def test_code_red_blocks_even_if_human_all_confirmed(self):
        gate = can_start(code_ok=False, confirmed=HUMAN_CHECK_IDS)
        self.assertFalse(gate["go"])
        self.assertEqual(gate["missing"][0], "code_preflight")

    def test_missing_lists_only_the_unconfirmed(self):
        missing = missing_human_checks(["cat_fed_noon", "phone_silent"])
        self.assertNotIn("cat_fed_noon", missing)
        self.assertNotIn("phone_silent", missing)
        self.assertIn("faq_scanned", missing)

    def test_retro_still_capped_at_two(self):
        self.assertEqual(len(run_dayof()["retro_questions"]), 2)


if __name__ == "__main__":
    unittest.main()
