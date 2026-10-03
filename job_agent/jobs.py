"""Milestone 2: job ingestion — normalize, deduplicate, store with provenance.

Concepts:
- ETL / ingestion pipeline: Extract (fetcher) -> Transform (normalize) -> Load (store)
- Stable ID / fingerprint: the same job must get the same id every run, otherwise
  deduplication is impossible. Prefer the source's external_id; fall back to a
  hash of normalized (company, title, location, url).
- Idempotency: running the fetch twice must NOT create duplicates. Second run
  should report new=0 — this is how real crawlers / data pipelines are tested.
- Provenance: every record keeps source_url + fetched_at, so you can always
  answer "where did this come from, and when did we see it?"
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STORE_PATH = DATA_DIR / "jobs.json"


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601, e.g. 2026-10-03T20:10:00+00:00."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_text(value: str) -> str:
    """Normalize text for fingerprinting only: lowercase, collapse whitespace, strip."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def job_fingerprint(source: str, external_id: str, company: str, title: str, location: str, url: str) -> str:
    """Stable id for a job. Same input -> same id, across runs and machines."""
    if external_id:
        return f"{source}:{external_id}"
    raw = "|".join([_norm_text(company), _norm_text(title), _norm_text(location), _norm_text(url)])
    return f"{source}:sha1:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


@dataclass
class Job:
    id: str
    source: str            # e.g. "sample" or "greenhouse"
    external_id: str       # id from the source system, "" if none
    company: str
    title: str
    location: str
    url: str               # source_url — provenance: where to apply / verify
    fetched_at: str        # ISO-8601 UTC — provenance: when we fetched it
    first_seen_at: str
    last_seen_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_job(raw: dict, source: str, fetched_at: str) -> Job:
    """Turn one messy raw record (any source) into our one clean Job shape."""
    company = str(raw.get("company", "")).strip()
    title = str(raw.get("title", "")).strip()
    location = str(raw.get("location", "")).strip()
    url = str(raw.get("url") or raw.get("absolute_url") or "").strip()
    external_id = str(raw.get("external_id") or raw.get("id") or "").strip()
    return Job(
        id=job_fingerprint(source, external_id, company, title, location, url),
        source=source,
        external_id=external_id,
        company=company,
        title=title,
        location=location,
        url=url,
        fetched_at=fetched_at,
        first_seen_at=fetched_at,
        last_seen_at=fetched_at,
    )


def deduplicate(jobs: list[Job]) -> tuple[list[Job], int]:
    """Drop duplicates inside one batch, keeping the first occurrence."""
    seen: set[str] = set()
    unique: list[Job] = []
    skipped = 0
    for job in jobs:
        if job.id in seen:
            skipped += 1
            continue
        seen.add(job.id)
        unique.append(job)
    return unique, skipped


def load_store(path: Path = STORE_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_store(jobs: list[dict], path: Path = STORE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def merge_into_store(incoming: list[Job], path: Path = STORE_PATH) -> dict:
    """Merge a deduplicated batch into the on-disk store. Idempotent.

    - New id      -> append, count as new
    - Existing id -> only refresh last_seen_at / fetched_at, count as duplicate
    Returns a small summary dict (this is what the fetch_jobs tool returns).
    """
    existing = load_store(path)
    by_id = {row["id"]: row for row in existing}
    new_count = 0
    dup_count = 0
    for job in incoming:
        if job.id in by_id:
            by_id[job.id]["last_seen_at"] = job.last_seen_at
            by_id[job.id]["fetched_at"] = job.fetched_at
            dup_count += 1
        else:
            by_id[job.id] = job.to_dict()
            new_count += 1
    merged = list(by_id.values())
    save_store(merged, path)
    return {"new": new_count, "duplicates_skipped": dup_count, "total_stored": len(merged)}
