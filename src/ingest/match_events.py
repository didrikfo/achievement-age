"""Stage 0/1 of matching expansion: find every known person named in an event,
using one Aho-Corasick pass over a widened births pool.

Supersedes ingest.pipeline.match_births_to_events, which gated on the top 1,000
Pantheon-ranked people (1,232 matched events) and silently kept whichever name
its dict iteration reached first when a text named several known people. Here
the multi-name case becomes an explicit "ambiguous" status routed to Stage 2
instead of a guess.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from core.matching import normalize_name
from ingest.enrichment import load_births_lookup, write_review_entries
from ingest.name_index import build_name_index, find_names_in_text
from ingest.pipeline import calculate_age

MAX_AGE_DAYS = 120 * 365

WIDENED_BIRTHS_PATH = DATA_DIR / "historical_births_cleaned.json"
MATCHING_REVIEW_PATH = DATA_DIR / "tmp" / "matching_review.json"


def _colliding_names(path: Path) -> Set[str]:
    """Normalized names whose source records disagree about the birth date.

    load_births_lookup keys on normalize_name and so keeps only the last record
    for a repeated name. In the widened pool hundreds of distinct people share a
    normalized name ("Winston Churchill" is three different people), and picking
    the file's last one is a silent wrong answer. Reading the raw file is the
    only way to see the losers the lookup dict discarded.
    """
    dates_by_name: Dict[str, Set[Tuple[int, int, int]]] = {}
    for birth in load_json(path):
        try:
            key = normalize_name(birth["name"])
            date = (int(birth["year"]), int(birth["month"]), int(birth["day"]))
        except (KeyError, ValueError, TypeError):
            continue
        dates_by_name.setdefault(key, set()).add(date)
    return {name for name, dates in dates_by_name.items() if len(dates) > 1}


def load_widened_births_lookup(path: Path = WIDENED_BIRTHS_PATH) -> Dict[str, Dict]:
    """Every scraped birth record keyed by normalize_name, minus the unusable ones.

    Drops the top-1,000 Pantheon fame gate. Two categories stay excluded:
    single-token names, so "John" or "Cicero" alone can never match an event
    text; and names several different people share (see _colliding_names), which
    are unresolvable here - those events fall through to Stage 2/3 instead of
    being matched to whichever record happened to win.
    """
    lookup = load_births_lookup(path)
    colliding = _colliding_names(path)
    return {
        key: value
        for key, value in lookup.items()
        if len(key.split()) > 1 and key not in colliding
    }


def _entry_signature(entry: Dict) -> str:
    """Stable full-content key for a review/queue entry, for dedup on rerun."""
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)


def dedup_against_file(path: Path, entries: List[Dict]) -> List[Dict]:
    """The entries in `entries` not already stored in the JSON array at `path`.

    Every stage appends to shared files (the review report, the Stage 3 queue)
    and every stage is meant to be rerunnable, but the underlying append helpers
    write unconditionally. Filtering here - by exact entry content, plus within
    the batch itself - makes a second identical run a no-op. A missing or
    corrupt target file is treated as empty.
    """
    try:
        existing = load_json(path)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []

    seen = {_entry_signature(entry) for entry in existing}
    fresh: List[Dict] = []
    for entry in entries:
        signature = _entry_signature(entry)
        if signature in seen:
            continue
        seen.add(signature)
        fresh.append(entry)
    return fresh


COMMEMORATIVE_PATTERNS = (
    "named after", "in honor of", "in honour of", "anniversary of",
    "in memory of", "dedicated to", "commemorat",
)


def _mentions_commemorative_reference(text: str) -> bool:
    """True if text suggests a person is only referenced, not acting.

    A school "named after" someone, or text describing an "anniversary of"
    someone's death, does not mean that person did anything on this date -
    the age bound alone can't catch this within a normal lifespan window.
    Checked case-insensitively against the raw text (these are literal
    English phrases, not names, so normalize_name's punctuation-stripping
    isn't relevant here).
    """
    lowered = text.lower()
    return any(pattern in lowered for pattern in COMMEMORATIVE_PATTERNS)


def classify_event(event: Dict, automaton, births_lookup: Dict[str, Dict]) -> Tuple[str, object]:
    """Classify one event against the known-person index.

    Returns (status, payload):
    - ("matched", {"matched": [...], "implausible": [...]})
        "matched": one record per candidate whose computed age is plausible
        (event dict + name + age) - always non-empty when status is
        "matched". A single-candidate text yields a one-element list; a
        multi-candidate text (several distinct, non-nested real people
        literally named in the text) yields one per person.
        "implausible": names of candidates from the same text that
        individually failed the bound - possible even when others in the
        same text passed.
    - ("possible_reference", names)  - the text matched a commemorative/
        named-after phrase; never auto-accepted regardless of candidate
        count. names is whatever known candidates were found (may be []).
    - ("unmatched", None)            - no known person named, and no
        commemorative phrase either.
    - ("implausible", names)         - every candidate found failed the
        bound (none passed).
    - ("unusable", None)             - the event has no numeric year.
    """
    year = event.get("year")
    if year is None or not str(year).isdigit():
        return "unusable", None

    names = find_names_in_text(automaton, event["text"])

    if _mentions_commemorative_reference(event["text"]):
        return "possible_reference", names

    if not names:
        return "unmatched", None

    matched: List[Dict] = []
    implausible: List[str] = []
    for name in names:
        birth = births_lookup.get(name)
        if birth is None:
            continue
        age = calculate_age(
            birth["year"], birth["month"], birth["day"],
            int(year), int(event["month"]), int(event["day"]),
        )
        if age is None or not (0 <= age <= MAX_AGE_DAYS):
            implausible.append(birth["name"])
        else:
            matched.append({**event, "name": birth["name"], "age": age})

    if matched:
        return "matched", {"matched": matched, "implausible": implausible}
    if implausible:
        return "implausible", implausible
    return "unmatched", None


EVENTS_WITH_AGE_PATH = DATA_DIR / "events_with_age.json"
SUBJECT_PENDING_PATH = DATA_DIR / "tmp" / "subject_pending.json"


def _event_key(event: Dict):
    """Natural key for a matched event here: (name, text).

    Deliberately not the same key as elsewhere in the pipeline, and the
    difference matters. llm_utils._event_key and llm_utils.get_pending_events
    key on `text` alone, while migrate_to_supabase.filter_new_entries keys on
    (name, text) like this function. So a text stored twice under two different
    names is a duplicate to llm_utils but two distinct records here - which is
    why append_matched_events refuses to create that situation in the first
    place and sends the conflict to review instead.
    """
    return (event.get("name"), event.get("text"))


def append_matched_events(
    new_events: List[Dict],
    path: Path = EVENTS_WITH_AGE_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> int:
    """Append matched events to path, skipping any whose (name, text) is already there.

    An event whose `text` is already stored under a *different* name is not
    appended either: a rerun of a Stage 2 chunk can get a different subject back
    from the (nondeterministic) subagent, and storing both would show the same
    event to users twice, attributed to two people with two different ages.
    Those go to the review report instead, like every other case this pipeline
    cannot decide on its own.

    Returns how many were actually added. Safe to call repeatedly - Stage 1, 2
    and 3 all append to the same file across separate runs.
    """
    try:
        existing = load_json(path)
    except FileNotFoundError:
        existing = []

    seen = {_event_key(event) for event in existing}
    names_by_text: Dict[object, Set[object]] = {}
    for event in existing:
        names_by_text.setdefault(event.get("text"), set()).add(event.get("name"))

    added = 0
    conflicts: List[Dict] = []
    for event in new_events:
        if _event_key(event) in seen:
            continue

        text = event.get("text")
        stored_names = names_by_text.get(text)
        if stored_names:
            conflicts.append(
                {
                    "stage": "append",
                    "issue_type": "conflicting_subject",
                    "name": event.get("name"),
                    "text": text,
                    "detail": f"text already attributed to {sorted(map(str, stored_names))}",
                }
            )
            continue

        existing.append(event)
        seen.add(_event_key(event))
        names_by_text.setdefault(text, set()).add(event.get("name"))
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(path, existing)
    write_review_entries(dedup_against_file(review_path, conflicts), review_path)
    return added


def _implausible_review_entries(event: Dict, names: List[str]) -> List[Dict]:
    """One review entry per name whose computed age failed the plausibility bound."""
    return [
        {
            "stage": "stage_1",
            "issue_type": "implausible_age",
            "name": name,
            "text": event["text"],
            "detail": f"age for {name!r} outside 0..{MAX_AGE_DAYS} days",
        }
        for name in names
    ]


def run_stage_one(
    events_path: Path = DATA_DIR / "historical_events.json",
    births_path: Path = WIDENED_BIRTHS_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    pending_path: Path = SUBJECT_PENDING_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> Dict[str, int]:
    """Classify every scraped event, appending matches and queueing the rest for Stage 2.

    Writes three files: matched events (appended - possibly several per event
    when a text names multiple real people), the Stage 2 queue (unmatched +
    possible-reference events), and review entries for implausible ages.
    """
    events = load_json(events_path)
    births_lookup = load_widened_births_lookup(births_path)
    automaton = build_name_index(births_lookup.keys())

    counts = {"matched": 0, "possible_reference": 0, "unmatched": 0, "implausible": 0, "unusable": 0}
    matched: List[Dict] = []
    pending: List[Dict] = []
    review: List[Dict] = []

    for event in events:
        status, payload = classify_event(event, automaton, births_lookup)
        counts[status] += 1

        if status == "matched":
            matched.extend(payload["matched"])
            review.extend(_implausible_review_entries(event, payload["implausible"]))
        elif status == "possible_reference":
            entry = {**event, "reason": "possible_reference"}
            if payload:
                entry["candidates"] = payload
            pending.append(entry)
        elif status == "unmatched":
            pending.append({**event, "reason": "unmatched"})
        elif status == "implausible":
            review.extend(_implausible_review_entries(event, payload))

    counts["appended"] = append_matched_events(matched, matched_path, review_path)

    pending_path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(pending_path, pending)
    write_review_entries(dedup_against_file(review_path, review), review_path)

    return counts


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run_stage_one())
