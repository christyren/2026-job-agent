"""Milestone 2 tests: dedup, fingerprint stability, provenance, idempotency.
Run with: python3 -m unittest discover -s tests -v  (no API key, no network)"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_agent.fetchers import fetch_sample
from job_agent.jobs import deduplicate, job_fingerprint, merge_into_store, normalize_job
from job_agent.scoring import hard_filter, needs_review

TS = "2026-10-03T20:10:00+00:00"


class TestJobs(unittest.TestCase):
    def test_fingerprint_prefers_external_id(self):
        a = job_fingerprint("sample", "101", "ExampleCloud", "SWE II", "Seattle", "https://x/101")
        self.assertEqual(a, "sample:101")

    def test_fingerprint_fallback_ignores_case_and_extra_spaces(self):
        a = job_fingerprint("sample", "", "DataFlow", "Backend Engineer", "SF", "https://x/202")
        b = job_fingerprint("sample", "", "  dataflow ", "Backend  Engineer", "sf", "https://x/202")
        self.assertEqual(a, b)

    def test_sample_deduplicates_to_three_unique_jobs(self):
        raw = fetch_sample()
        jobs = [normalize_job(r, source="sample", fetched_at=TS) for r in raw]
        unique, skipped = deduplicate(jobs)
        self.assertEqual(len(raw), 5)       # sample intentionally contains duplicates
        self.assertEqual(len(unique), 3)
        self.assertEqual(skipped, 2)

    def test_provenance_fields_are_recorded(self):
        job = normalize_job(fetch_sample()[0], source="sample", fetched_at=TS)
        self.assertEqual(job.url, "https://example.com/jobs/101")  # source link
        self.assertEqual(job.fetched_at, TS)                        # fetch timestamp
        self.assertEqual(job.first_seen_at, TS)

    def test_merge_is_idempotent(self):
        raw = fetch_sample()
        jobs, _ = deduplicate([normalize_job(r, source="sample", fetched_at=TS) for r in raw])
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "jobs.json"
            first = merge_into_store(jobs, path=store)
            second = merge_into_store(jobs, path=store)
        self.assertEqual(first["new"], 3)
        self.assertEqual(second["new"], 0)  # second run: nothing new — idempotent
        self.assertEqual(second["total_stored"], 3)


class TestScoringInterface(unittest.TestCase):
    def test_hard_filter_blocks_excluded_company(self):
        result = hard_filter({"company": "Microsoft"}, {"exclude_companies": ["Microsoft"]})
        self.assertEqual(result.verdict, "no_match")
        self.assertEqual(result.confidence, 1.0)

    def test_low_confidence_needs_review(self):
        self.assertTrue(needs_review(0.5))
        self.assertFalse(needs_review(0.9))


if __name__ == "__main__":
    unittest.main()
