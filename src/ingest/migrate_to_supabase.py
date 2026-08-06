"""One-off script: load data/displayable_events.json into the Supabase events table.

Run by hand once the Supabase tables exist (see SUPABASE_SETUP.md):

    python -m ingest.migrate_to_supabase
"""

from __future__ import annotations

from typing import Dict, List

from core.config import DATA_DIR
from core.db import get_client
from core.io import load_json

BATCH_SIZE = 200


def _to_event_row(entry: Dict) -> Dict:
    return {
        "name": entry["name"],
        "text": entry["text"],
        "display_text": entry["display_text"],
        "year": int(entry["year"]),
        "month": int(entry["month"]),
        "day": int(entry["day"]),
        "age_days": int(entry["age"]),
        "event_type": "achievement",
        "source": "initial_migration",
    }


def main() -> None:
    entries: List[Dict] = load_json(DATA_DIR / "displayable_events.json")
    rows = [_to_event_row(entry) for entry in entries]

    client = get_client()
    inserted = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        client.table("events").insert(batch).execute()
        inserted += len(batch)
        print(f"Inserted {inserted}/{len(rows)}")

    print(f"Done. Inserted {inserted} events.")


if __name__ == "__main__":  # pragma: no cover - manual helper
    main()
