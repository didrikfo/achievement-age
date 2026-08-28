"""Parsing for the Nobel Prize laureate dataset - a structured source where the
subject is already named, unlike the scraped-text corpus ingest.match_events
resolves. See docs/superpowers/specs/2026-08-28-nobel-prize-ingestion-design.md.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR

NOBEL_CSV_PATH = DATA_DIR / "raw_data" / "nobel_prizes_1901-2025_cleaned.csv"

#: Provenance label written to events.source - the single source of truth other
#: modules (nobel_llm_utils, migrate_nobel_to_supabase, backfill_event_enrichment)
#: import, so the label can never drift between them.
NOBEL_SOURCE = "nobel_prize_dataset"

#: Deterministic category -> tag mapping. Never asked of the LLM: a Nobel
#: category is a closed, known fact, and letting a subagent re-derive the tag
#: would risk disagreeing with this table for no better information than it
#: already has (see the design spec's "Tags: deterministic, not LLM-chosen").
NOBEL_CATEGORY_TAGS: Dict[str, str] = {
    "Physics": "science",
    "Chemistry": "science",
    "Physiology or Medicine": "health",
    "Literature": "arts",
    "Peace": "politics",
    "Economic Sciences": "economics",
}

_ECONOMIC_SCIENCES_DISPLAY = "the Nobel Memorial Prize in Economic Sciences"


def category_display_name(category: str) -> str:
    """The prize name as it reads in a sentence.

    Economic Sciences is officially the Nobel Memorial Prize in Economic
    Sciences in Memory of Alfred Nobel, not one of the five prizes established
    in Nobel's 1895 will - the other five categories read as "the Nobel Prize
    in {category}", except Peace, whose universal real-world name is "the
    Nobel Peace Prize" rather than the grammatically-awkward "the Nobel Prize
    in Peace".
    """
    if category == "Economic Sciences":
        return _ECONOMIC_SCIENCES_DISPLAY
    if category == "Peace":
        return "the Nobel Peace Prize"
    return f"the Nobel Prize in {category}"


def build_event_text(record: Dict) -> str:
    """The deterministic raw-fact sentence stored in events.text.

    Plays the same role the scraped Wikipedia sentence plays for the rest of
    the corpus: a stable source-of-truth string that check_facts_preserved
    checks the LLM-written event_phrase against.
    """
    return f'{record["name"]} won {category_display_name(record["category"])}: "{record["motivation"]}"'


def _parse_date_awarded(value: str) -> Tuple[int, int, int]:
    """date_awarded is always MM/DD/YYYY."""
    month, day, year = value.split("/")
    return int(year), int(month), int(day)


def _parse_birth_date(value: str) -> Optional[Tuple[int, int, int]]:
    """birth_date is YYYY-MM-DD for some rows and MM/DD/YYYY for others, or blank.

    Both formats coexist in the real export (295 ISO rows, 457 US-format rows).
    Confirmed against real laureates' known birthdates that the non-ISO rows
    are MM/DD/YYYY, matching date_awarded's format, not DD/MM/YYYY.
    """
    value = value.strip()
    if not value:
        return None
    if "-" in value:
        year, month, day = value.split("-")
        return int(year), int(month), int(day)
    month, day, year = value.split("/")
    return int(year), int(month), int(day)


def load_nobel_records(csv_path: Path = NOBEL_CSV_PATH) -> List[Dict]:
    """Load every row of the Nobel CSV into a canonical record dict.

    utf-8-sig strips the BOM the export leaves on the first header
    (award_year), matching core.io.load_json's existing convention for the
    same issue.
    """
    records: List[Dict] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            award_year, award_month, award_day = _parse_date_awarded(row["date_awarded"])
            birth = _parse_birth_date(row["birth_date"])
            records.append(
                {
                    "laureate_id": row["laureate_id"],
                    "name": row["known_name"],
                    "category": row["category"],
                    "award_year": award_year,
                    "award_month": award_month,
                    "award_day": award_day,
                    "motivation": row["motivation"],
                    "wikipedia_url": row["wikipedia_url"] or None,
                    "birth_year": birth[0] if birth else None,
                    "birth_month": birth[1] if birth else None,
                    "birth_day": birth[2] if birth else None,
                }
            )
    return records


def split_by_birth_data(records: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """(with_birth_date, missing_birth_date) - the latter needs Wikidata resolution."""
    with_birth_date = [r for r in records if r["birth_year"] is not None]
    missing_birth_date = [r for r in records if r["birth_year"] is None]
    return with_birth_date, missing_birth_date
