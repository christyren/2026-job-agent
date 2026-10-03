"""Milestone 2: fetchers — the Extract step. Each fetcher returns raw dicts in
ONE common shape {company, title, location, url, external_id}, so normalize
and the rest of the pipeline never need to know which source a job came from.

Two sources today:
- sample:     local JSON file, works offline, deterministic — use this to learn/test
- greenhouse: Greenhouse public Job Board API, no API key needed:
              GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
              Docs summary: https://jobspipe.dev/sources/greenhouse
              Tip: do NOT add ?content=true for the list call (payload ~12x bigger);
              fetch full descriptions later, only for jobs that pass filtering.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"


def fetch_sample(path: Path | None = None) -> list[dict]:
    """Read the local sample file. Contains intentional duplicates for dedup practice."""
    p = Path(path) if path else DATA_DIR / "sample_jobs_raw.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def fetch_greenhouse(board_token: str, timeout: int = 15) -> list[dict]:
    """Fetch one company's public Greenhouse board and map it to the common shape."""
    url = f"{GREENHOUSE_BASE}/{board_token}/jobs"
    req = urllib.request.Request(url, headers={"User-Agent": "job-agent-learning/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    jobs = []
    for raw in payload.get("jobs", []):
        loc = raw.get("location") or {}
        jobs.append({
            "company": board_token,
            "title": raw.get("title", ""),
            "location": loc.get("name", "") if isinstance(loc, dict) else str(loc),
            "url": raw.get("absolute_url", ""),
            "external_id": str(raw.get("id", "")),
        })
    return jobs
