"""Run with: python3 -m unittest discover -s tests -v  (no API key needed)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.tools import dispatch_tool, estimate_cost, get_preferences


class TestTools(unittest.TestCase):
    def test_preferences_exclude_former_employers(self):
        prefs = get_preferences()
        self.assertIn("Microsoft", prefs["exclude_companies"])
        self.assertGreaterEqual(prefs["min_base_usd"], 150000)

    def test_estimate_cost_math(self):
        # 100 jobs * (3000 in + 500 out): 0.3M*$2 + 0.05M*$10 = $0.60 + $0.50 = $1.10
        result = estimate_cost(num_jobs=100)
        self.assertEqual(result["estimated_cost_usd"], 1.1)

    def test_dispatch_unknown_tool_raises(self):
        with self.assertRaises(KeyError):
            dispatch_tool("does_not_exist", {})


if __name__ == "__main__":
    unittest.main()
