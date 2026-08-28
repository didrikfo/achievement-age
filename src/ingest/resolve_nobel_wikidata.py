"""Resolve birth dates for Nobel laureates whose CSV row has none (243 of 995).

Run after ingest.sources.nobel.split_by_birth_data has produced the
missing_birth_date list:

    python -m ingest.resolve_nobel_wikidata

Safe to rerun: every lookup outcome is cached by name in the same on-disk
cache ingest.sources.wikidata already uses for the historical corpus's Stage
3, so a second run makes no network requests for names already attempted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from core.config import DATA_DIR
from core.io import save_to_json
from ingest.enrichment import write_review_entries
from ingest.sources import wikidata
from ingest.sources.nobel import NOBEL_CSV_PATH, load_nobel_records, split_by_birth_data

REVIEW_PATH = DATA_DIR / "tmp" / "nobel_wikidata_review.json"
OUTPUT_PATH = DATA_DIR / "tmp" / "nobel_resolved_thin.json"


def resolve_missing_birth_dates(
    records: List[Dict], cache: Dict, review_path: Path = REVIEW_PATH
) -> List[Dict]:
    """Resolve each record's birth date via Wikidata, returning only the resolved ones.

    Unresolved records (ambiguous/not_found/insufficient_precision) are written
    to review_path and excluded from the return value - not blocking the run,
    rerunnable later once the cache or the source data improves.
    """
    resolved: List[Dict] = []
    review_entries: List[Dict] = []

    for record in records:
        result = wikidata.lookup_birth_date(record["name"], record["award_year"], cache)
        status = result["status"]
        if status != "resolved":
            review_entries.append(
                {
                    "stage": "nobel_wikidata",
                    "issue_type": status,
                    "name": record["name"],
                    "detail": f"Wikidata lookup for {record['name']!r} (award_year={record['award_year']}) returned {status}",
                }
            )
            continue
        resolved.append(
            {**record, "birth_year": result["year"], "birth_month": result["month"], "birth_day": result["day"]}
        )

    write_review_entries(review_entries, review_path)
    return resolved


def run(
    csv_path: Path = NOBEL_CSV_PATH,
    output_path: Path = OUTPUT_PATH,
    cache_path: Path = wikidata.CACHE_PATH,
    review_path: Path = REVIEW_PATH,
) -> Dict[str, int]:
    """Load the CSV, resolve the missing-birth-date rows, write survivors to output_path."""
    records = load_nobel_records(csv_path)
    _, missing = split_by_birth_data(records)

    cache = wikidata.load_cache(cache_path)
    resolved = resolve_missing_birth_dates(missing, cache, review_path)
    wikidata.save_cache(cache, cache_path)

    save_to_json(output_path, resolved)
    return {"missing": len(missing), "resolved": len(resolved)}


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run())
