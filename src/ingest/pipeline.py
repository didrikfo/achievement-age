"""Legacy ingestion pipeline that produces intermediate JSON files."""

from __future__ import annotations

from datetime import date
from typing import Dict, List

from core.io import load_json


def calculate_age(birth_year: int, birth_month: int, birth_day: int, event_year: int, event_month: int, event_day: int) -> int | None:
    """Return the age in days between the birth date and event date."""
    try:
        dob = date(birth_year, birth_month, birth_day)
        event_date = date(event_year, event_month, event_day)
        return (event_date - dob).days
    except Exception:
        return None


def only_name_and_text_from_json(event_json_path: str) -> List[Dict[str, object]]:
    events_with_age = load_json(event_json_path)
    return [{"name": event.get("name"), "text": event.get("text")} for event in events_with_age]


def main() -> None:  # pragma: no cover - manual helper
    """Stage 0/1 matching now lives in ingest.match_events (Aho-Corasick, widened pool)."""
    from ingest.match_events import run_stage_one

    print(run_stage_one())


if __name__ == "__main__":  # pragma: no cover - manual helper
    main()
