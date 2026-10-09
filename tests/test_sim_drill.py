"""Tests for the simulation preflight + timebox drill (2026-10-09).

The point of these tests mirrors the point of the module: if any of
them goes red on simulation morning, you fix the environment BEFORE
the timer starts, not during it.
"""
import unittest

from job_agent.sim_drill import (
    RETRO_QUESTIONS,
    TIMEBOX,
    format_preflight,
    format_timebox,
    phase_at,
    run_preflight,
)


class TestTimebox(unittest.TestCase):
    def test_phases_cover_sixty_minutes_without_gaps(self):
        starts = [p.start for p in TIMEBOX]
        self.assertEqual(starts[0], 0)
        for prev, nxt in zip(TIMEBOX, TIMEBOX[1:]):
            self.assertEqual(prev.end, nxt.start)

    def test_phase_at_boundaries(self):
        self.assertEqual(phase_at(0).name, "read_all")
        self.assertEqual(phase_at(4).name, "read_all")
        self.assertEqual(phase_at(5).name, "part1")
        self.assertEqual(phase_at(24).name, "part1")
        self.assertEqual(phase_at(25).name, "later_parts")
        self.assertEqual(phase_at(45).name, "edge_checks")
        self.assertEqual(phase_at(55).name, "fix_only")
        self.assertEqual(phase_at(60).name, "fix_only")

    def test_phase_at_clamps_out_of_range(self):
        self.assertEqual(phase_at(-3).name, "read_all")
        self.assertEqual(phase_at(999).name, "fix_only")

    def test_every_phase_has_a_checkpoint(self):
        for phase in TIMEBOX:
            self.assertTrue(phase.job)
            self.assertTrue(phase.checkpoint)


class TestPreflight(unittest.TestCase):
    def test_preflight_is_all_green(self):
        result = run_preflight()
        self.assertTrue(result["all_ok"], result["checks"])

    def test_preflight_checks_the_three_edges(self):
        checks = run_preflight()["checks"]
        for name in ("precision", "duplicate", "empty"):
            self.assertTrue(checks[name], name)

    def test_preflight_keeps_formal_date_off_today(self):
        # The drill must never quietly move the real attempt earlier:
        # practice today (2026-10-09), formal attempt on 2026-10-12.
        result = run_preflight()
        self.assertEqual(result["formal_date"], "2026-10-12")
        self.assertIn("do NOT press the real Start", result["instruction"])


class TestRetro(unittest.TestCase):
    def test_retro_is_exactly_two_questions(self):
        # Capped on purpose: extension point + input format, nothing else.
        self.assertEqual(len(RETRO_QUESTIONS), 2)
        joined = " ".join(RETRO_QUESTIONS).lower()
        self.assertIn("extension point", joined)
        self.assertIn("input format", joined)

    def test_formatters_render(self):
        self.assertIn("all_ok: True", format_preflight(run_preflight()))
        text = format_timebox()
        self.assertIn("read_all", text)
        self.assertIn("fix_only", text)


if __name__ == "__main__":
    unittest.main()
