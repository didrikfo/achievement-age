"""Stage 0/1 of matching expansion: find every known person named in an event,
using one Aho-Corasick pass over a widened births pool.

Supersedes ingest.pipeline.match_births_to_events, which gated on the top 1,000
Pantheon-ranked people (1,232 matched events) and silently kept whichever name
its dict iteration reached first when a text named several known people. Here
the multi-name case becomes an explicit "ambiguous" status routed to Stage 2
instead of a guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from ingest.enrichment import load_births_lookup, write_review_entries
from ingest.name_index import build_name_index, find_names_in_text
from ingest.pipeline import calculate_age

MAX_AGE_DAYS = 120 * 365

WIDENED_BIRTHS_PATH = DATA_DIR / "historical_births_cleaned.json"
MATCHING_REVIEW_PATH = DATA_DIR / "tmp" / "matching_review.json"


def load_widened_births_lookup(path: Path = WIDENED_BIRTHS_PATH) -> Dict[str, Dict]:
    """Every scraped birth record keyed by normalize_name, minus single-token names.

    Drops the top-1,000 Pantheon fame gate. Single-token names stay excluded so
    "John" or "Cicero" alone can never match an event text.
    """
    lookup = load_births_lookup(path)
    return {key: value for key, value in lookup.items() if len(key.split()) > 1}


def classify_event(event: Dict, automaton, births_lookup: Dict[str, Dict]) -> Tuple[str, object]:
    """Classify one event against the known-person index.

    Returns (status, payload):
    - ("matched", event_record)   - exactly one known person, plausible age.
                                    event_record adds "name" and "age" to event.
    - ("ambiguous", names)        - several known people named; needs Stage 2.
    - ("unmatched", None)         - no known person named; needs Stage 2.
    - ("implausible", name)       - one person, but the age fails the bound.
    - ("unusable", None)          - the event has no numeric year.
    """
    year = event.get("year")
    if year is None or not str(year).isdigit():
        return "unusable", None

    names = find_names_in_text(automaton, event["text"])
    if not names:
        return "unmatched", None
    if len(names) > 1:
        return "ambiguous", names

    birth = births_lookup.get(names[0])
    if birth is None:
        return "unmatched", None

    age = calculate_age(
        birth["year"], birth["month"], birth["day"],
        int(year), int(event["month"]), int(event["day"]),
    )
    if age is None or not (0 <= age <= MAX_AGE_DAYS):
        return "implausible", birth["name"]

    return "matched", {**event, "name": birth["name"], "age": age}


EVENTS_WITH_AGE_PATH = DATA_DIR / "events_with_age.json"
SUBJECT_PENDING_PATH = DATA_DIR / "tmp" / "subject_pending.json"


def _event_key(event: Dict):
    """Natural key for a matched event - same (name, text) key llm_utils uses."""
    return (event.get("name"), event.get("text"))


def append_matched_events(new_events: List[Dict], path: Path = EVENTS_WITH_AGE_PATH) -> int:
    """Append matched events to path, skipping any whose (name, text) is already there.

    Returns how many were actually added. Safe to call repeatedly - Stage 1, 2
    and 3 all append to the same file across separate runs.
    """
    try:
        existing = load_json(path)
    except FileNotFoundError:
        existing = []

    seen = {_event_key(event) for event in existing}
    added = 0
    for event in new_events:
        if _event_key(event) in seen:
            continue
        existing.append(event)
        seen.add(_event_key(event))
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(path, existing)
    return added


def run_stage_one(
    events_path: Path = DATA_DIR / "historical_events.json",
    births_path: Path = WIDENED_BIRTHS_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    pending_path: Path = SUBJECT_PENDING_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> Dict[str, int]:
    """Classify every scraped event, appending matches and queueing the rest for Stage 2.

    Writes three files: matched events (appended), the Stage 2 queue
    (unmatched + ambiguous events), and review entries for implausible ages.
    """
    events = load_json(events_path)
    births_lookup = load_widened_births_lookup(births_path)
    automaton = build_name_index(births_lookup.keys())

    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0, "implausible": 0, "unusable": 0}
    matched: List[Dict] = []
    pending: List[Dict] = []
    review: List[Dict] = []

    for event in events:
        status, payload = classify_event(event, automaton, births_lookup)
        counts[status] += 1

        if status == "matched":
            matched.append(payload)
        elif status in ("unmatched", "ambiguous"):
            entry = {**event, "reason": status}
            if status == "ambiguous":
                entry["candidates"] = payload
            pending.append(entry)
        elif status == "implausible":
            review.append(
                {
                    "stage": "stage_1",
                    "issue_type": "implausible_age",
                    "name": payload,
                    "text": event["text"],
                    "detail": f"age for {payload!r} outside 0..{MAX_AGE_DAYS} days",
                }
            )

    counts["appended"] = append_matched_events(matched, matched_path)

    pending_path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(pending_path, pending)
    write_review_entries(review, review_path)

    return counts


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run_stage_one())
