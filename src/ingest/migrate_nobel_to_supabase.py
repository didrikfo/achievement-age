"""Person resolution, the duplicate-day guard, and the final Supabase write for
Nobel Prize laureate records. Two phases in one module, mirroring
ingest.migrate_to_supabase's shape:

    python -c "from ingest.migrate_nobel_to_supabase import prepare_pending_records; \
        from ingest.sources.nobel import load_nobel_records; \
        prepare_pending_records(load_nobel_records())"
    # ... resolve_nobel_wikidata, then the LLM phrasing pass populate
    # data/nobel_displayable.json in between ...
    python -c "from ingest.migrate_nobel_to_supabase import insert_nobel_events; \
        from core.io import load_json; from ingest.nobel_llm_utils import DISPLAYABLE_PATH; \
        insert_nobel_events(load_json(DISPLAYABLE_PATH))"

See docs/superpowers/specs/2026-08-28-nobel-prize-ingestion-design.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.db import get_client
from core.io import save_to_json
from core.matching import normalize_name
from ingest.enrichment import build_tag_rows, write_review_entries
from ingest.match_events import MAX_AGE_DAYS
from ingest.pipeline import calculate_age
from ingest.sources.nobel import NOBEL_SOURCE, build_event_text

EVENTS_PAGE_SIZE = 1000

NOBEL_PENDING_PATH = DATA_DIR / "tmp" / "nobel_pending.json"
NOBEL_PERSON_REVIEW_PATH = DATA_DIR / "tmp" / "nobel_person_review.json"
NOBEL_DUPLICATE_REVIEW_PATH = DATA_DIR / "tmp" / "nobel_duplicate_review.json"
NOBEL_AGE_REVIEW_PATH = DATA_DIR / "tmp" / "nobel_age_review.json"


# --- pure functions ---


def build_person_rows(records: List[Dict]) -> List[Dict]:
    """One persons row per distinct name, sorted for a deterministic insert order."""
    return [{"name": name} for name in sorted({record["name"] for record in records})]


def find_normalized_name_collisions(records: List[Dict], existing_persons: List[Dict]) -> List[Dict]:
    """Records whose name normalizes the same as an existing person's but isn't an
    exact string match - e.g. Nobel's "J.J. Thomson" vs. the existing "J. J.
    Thomson". Exact matches are meant to reuse the existing row via upsert;
    these near-duplicates are flagged for a one-line manual fix rather than
    auto-merged or silently duplicated (detect-don't-guess, matching this
    codebase's stated position on same-name collisions).
    """
    existing_by_norm: Dict[str, List[str]] = {}
    for person in existing_persons:
        existing_by_norm.setdefault(normalize_name(person["name"]), []).append(person["name"])

    collisions: List[Dict] = []
    for record in records:
        norm = normalize_name(record["name"])
        matches = existing_by_norm.get(norm, [])
        if matches and record["name"] not in matches:
            collisions.append(
                {
                    "issue_type": "normalized_name_collision",
                    "name": record["name"],
                    "detail": f"normalizes the same as existing person(s) {matches!r}",
                }
            )
    return collisions


def wikipedia_url_updates(
    records: List[Dict],
    name_to_person_id: Dict[str, int],
    existing_wikipedia_by_id: Dict[int, Optional[str]],
) -> List[Tuple[int, str]]:
    """(person_id, url) pairs to write - only where the person currently has no
    wikipedia_url, matching backfill_person_wikipedia.py's rule of never
    overwriting an already-verified value.
    """
    updates: List[Tuple[int, str]] = []
    seen_ids = set()
    for record in records:
        url = record.get("wikipedia_url")
        person_id = name_to_person_id.get(record["name"])
        if not url or person_id is None or person_id in seen_ids:
            continue
        if not existing_wikipedia_by_id.get(person_id):
            updates.append((person_id, url))
            seen_ids.add(person_id)
    return updates


def find_duplicate_day_records(
    records: List[Dict],
    name_to_person_id: Dict[str, int],
    existing_event_keys: Dict[Tuple[int, int, int, int], Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """Split records into (keep, blocked): one event per person per day.

    A record whose person_id + exact award date already has an events row
    (typically scraped from Wikipedia's "on this day" corpus) is blocked. A
    record with no resolved person_id can't be checked and is always kept.
    """
    keep: List[Dict] = []
    blocked: List[Dict] = []
    for record in records:
        person_id = name_to_person_id.get(record["name"])
        key = (person_id, record["award_year"], record["award_month"], record["award_day"])
        if person_id is not None and key in existing_event_keys:
            blocked.append(record)
        else:
            keep.append(record)
    return keep, blocked


def build_pending_records(
    records: List[Dict], name_to_person_id: Dict[str, int]
) -> Tuple[List[Dict], List[Dict]]:
    """Compute age_days and person_id for each record.

    Returns (pending, implausible). pending records carry person_id and
    age_days, ready for the LLM phrasing pass. implausible records failed the
    0 <= age_days <= MAX_AGE_DAYS bound and are excluded - expected to be rare
    (CSV-supplied birth dates are already sane), but matters for the
    Wikidata-resolved records where a same-name mismatch is possible.
    """
    pending: List[Dict] = []
    implausible: List[Dict] = []
    for record in records:
        age_days = calculate_age(
            record["birth_year"], record["birth_month"], record["birth_day"],
            record["award_year"], record["award_month"], record["award_day"],
        )
        if age_days is None or not (0 <= age_days <= MAX_AGE_DAYS):
            implausible.append(record)
            continue
        pending.append({**record, "person_id": name_to_person_id.get(record["name"]), "age_days": age_days})
    return pending, implausible


def _to_event_row(record: Dict) -> Dict:
    """A phrased, pending Nobel record -> an events insert row."""
    return {
        "name": record["name"],
        "person_id": record["person_id"],
        "text": build_event_text(record),
        "event_phrase": record["event_phrase"],
        "reword_prompt_version": record.get("reword_prompt_version", 0),
        "year": record["award_year"],
        "month": record["award_month"],
        "day": record["award_day"],
        "age_days": record["age_days"],
        "event_type": "achievement",
        "source": NOBEL_SOURCE,
    }


# --- paginated Supabase reads ---


def fetch_all_persons(client) -> List[Dict]:
    """Every persons row (id, name, wikipedia_url), paginated past the ~1000 row cap."""
    persons: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("persons")
            .select("id, name, wikipedia_url")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        persons.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return persons


def fetch_existing_event_person_dates(client) -> Dict[Tuple[int, int, int, int], Dict]:
    """(person_id, year, month, day) -> {"id", "text"} for every events row with a
    person_id, paginated past the ~1000 row cap. Rows with no person_id are
    skipped - the duplicate-day guard has nothing to compare them against.
    """
    keys: Dict[Tuple[int, int, int, int], Dict] = {}
    start = 0
    while True:
        page = (
            client.table("events")
            .select("id, person_id, year, month, day, text")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        for row in page:
            if row["person_id"] is None:
                continue
            keys[(row["person_id"], row["year"], row["month"], row["day"])] = {
                "id": row["id"], "text": row["text"],
            }
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return keys


# --- orchestrators (impure) ---


def prepare_pending_records(
    records: List[Dict],
    output_path: Path = NOBEL_PENDING_PATH,
    person_review_path: Path = NOBEL_PERSON_REVIEW_PATH,
    duplicate_review_path: Path = NOBEL_DUPLICATE_REVIEW_PATH,
    age_review_path: Path = NOBEL_AGE_REVIEW_PATH,
) -> Dict[str, int]:
    """Phase 1: resolve person_id, guard duplicates, compute age, write survivors.

    The last point before any LLM cost is spent (Task 4's phrasing pass reads
    output_path), so a bad guard or upsert never wastes a phrasing run.
    """
    client = get_client()

    existing_persons = fetch_all_persons(client)
    collisions = find_normalized_name_collisions(records, existing_persons)
    write_review_entries(collisions, person_review_path)
    colliding_names = {collision["name"] for collision in collisions}
    records = [record for record in records if record["name"] not in colliding_names]

    person_rows = build_person_rows(records)
    upserted = client.table("persons").upsert(person_rows, on_conflict="name").execute().data
    name_to_person_id = {person["name"]: person["id"] for person in upserted}

    existing_wikipedia_by_id = {person["id"]: person["wikipedia_url"] for person in existing_persons}
    for person_id, url in wikipedia_url_updates(records, name_to_person_id, existing_wikipedia_by_id):
        client.table("persons").update({"wikipedia_url": url}).eq("id", person_id).execute()

    existing_event_keys = fetch_existing_event_person_dates(client)
    keep, blocked = find_duplicate_day_records(records, name_to_person_id, existing_event_keys)
    write_review_entries(
        [
            {
                "issue_type": "duplicate_day",
                "name": record["name"],
                "detail": (
                    f"event already exists on {record['award_year']}-{record['award_month']:02d}-{record['award_day']:02d} "
                    f"(existing event id={existing_event_keys[(name_to_person_id.get(record['name']), record['award_year'], record['award_month'], record['award_day'])]['id']})"
                ),
            }
            for record in blocked
        ],
        duplicate_review_path,
    )

    pending, implausible = build_pending_records(keep, name_to_person_id)
    write_review_entries(
        [
            {
                "issue_type": "implausible_age",
                "name": record["name"],
                "detail": f"no plausible age for award_year={record['award_year']}",
            }
            for record in implausible
        ],
        age_review_path,
    )

    save_to_json(output_path, pending)
    return {
        "input": len(records) + len(collisions),
        "name_collisions": len(collisions),
        "duplicate_day_blocked": len(blocked),
        "implausible_age": len(implausible),
        "pending": len(pending),
    }


def insert_nobel_events(records: List[Dict], batch_size: int = 200) -> int:
    """Phase 2: insert events/event_tags rows for phrased Nobel records.

    Assumes records already passed through prepare_pending_records (person_id
    set, age_days computed, duplicates guarded) and the LLM phrasing pass
    (event_phrase and tags set) - i.e. records loaded from
    ingest.nobel_llm_utils.DISPLAYABLE_PATH.
    """
    client = get_client()
    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}

    rows = [_to_event_row(record) for record in records]
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        batch_records = records[start : start + batch_size]
        inserted_events = client.table("events").insert(batch).execute().data

        tag_rows = []
        for inserted_event, record in zip(inserted_events, batch_records):
            tag_rows.extend(build_tag_rows(inserted_event["id"], record.get("tags") or [], tag_name_to_id))
        if tag_rows:
            client.table("event_tags").insert(tag_rows).execute()

        inserted += len(batch)
    return inserted
