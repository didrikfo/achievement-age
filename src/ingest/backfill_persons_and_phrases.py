"""One-off migration: create persons rows, backfill events.person_id, and split
events.event_phrase (still holding the old full sentences right after the SQL
rename in SUPABASE_SETUP.md) into just the suffix after the static prefix.

Run once, after applying the SQL in SUPABASE_SETUP.md's "Persons and event
detail fields" section:

    python -m ingest.backfill_persons_and_phrases
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.db import get_client

EVENTS_PAGE_SIZE = 1000


def _fetch_all_events(client) -> List[Dict]:
    """Page through all events (Supabase/PostgREST caps a single response at ~1000 rows)."""
    events: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("id, name, event_phrase")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        events.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return events


def expected_prefix(name: str) -> str:
    """The static prefix that used to be baked into every display_text value."""
    return f"The same age that {name} was when "


def strip_prefix(name: str, event_phrase: str) -> Optional[str]:
    """Return the suffix if event_phrase starts with the expected prefix for name, else None."""
    prefix = expected_prefix(name)
    if event_phrase.startswith(prefix):
        return event_phrase[len(prefix):]
    return None


def build_person_rows(names: List[str]) -> List[Dict]:
    """One persons row per distinct name, sorted for a deterministic insert order."""
    return [{"name": name} for name in sorted(set(names))]


def main() -> None:
    client = get_client()

    events = _fetch_all_events(client)

    # 1. One persons row per distinct name (does not attempt to split real name collisions).
    person_rows = build_person_rows([event["name"] for event in events])
    persons = client.table("persons").upsert(person_rows, on_conflict="name").execute().data
    name_to_person_id = {person["name"]: person["id"] for person in persons}

    # 2 & 3. Backfill person_id, and split event_phrase into just the suffix.
    unstrippable: List[Tuple[int, str]] = []
    for event in events:
        update: Dict = {"person_id": name_to_person_id[event["name"]]}

        suffix = strip_prefix(event["name"], event["event_phrase"])
        if suffix is not None:
            update["event_phrase"] = suffix
        else:
            unstrippable.append((event["id"], event["event_phrase"]))

        client.table("events").update(update).eq("id", event["id"]).execute()

    print(f"Backfilled {len(events)} events across {len(person_rows)} persons.")
    if unstrippable:
        print(f"{len(unstrippable)} rows did not match the expected prefix and were left as full sentences:")
        for event_id, text in unstrippable:
            print(f"  id={event_id}: {text!r}")


if __name__ == "__main__":  # pragma: no cover - manual one-off script
    main()
