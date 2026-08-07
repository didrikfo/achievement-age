"""One-off script: load data/displayable_events.json into the Supabase events table.

Run by hand once the Supabase tables exist (see SUPABASE_SETUP.md):

    python -m ingest.migrate_to_supabase
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from core.config import DATA_DIR
from core.db import get_client
from core.io import load_json
from ingest.backfill_persons_and_phrases import build_person_rows
from ingest.enrichment import build_tag_rows

BATCH_SIZE = 200
EVENTS_PAGE_SIZE = 1000


def fetch_existing_event_keys(client) -> Set[Tuple[str, str]]:
    """Every (name, text) pair already in Supabase, so a rerun can't duplicate them.

    Paginated - PostgREST caps a single response at ~1000 rows.
    """
    keys: Set[Tuple[str, str]] = set()
    start = 0
    while True:
        page = (
            client.table("events")
            .select("name, text")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        keys.update((row["name"], row["text"]) for row in page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return keys


def filter_new_entries(entries: List[Dict], existing_keys: Set[Tuple[str, str]]) -> List[Dict]:
    """Drop entries already in Supabase, and any duplicated within entries itself."""
    seen = set(existing_keys)
    new_entries: List[Dict] = []
    for entry in entries:
        key = (entry["name"], entry["text"])
        if key in seen:
            continue
        seen.add(key)
        new_entries.append(entry)
    return new_entries


def _to_event_row(entry: Dict, name_to_person_id: Dict[str, int]) -> Dict:
    return {
        "name": entry["name"],
        "person_id": name_to_person_id.get(entry["name"]),
        "text": entry["text"],
        "event_phrase": entry.get("event_phrase") or entry["display_text"],
        "year": int(entry["year"]),
        "month": int(entry["month"]),
        "day": int(entry["day"]),
        "age_days": int(entry["age"]),
        "event_type": "achievement",
        "source": "initial_migration",
    }


def main() -> None:
    entries: List[Dict] = load_json(DATA_DIR / "displayable_events.json")

    client = get_client()

    already_migrated = fetch_existing_event_keys(client)
    entries = filter_new_entries(entries, already_migrated)
    if not entries:
        print("Nothing new to migrate.")
        return
    print(f"{len(entries)} new event(s) to migrate.")

    person_rows = build_person_rows([entry["name"] for entry in entries])
    persons = client.table("persons").upsert(person_rows, on_conflict="name").execute().data
    name_to_person_id = {person["name"]: person["id"] for person in persons}

    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}

    rows = [_to_event_row(entry, name_to_person_id) for entry in entries]

    inserted = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        batch_entries = entries[start : start + BATCH_SIZE]
        inserted_events = client.table("events").insert(batch).execute().data

        tag_rows: List[Dict] = []
        for inserted_event, entry in zip(inserted_events, batch_entries):
            tag_rows.extend(build_tag_rows(inserted_event["id"], entry.get("tags") or [], tag_name_to_id))
        if tag_rows:
            client.table("event_tags").insert(tag_rows).execute()

        inserted += len(batch)
        print(f"Inserted {inserted}/{len(rows)}")

    print(f"Done. Inserted {inserted} events.")


if __name__ == "__main__":  # pragma: no cover - manual helper
    main()
