"""LLM helpers used during data preparation.

Rewording (adding event_phrase, tags, and subject corrections to matched events)
is done by spawning Claude Haiku subagents from within a Claude Code session -
see prepare_reword_chunks and merge_reworded_chunk. This module has no
network/API-calling code itself; it only prepares chunk files for a subagent
to process (using ingest.enrichment.build_prompt for instructions) and merges
the result back in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from ingest.enrichment import (
    REWORD_PROMPT_VERSION,
    check_facts_preserved,
    check_phrase_format,
    load_births_lookup,
    resolve_subject,
    validate_tags,
    write_review_entries,
)

CHUNK_SIZE = 100
CHUNK_DIR = DATA_DIR / "tmp" / "reword_chunks"
REVIEW_PATH = DATA_DIR / "tmp" / "enrichment_review.json"

#: Field stamped on a merged record whose `name` was changed by a reword-time
#: subject correction, holding the name it had in events_with_age.json. Needed
#: because _event_key includes `name` - see get_pending_events.
ORIGINAL_NAME_FIELD = "original_name"


def _event_key(event: Dict) -> Tuple[object, object]:
    """Natural key for an event record: (name, text).

    A single text can legitimately carry several records - one per co-subject
    named in it (see ingest.match_events.classify_event) - so `text` alone does
    not identify a record. ingest.match_events._event_key and
    ingest.migrate_to_supabase.filter_new_entries key on (name, text) too, so
    all three agree about what counts as the same record.
    """
    return (event.get("name"), event.get("text"))


def _fallback_event_phrase(event: Dict) -> str:
    """Deterministic name-onward phrase used when a subagent can't produce usable output.

    Mirrors exactly what core.matching.full_sentence's normalizer reconstructs
    for the oldest suffix-only rows: the plain name (never a title - we have no
    source for one without the LLM) plus the original text, lowercased at the
    join. Degraded but well-formed, and already in the current shape - no
    tensed opening, since that's computed at display time, not stored.

    Records built this way are deliberately NOT stamped with
    REWORD_PROMPT_VERSION by the caller, so the phrasing backfill re-queues
    them later.
    """
    text = event.get("text", "") or ""
    lowered = text[:1].lower() + text[1:] if text else text
    return f"{event.get('name', '')} was when {lowered}"


def get_pending_events(
    events_path=DATA_DIR / "events_with_age.json",
    displayable_path=DATA_DIR / "displayable_events.json",
) -> Tuple[List[Dict], List[Dict]]:
    """Return (already_processed, pending) events, split by whether they're displayable yet.

    Keyed on (name, text), so a text already displayable under one name is
    still pending under a *different* one - a text can name several
    co-subjects and each needs its own event_phrase, and a name corrected in
    events_with_age.json (see match_events.append_matched_events's truncation
    rule) needs re-rewording under the corrected name.

    A record whose name was changed by a reword-time subject correction also
    carries ORIGINAL_NAME_FIELD; that pre-correction key counts as processed
    too, so the untouched source record in events_with_age.json isn't
    re-queued on every run.
    """
    all_events = load_json(events_path)
    try:
        processed = load_json(displayable_path)
    except FileNotFoundError:
        processed = []

    processed_keys = set()
    for event in processed:
        processed_keys.add(_event_key(event))
        original_name = event.get(ORIGINAL_NAME_FIELD)
        if original_name:
            processed_keys.add((original_name, event.get("text")))

    pending = [event for event in all_events if _event_key(event) not in processed_keys]
    return processed, pending


def prepare_reword_chunks(chunk_size: int = CHUNK_SIZE, max_events: int | None = None) -> List[Path]:
    """Split pending events into numbered chunk files for a subagent to process.

    Returns the chunk file paths. Each is a JSON array of event records still
    missing event_phrase. Dispatch instructions for the subagent should come
    from ingest.enrichment.build_prompt(), not be crafted ad hoc.
    """
    _, pending = get_pending_events()
    if max_events is not None:
        pending = pending[:max_events]

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        chunk = pending[start : start + chunk_size]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths


def merge_reworded_chunk(
    chunk_path,
    result_path,
    displayable_path=DATA_DIR / "displayable_events.json",
    births_path=DATA_DIR / "top_1000_births.json",
    review_path=REVIEW_PATH,
) -> int:
    """Merge a subagent's reworded chunk into displayable_path (default data/displayable_events.json).

    Records are matched back to the original chunk by (name, text), not by
    list order/position, so a subagent that drops or reorders a record is
    still handled correctly - and two co-subject records sharing a text stay
    distinct instead of collapsing onto one another's event_phrase. Both the
    chunk and the subagent's response carry `name` per record (reword_prompt.md
    asks for it back unchanged). Any record that doesn't come back with a usable
    event_phrase (missing result file, invalid JSON, or a blank field) gets
    the deterministic fallback template instead, with no tags and no subject
    correction attempted.

    Records that do come back get their tags validated against
    ingest.enrichment.TAG_TAXONOMY and any suggested_subject validated against
    the known births list (ingest.enrichment.resolve_subject) - anything that
    fails either check is recorded in review_path instead of applied. Returns
    how many records were merged.
    """
    chunk = load_json(chunk_path)

    reworded_by_key: Dict[object, Dict] = {}
    try:
        reworded = load_json(result_path)
        reworded_by_key = {_event_key(event): event for event in reworded}
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    births_lookup = load_births_lookup(births_path)
    review_entries: List[Dict] = []
    merged: List[Dict] = []

    for event in chunk:
        result = reworded_by_key.get(_event_key(event))
        if not result or not result.get("event_phrase"):
            # Deliberately unstamped: a fallback record isn't subagent output, so
            # the phrasing backfill should re-queue it later.
            merged.append({**event, "event_phrase": _fallback_event_phrase(event), "tags": []})
            continue

        merged_event = {
            **event,
            "event_phrase": result["event_phrase"],
            "reword_prompt_version": REWORD_PROMPT_VERSION,
        }

        tags, tag_reason = validate_tags(result.get("tags") or [])
        merged_event["tags"] = tags
        if tag_reason:
            review_entries.append(
                {"name": event.get("name"), "text": event.get("text"), "issue_type": "tags", "detail": tag_reason}
            )

        correction, subject_reason = resolve_subject(event, result.get("suggested_subject"), births_lookup)
        if correction:
            if correction["name"] != event.get("name"):
                merged_event[ORIGINAL_NAME_FIELD] = event.get("name")
            merged_event["name"] = correction["name"]
            merged_event["age"] = correction["age_days"]
        if subject_reason:
            review_entries.append(
                {"name": event.get("name"), "text": event.get("text"), "issue_type": "subject", "detail": subject_reason}
            )

        # Format-checked against the post-correction name (merged_event["name"]),
        # not the pre-correction one. The reword subagent always writes
        # event_phrase about the original `name` it was given, never a
        # substituted subject, so when a correction is applied the stored
        # phrase still names the original person while merged_event["name"]
        # now holds the corrected one. Checking against the post-correction
        # name is expected to flag a mismatch in that case - a real signal,
        # since the notification title/body built from event["name"] would
        # disagree with the phrase's subject too.
        format_reason = check_phrase_format(merged_event["event_phrase"], merged_event.get("name") or "")
        if format_reason:
            review_entries.append(
                {"name": merged_event.get("name"), "text": event.get("text"), "issue_type": "format", "detail": format_reason}
            )

        missing = check_facts_preserved(event.get("text", ""), merged_event["event_phrase"])
        if missing:
            review_entries.append(
                {
                    "name": merged_event.get("name"),
                    "text": event.get("text"),
                    "issue_type": "facts",
                    "detail": f"missing from phrase: {', '.join(missing)}",
                }
            )

        merged.append(merged_event)

    write_review_entries(review_entries, review_path)

    try:
        existing = load_json(displayable_path)
    except FileNotFoundError:
        existing = []
    existing.extend(merged)
    save_to_json(displayable_path, existing)

    return len(merged)
