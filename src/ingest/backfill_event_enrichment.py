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
from ingest.enrichment import (
    REWORD_PROMPT_VERSION,
    build_tag_rows,
    check_facts_preserved,
    check_phrase_format,
    load_births_lookup,
    resolve_subject,
    validate_tags,
    write_review_entries,
)

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
            .select("id, name, text, year, month, day, reword_prompt_version")
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
    """Every event_id that already has at least one row in event_tags.

    Paginated the same way as _fetch_all_events: event_tags has 1-3 rows per
    event, so an unpaginated select can silently truncate at Supabase's ~1000
    row cap and make already-tagged events look pending again on a rerun.
    """
    event_ids: Set[int] = set()
    start = 0
    while True:
        page = (
            client.table("event_tags")
            .select("event_id")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        event_ids.update(row["event_id"] for row in page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return event_ids


def pending_events(all_events: List[Dict], tagged_event_ids: Set[int]) -> List[Dict]:
    """Events with no event_tags rows yet - safe to call repeatedly (resumable backfill)."""
    return [event for event in all_events if event["id"] not in tagged_event_ids]


def pending_phrasing_events(all_events: List[Dict], version: int) -> List[Dict]:
    """Events not yet written under the current reword prompt.

    A missing key counts as 0 (the column default), so rows predating the
    column are always pending.
    """
    return [event for event in all_events if (event.get("reword_prompt_version") or 0) < version]


def prepare_chunks(chunk_size: int = CHUNK_SIZE, mode: str = "tags") -> List[Path]:
    """Fetch pending events from Supabase and split them into numbered chunk files.

    mode="tags" (default) selects events with no event_tags rows yet - the
    original enrichment backfill. mode="phrasing" selects events not yet written
    under the current reword prompt, for a re-phrasing pass over rows that
    already have tags.
    """
    if mode not in ("tags", "phrasing"):
        raise ValueError(f"unknown mode {mode!r}, expected 'tags' or 'phrasing'")

    client = get_client()
    all_events = _fetch_all_events(client)
    if mode == "phrasing":
        pending = pending_phrasing_events(all_events, REWORD_PROMPT_VERSION)
    else:
        pending = pending_events(all_events, _fetch_tagged_event_ids(client))

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        chunk = pending[start : start + chunk_size]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths


def _phrase_review_entries(event: Dict, event_phrase: str, name: str) -> List[Dict]:
    """Advisory format/fact review entries for one phrase. Never blocks the write.

    `name` is the event's name as given - its only caller is the mode="phrasing"
    branch of resolve_event_update, which never applies a subject correction.
    """
    entries: List[Dict] = []

    format_reason = check_phrase_format(event_phrase, name)
    if format_reason:
        entries.append({"event_id": event["id"], "issue_type": "format", "detail": format_reason})

    missing = check_facts_preserved(event.get("text", ""), event_phrase)
    if missing:
        entries.append(
            {
                "event_id": event["id"],
                "issue_type": "facts",
                "detail": f"missing from phrase: {', '.join(missing)}",
            }
        )
    return entries


def resolve_event_update(
    event: Dict,
    result: Optional[Dict],
    births_lookup: Dict[str, Dict],
    mode: str = "tags",
) -> Tuple[Optional[Dict], List[str], List[Dict]]:
    """Pure decision logic for one event: what to write, and what to flag for review.

    Returns (events_update_or_none, valid_tags, review_entries). events_update_or_none
    is None when no usable event_phrase came back (nothing to write for this event).

    mode="phrasing" writes only event_phrase and reword_prompt_version. It assigns
    no tags (these rows already have them) and does not apply subject corrections -
    doing so would pull age recomputation and persons upserts into what should be a
    single-purpose, easily-reversible pass. Suggested subjects are still recorded
    for review so the errors surface for a separate decision.
    """
    review_entries: List[Dict] = []

    if not result or not result.get("event_phrase"):
        review_entries.append(
            {"event_id": event["id"], "issue_type": "reword", "detail": "no usable event_phrase returned"}
        )
        return None, [], review_entries

    event_phrase = result["event_phrase"]

    if mode == "phrasing":
        update: Dict = {"event_phrase": event_phrase, "reword_prompt_version": REWORD_PROMPT_VERSION}
        suggested = result.get("suggested_subject")
        if suggested:
            review_entries.append(
                {
                    "event_id": event["id"],
                    "issue_type": "subject",
                    "detail": f"suggested subject {suggested!r} recorded but not applied (phrasing pass)",
                }
            )
        review_entries.extend(_phrase_review_entries(event, event_phrase, event.get("name") or ""))
        return update, [], review_entries

    tags, tag_reason = validate_tags(result.get("tags") or [])
    if tag_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "tags", "detail": tag_reason})

    correction, subject_reason = resolve_subject(event, result.get("suggested_subject"), births_lookup)
    if subject_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "subject", "detail": subject_reason})

    update = {"event_phrase": event_phrase}
    if correction:
        update["name"] = correction["name"]
        update["age_days"] = correction["age_days"]

    return update, tags, review_entries


def _find_result(
    event: Dict,
    reworded_by_id: Dict[int, Dict],
    reworded_by_name_text: Dict[Tuple[object, object], Dict],
) -> Optional[Dict]:
    """Look up a chunk event's reworded result, by id first, falling back to (name, text).

    A subagent that doesn't echo `id` back (it's easy to miss in the prompt's output
    schema) would otherwise fail to match every event in the chunk.
    """
    result = reworded_by_id.get(event["id"])
    if result is not None:
        return result
    return reworded_by_name_text.get((event.get("name"), event.get("text")))


def merge_chunk(chunk_path, result_path, review_path: Path = REVIEW_PATH, mode: str = "tags") -> int:
    """Validate one chunk's subagent output and write event/event_tags updates to Supabase.

    Returns how many events in the chunk were processed (written or flagged for review).
    """
    chunk = load_json(chunk_path)
    try:
        reworded = load_json(result_path)
    except (FileNotFoundError, json.JSONDecodeError):
        reworded = []
    reworded_by_id = {event["id"]: event for event in reworded if "id" in event}
    reworded_by_name_text = {(event.get("name"), event.get("text")): event for event in reworded}

    client = get_client()
    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}
    births_lookup = load_births_lookup()

    all_review_entries: List[Dict] = []
    try:
        for event in chunk:
            result = _find_result(event, reworded_by_id, reworded_by_name_text)
            update, tags, review_entries = resolve_event_update(event, result, births_lookup, mode=mode)
            all_review_entries.extend(review_entries)

            if update is None:
                continue

            try:
                if "name" in update:
                    # A subject correction was accepted: make sure the corrected person
                    # has a persons row, and point the event at it so the UI's
                    # Wikipedia link (joined via person_id) matches the new name.
                    person_rows = (
                        client.table("persons")
                        .upsert({"name": update["name"]}, on_conflict="name")
                        .execute()
                        .data
                    )
                    update["person_id"] = person_rows[0]["id"]

                client.table("events").update(update).eq("id", event["id"]).execute()

                tag_rows = build_tag_rows(event["id"], tags, tag_name_to_id)
                if tag_rows:
                    client.table("event_tags").insert(tag_rows).execute()
            except Exception as exc:  # noqa: BLE001 - one event's failure shouldn't sink the whole chunk
                all_review_entries.append(
                    {"event_id": event["id"], "issue_type": "error", "detail": str(exc)}
                )
    finally:
        write_review_entries(all_review_entries, review_path)

    return len(chunk)


if __name__ == "__main__":  # pragma: no cover - manual, requires a subagent in between
    paths = prepare_chunks()
    print(f"Wrote {len(paths)} chunk file(s) to {CHUNK_DIR}.")
    print("Dispatch a Haiku subagent per chunk (prompt: ingest.enrichment.build_prompt()),")
    print("write each result to <chunk>_result.json, then call merge_chunk() per chunk.")
