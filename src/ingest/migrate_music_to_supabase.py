"""Person resolution, guards, and the final Supabase write for music events.

Two phases, mirroring ingest.migrate_nobel_to_supabase - but with no LLM pass
in between, since ingest.sources.music words every event deterministically:

    python -m ingest.migrate_music_to_supabase prepare   # persons upsert + guards
    # ... review data/music_displayable.json and data/tmp/music_*_review.json ...
    python -m ingest.migrate_music_to_supabase insert    # events + event_tags

Input is data/tmp/music_resolved.json from ingest.resolve_music_persons. See
docs/superpowers/specs/2026-09-24-music-events-ingestion-design.md.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.config import DATA_DIR
from core.db import get_client
from core.io import load_json, save_to_json
from ingest.enrichment import build_tag_rows, write_review_entries
from ingest.migrate_nobel_to_supabase import (
    EVENTS_PAGE_SIZE,
    build_person_rows,
    fetch_all_persons,
    find_normalized_name_collisions,
)
from ingest.resolve_music_persons import RESOLVED_PATH
from ingest.sources.music import KIND_ORDER, KIND_SOURCES, MUSIC_TAG, build_event_phrase, event_clause, event_detail

MUSIC_DISPLAYABLE_PATH = DATA_DIR / "music_displayable.json"
MUSIC_PERSON_REVIEW_PATH = DATA_DIR / "tmp" / "music_person_review.json"
MUSIC_DUPLICATE_REVIEW_PATH = DATA_DIR / "tmp" / "music_duplicate_review.json"

EventKey = Tuple[int, int, int, int]

#: Musicians who share a name with someone already in the calendar, checked by
#: hand: Jack Johnson the boxer and Machine Gun Kelly the gangster. persons is
#: unique on name, so each gets a suffixed row of their own. This is a list, not
#: a reaction to every birth-date conflict, because a conflict can also mean the
#: existing person's age is wrong (Michael Jackson's were), and that needs fixing.
MUSICIAN_NAMESAKES = {"Jack Johnson", "Machine Gun Kelly"}


# --- pure functions ---


def person_name(name: str) -> str:
    """The persons.name a credited name is stored under.

    Only the persons row carries the suffix, where it also happens to be the
    Wikipedia article title. events.name stays the plain name: core.matching
    expects it inside the phrase, and a reader only ever needs "Jack Johnson".
    """
    return f"{name} (musician)" if name in MUSICIAN_NAMESAKES else name


def merge_same_day(records: List[Dict]) -> List[Dict]:
    """One event per person per day, built from that person's resolved records.

    A person can top two charts in the same week - Taylor Swift's Midnights and
    "Anti-Hero" both reached number one on the charts dated 2022-11-05 - and
    the calendar allows one event per person per day, so their clauses are
    joined into one phrase and their detail sentences into one text.
    """
    groups: Dict[Tuple[str, int, int, int], List[Dict]] = {}
    for record in records:
        groups.setdefault((record["name"], record["year"], record["month"], record["day"]), []).append(record)

    events: List[Dict] = []
    for (name, year, month, day), group in sorted(groups.items(), key=lambda item: (item[0][1:], item[0][0])):
        group = sorted(group, key=lambda record: (KIND_ORDER.index(record["kind"]), record["title"]))
        first = group[0]
        events.append(
            {
                "name": person_name(name),
                "display_name": name,
                "year": year,
                "month": month,
                "day": day,
                "age_days": first["age_days"],
                "birth_year": first["birth_year"],
                "birth_month": first["birth_month"],
                "birth_day": first["birth_day"],
                "birth_source": first["birth_source"],
                "wikidata_qid": first.get("wikidata_qid"),
                "source": KIND_SOURCES[first["kind"]],
                "record_keys": [record["record_key"] for record in group],
                "text": " ".join(event_detail(record) for record in group),
                "event_phrase": build_event_phrase(name, [event_clause(record, name) for record in group]),
                "tags": [MUSIC_TAG],
            }
        )
    return events


def implied_birth_dates(event_rows: List[Dict]) -> Dict[int, Set[date]]:
    """person_id -> the birth dates their existing events imply (event date - age_days)."""
    implied: Dict[int, Set[date]] = {}
    for row in event_rows:
        if row.get("person_id") is None or row.get("age_days") is None:
            continue
        try:
            birth = date(row["year"], row["month"], row["day"]) - timedelta(days=row["age_days"])
        except (ValueError, OverflowError):
            continue  # BC or otherwise unrepresentable dates - nothing to compare
        implied.setdefault(row["person_id"], set()).add(birth)
    return implied


def find_birth_date_conflicts(
    events: List[Dict], existing_persons: List[Dict], implied: Dict[int, Set[date]]
) -> List[Dict]:
    """Events whose name is an existing person's, but whose birth date isn't theirs.

    persons rows are upserted by exact name, so a musician sharing a name with
    someone already in the calendar would otherwise silently become that
    person. Their existing events pin down whose birthday the row stands for:
    a disagreement is flagged and skipped (detect-don't-guess), not merged.
    A person with no dated events yet has nothing to disagree with.
    """
    id_by_name = {person["name"]: person["id"] for person in existing_persons}
    conflicts: List[Dict] = []
    reported: Set[str] = set()
    for event in events:
        person_id = id_by_name.get(event["name"])
        existing = implied.get(person_id) if person_id is not None else None
        if not existing or event["name"] in reported:
            continue
        ours = date(event["birth_year"], event["birth_month"], event["birth_day"])
        if ours not in existing:
            reported.add(event["name"])
            conflicts.append(
                {
                    "issue_type": "birth_date_conflict",
                    "name": event["name"],
                    "detail": (
                        f"resolved birth date {ours.isoformat()} (via {event['birth_source']}) but existing "
                        f"person id={person_id}'s events imply {sorted(d.isoformat() for d in existing)}"
                    ),
                }
            )
    return conflicts


def split_duplicate_days(
    events: List[Dict], existing_event_keys: Dict[EventKey, Dict]
) -> Tuple[List[Dict], List[Tuple[Dict, Dict]]]:
    """(keep, blocked): an event whose person already has an events row that day is blocked.

    The Wikipedia "on this day" corpus already covers a few of these (e.g.
    ABBA's Eurovision win, or the Beatles' first US number one), and the
    calendar allows one event per person per day.
    """
    keep: List[Dict] = []
    blocked: List[Tuple[Dict, Dict]] = []
    for event in events:
        key = (event.get("person_id"), event["year"], event["month"], event["day"])
        if event.get("person_id") is not None and key in existing_event_keys:
            blocked.append((event, existing_event_keys[key]))
        else:
            keep.append(event)
    return keep, blocked


def to_event_row(event: Dict) -> Dict:
    """A prepared music event -> an events insert row."""
    return {
        "name": event["display_name"],
        "person_id": event["person_id"],
        "text": event["text"],
        "event_phrase": event["event_phrase"],
        "year": event["year"],
        "month": event["month"],
        "day": event["day"],
        "age_days": event["age_days"],
        "event_type": "achievement",
        "source": event["source"],
    }


# --- paginated Supabase reads ---


def fetch_dated_event_rows(client) -> List[Dict]:
    """(id, person_id, year, month, day, age_days, text) for every events row, paginated."""
    rows: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("id, person_id, year, month, day, age_days, text")
            .order("id")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return rows


def event_keys(event_rows: List[Dict]) -> Dict[EventKey, Dict]:
    """(person_id, year, month, day) -> {"id", "text"}; rows with no person_id are skipped."""
    return {
        (row["person_id"], row["year"], row["month"], row["day"]): {"id": row["id"], "text": row["text"]}
        for row in event_rows
        if row.get("person_id") is not None
    }


# --- orchestrators (impure) ---


def prepare_music_events(
    records: Optional[List[Dict]] = None,
    output_path: Path = MUSIC_DISPLAYABLE_PATH,
    person_review_path: Path = MUSIC_PERSON_REVIEW_PATH,
    duplicate_review_path: Path = MUSIC_DUPLICATE_REVIEW_PATH,
) -> Dict[str, int]:
    """Phase 1: merge same-day records, guard names, upsert persons, guard duplicates.

    Writes the survivors to output_path for review before insert_music_events.
    """
    if records is None:
        records = load_json(RESOLVED_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events = merge_same_day(records)
    merged_count = len(events)

    client = get_client()
    existing_persons = fetch_all_persons(client)
    existing_rows = fetch_dated_event_rows(client)

    collisions = find_normalized_name_collisions(events, existing_persons)
    conflicts = find_birth_date_conflicts(events, existing_persons, implied_birth_dates(existing_rows))
    write_review_entries(collisions + conflicts, person_review_path)
    skipped_names = {entry["name"] for entry in collisions + conflicts}
    events = [event for event in events if event["name"] not in skipped_names]

    person_rows = build_person_rows(events)
    name_to_person_id: Dict[str, int] = {}
    for start in range(0, len(person_rows), 500):
        upserted = client.table("persons").upsert(person_rows[start : start + 500], on_conflict="name").execute().data
        name_to_person_id.update({person["name"]: person["id"] for person in upserted})
    missing = {event["name"] for event in events} - name_to_person_id.keys()
    if missing:
        raise RuntimeError(f"{len(missing)} name(s) unresolved after upsert: {sorted(missing)[:5]}")
    events = [{**event, "person_id": name_to_person_id[event["name"]]} for event in events]

    keep, blocked = split_duplicate_days(events, event_keys(existing_rows))
    write_review_entries(
        [
            {
                "issue_type": "duplicate_day",
                "name": event["name"],
                "detail": (
                    f"event already exists on {event['year']}-{event['month']:02d}-{event['day']:02d} "
                    f"(existing event id={existing['id']}: {existing['text'][:120]!r})"
                ),
            }
            for event, existing in blocked
        ],
        duplicate_review_path,
    )

    save_to_json(output_path, keep)
    return {
        "resolved_records": len(records),
        "events": merged_count,
        "name_collisions": len(collisions),
        "birth_date_conflicts": len(conflicts),
        "duplicate_day_blocked": len(blocked),
        "pending": len(keep),
    }


def insert_music_events(events: Optional[List[Dict]] = None, batch_size: int = 200) -> int:
    """Phase 2: insert events/event_tags rows for prepared music events.

    Safe to rerun, like insert_nobel_events: an event whose (person_id, year,
    month, day) already has an events row - e.g. from an earlier, partially
    failed run - is skipped, and the return value counts only new rows.
    """
    if events is None:
        events = load_json(MUSIC_DISPLAYABLE_PATH)
    client = get_client()
    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}

    existing_keys = event_keys(fetch_dated_event_rows(client))
    seen: Set[EventKey] = set()
    fresh: List[Dict] = []
    for event in events:
        key = (event["person_id"], event["year"], event["month"], event["day"])
        if key in existing_keys or key in seen:
            continue
        seen.add(key)
        fresh.append(event)

    inserted = 0
    for start in range(0, len(fresh), batch_size):
        batch = fresh[start : start + batch_size]
        inserted_rows = client.table("events").insert([to_event_row(event) for event in batch]).execute().data
        tag_rows = []
        for inserted_row, event in zip(inserted_rows, batch):
            tag_rows.extend(build_tag_rows(inserted_row["id"], event["tags"], tag_name_to_id))
        if tag_rows:
            client.table("event_tags").insert(tag_rows).execute()
        inserted += len(batch)
    return inserted


if __name__ == "__main__":  # pragma: no cover - manual helper
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        print(prepare_music_events())
    elif command == "insert":
        print(insert_music_events())
    else:
        sys.exit("usage: python -m ingest.migrate_music_to_supabase prepare|insert")
