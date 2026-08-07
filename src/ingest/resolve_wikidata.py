"""Stage 3: resolve subjects that Stage 2 identified but the local births data
doesn't know, by looking up their birth date on Wikidata.

Run after Stage 2's merge step has populated data/tmp/wikidata_pending.json:

    python -m ingest.resolve_wikidata

Safe to rerun: every lookup outcome is cached by name, so a second run makes no
network requests for names already attempted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from core.io import load_json
from ingest.enrichment import write_review_entries
from ingest.match_events import (
    EVENTS_WITH_AGE_PATH,
    MATCHING_REVIEW_PATH,
    MAX_AGE_DAYS,
    append_matched_events,
    dedup_against_file,
)
from ingest.pipeline import calculate_age
from ingest.sources import wikidata
from ingest.subject_extraction import WIKIDATA_PENDING_PATH


def run_stage_three(
    pending_path: Path = WIKIDATA_PENDING_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
    cache_path: Path = wikidata.CACHE_PATH,
) -> Dict[str, int]:
    """Resolve each pending subject against Wikidata, storing matches and flagging the rest."""
    pending = load_json(pending_path)
    cache = wikidata.load_cache(cache_path)

    counts = {
        "resolved": 0, "ambiguous": 0, "not_found": 0,
        "insufficient_precision": 0, "implausible": 0, "unusable": 0,
    }
    matched: List[Dict] = []
    review: List[Dict] = []

    for entry in pending:
        subject = entry["subject"]
        if not str(entry.get("year", "")).isdigit():
            # Stage 1 never queues one of these, but a hand-edited queue file
            # could - and no age is computable from a non-numeric year.
            counts["unusable"] += 1
            review.append(
                {
                    "stage": "stage_3",
                    "issue_type": "unusable_year",
                    "name": subject,
                    "text": entry.get("text"),
                    "detail": f"event year {entry.get('year')!r} is not numeric",
                }
            )
            continue

        event_year = int(entry["year"])
        result = wikidata.lookup_birth_date(subject, event_year, cache)
        status = result["status"]

        if status != "resolved":
            counts[status] += 1
            review.append(
                {
                    "stage": "stage_3",
                    "issue_type": status,
                    "name": subject,
                    "text": entry.get("text"),
                    "detail": f"Wikidata lookup for {subject!r} returned {status}",
                }
            )
            continue

        age = calculate_age(
            result["year"], result["month"], result["day"],
            event_year, int(entry["month"]), int(entry["day"]),
        )
        if age is None or not (0 <= age <= MAX_AGE_DAYS):
            counts["implausible"] += 1
            review.append(
                {
                    "stage": "stage_3",
                    "issue_type": "implausible_age",
                    "name": subject,
                    "text": entry.get("text"),
                    "detail": f"age for {subject!r} outside 0..{MAX_AGE_DAYS} days",
                }
            )
            continue

        counts["resolved"] += 1
        matched.append(
            {
                "year": entry["year"], "month": entry["month"], "day": entry["day"],
                "text": entry["text"], "name": subject, "age": age,
            }
        )

    counts["appended"] = append_matched_events(matched, matched_path, review_path)
    wikidata.save_cache(cache, cache_path)
    write_review_entries(dedup_against_file(review_path, review), review_path)
    return counts


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run_stage_three())
