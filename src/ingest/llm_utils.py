"""LLM helpers used during data preparation.

Rewording (adding event_phrase to matched events) is done by spawning Claude
Haiku subagents from within a Claude Code session - see prepare_reword_chunks
and merge_reworded_chunk. This module has no network/API-calling code itself;
it only prepares chunk files for a subagent to process and merges the result
back in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json

CHUNK_SIZE = 100
CHUNK_DIR = DATA_DIR / "tmp" / "reword_chunks"


def _event_key(event: Dict) -> Tuple[object, object]:
    """Natural key for an event: (name, text) - stable across pipeline reruns."""
    return (event.get("name"), event.get("text"))


def _fallback_event_phrase(event: Dict) -> str:
    """Deterministic event_phrase suffix used when a subagent can't produce usable output.

    Returns only the fragment that goes after "The same age that {name} was when " —
    that static prefix is built at display time by core.matching.full_sentence, not stored.
    """
    text = event.get("text", "") or ""
    return text[:1].lower() + text[1:] if text else text


def get_pending_events(
    events_path=DATA_DIR / "events_with_age.json",
    displayable_path=DATA_DIR / "displayable_events.json",
) -> Tuple[List[Dict], List[Dict]]:
    """Return (already_processed, pending) events, split by whether event_phrase exists."""
    all_events = load_json(events_path)
    try:
        processed = load_json(displayable_path)
    except FileNotFoundError:
        processed = []

    processed_keys = {_event_key(event) for event in processed}
    pending = [event for event in all_events if _event_key(event) not in processed_keys]
    return processed, pending


def prepare_reword_chunks(chunk_size: int = CHUNK_SIZE, max_events: int | None = None) -> List[Path]:
    """Split pending events into numbered chunk files for a subagent to process.

    Returns the chunk file paths. Each is a JSON array of event records still
    missing event_phrase.
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


def merge_reworded_chunk(chunk_path, result_path, displayable_path=DATA_DIR / "displayable_events.json") -> int:
    """Merge a subagent's reworded chunk into displayable_path (default data/displayable_events.json).

    Records are matched back to the original chunk by (name, text), not by
    list order/position, so a subagent that drops or reorders a record is
    still handled correctly. Any record that doesn't come back with a usable
    event_phrase (missing result file, invalid JSON, or a blank field) gets
    the deterministic fallback template instead. Returns how many records
    were merged.
    """
    chunk = load_json(chunk_path)

    reworded_by_key: Dict[Tuple[object, object], Dict] = {}
    try:
        reworded = load_json(result_path)
        reworded_by_key = {_event_key(event): event for event in reworded}
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    merged: List[Dict] = []
    for event in chunk:
        result = reworded_by_key.get(_event_key(event))
        if result and result.get("event_phrase"):
            merged.append(result)
        else:
            merged.append({**event, "event_phrase": _fallback_event_phrase(event)})

    try:
        existing = load_json(displayable_path)
    except FileNotFoundError:
        existing = []
    existing.extend(merged)
    save_to_json(displayable_path, existing)

    return len(merged)
