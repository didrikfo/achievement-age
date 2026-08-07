"""One-off enrichment backfill: re-process every Supabase event through the shared
reword/tag/subject-check prompt (ingest.enrichment), for events that don't already
have tags.

Two-phase, like ingest.llm_utils, because a Claude Code subagent has to run in
between the two calls - see SUPABASE_SETUP.md section 4 for the full sequence:

    python -c "from ingest.backfill_event_enrichment import prepare_chunks; prepare_chunks()"
    # ... dispatch a Haiku subagent per chunk file, using ingest.enrichment.build_prompt() ...
    python -c "from ingest.backfill_event_enrichment import merge_chunk; merge_chunk('data/tmp/enrichment_chunks/chunk_0000.json', 'data/tmp/enrichment_chunks/chunk_0000_result.json')"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.config import DATA_DIR
from core.db import get_client
from core.io import load_json, save_to_json
from ingest.enrichment import build_tag_rows, load_births_lookup, resolve_subject, validate_tags, write_review_entries

CHUNK_SIZE = 100
CHUNK_DIR = DATA_DIR / "tmp" / "enrichment_chunks"
REVIEW_PATH = DATA_DIR / "tmp" / "enrichment_review.json"

EVENTS_PAGE_SIZE = 1000


def _fetch_all_events(client) -> List[Dict]:
    """Page through every event's id/name/text/year/month/day (Supabase caps a page at ~1000 rows)."""
    events: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("id, name, text, year, month, day")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        events.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return events


def _fetch_tagged_event_ids(client) -> Set[int]:
    """Every event_id that already has at least one row in event_tags."""
    response = client.table("event_tags").select("event_id").execute()
    return {row["event_id"] for row in response.data}


def pending_events(all_events: List[Dict], tagged_event_ids: Set[int]) -> List[Dict]:
    """Events with no event_tags rows yet - safe to call repeatedly (resumable backfill)."""
    return [event for event in all_events if event["id"] not in tagged_event_ids]


def prepare_chunks(chunk_size: int = CHUNK_SIZE) -> List[Path]:
    """Fetch pending events from Supabase and split them into numbered chunk files."""
    client = get_client()
    pending = pending_events(_fetch_all_events(client), _fetch_tagged_event_ids(client))

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        chunk = pending[start : start + chunk_size]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths


def resolve_event_update(
    event: Dict,
    result: Optional[Dict],
    births_lookup: Dict[str, Dict],
) -> Tuple[Optional[Dict], List[str], List[Dict]]:
    """Pure decision logic for one event: what to write, and what to flag for review.

    Returns (events_update_or_none, valid_tags, review_entries). events_update_or_none
    is None when no usable event_phrase came back (nothing to write for this event).
    """
    review_entries: List[Dict] = []

    if not result or not result.get("event_phrase"):
        review_entries.append(
            {"event_id": event["id"], "issue_type": "reword", "detail": "no usable event_phrase returned"}
        )
        return None, [], review_entries

    tags, tag_reason = validate_tags(result.get("tags") or [])
    if tag_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "tags", "detail": tag_reason})

    correction, subject_reason = resolve_subject(event, result.get("suggested_subject"), births_lookup)
    if subject_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "subject", "detail": subject_reason})

    update: Dict = {"event_phrase": result["event_phrase"]}
    if correction:
        update["name"] = correction["name"]
        update["age_days"] = correction["age_days"]

    return update, tags, review_entries


def merge_chunk(chunk_path, result_path, review_path: Path = REVIEW_PATH) -> int:
    """Validate one chunk's subagent output and write event/event_tags updates to Supabase.

    Returns how many events in the chunk were processed (written or flagged for review).
    """
    chunk = load_json(chunk_path)
    try:
        reworded = load_json(result_path)
    except (FileNotFoundError, json.JSONDecodeError):
        reworded = []
    reworded_by_id = {event["id"]: event for event in reworded if "id" in event}

    client = get_client()
    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}
    births_lookup = load_births_lookup()

    all_review_entries: List[Dict] = []
    for event in chunk:
        result = reworded_by_id.get(event["id"])
        update, tags, review_entries = resolve_event_update(event, result, births_lookup)
        all_review_entries.extend(review_entries)

        if update is None:
            continue

        client.table("events").update(update).eq("id", event["id"]).execute()

        tag_rows = build_tag_rows(event["id"], tags, tag_name_to_id)
        if tag_rows:
            client.table("event_tags").insert(tag_rows).execute()

    write_review_entries(all_review_entries, review_path)
    return len(chunk)


if __name__ == "__main__":  # pragma: no cover - manual, requires a subagent in between
    paths = prepare_chunks()
    print(f"Wrote {len(paths)} chunk file(s) to {CHUNK_DIR}.")
    print("Dispatch a Haiku subagent per chunk (prompt: ingest.enrichment.build_prompt()),")
    print("write each result to <chunk>_result.json, then call merge_chunk() per chunk.")
