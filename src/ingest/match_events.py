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
from typing import Dict, Tuple

from core.config import DATA_DIR
from ingest.enrichment import load_births_lookup
from ingest.name_index import find_names_in_text
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
