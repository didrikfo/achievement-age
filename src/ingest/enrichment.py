"""Shared LLM-event-enrichment logic: tag taxonomy, prompt building, and
validation of what a reword subagent returns (tags, subject corrections).

Used by both ingest.llm_utils (new events, local JSON) and
ingest.backfill_event_enrichment (existing Supabase events), so the two
entry points can never disagree about what a valid tag or subject
correction looks like.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.io import load_json
from core.matching import name_matches_text, normalize_name
from ingest.pipeline import calculate_age

TAG_TAXONOMY = [
    "military", "politics", "science", "technology", "exploration", "space", "arts", "music",
    "film", "sports", "religion", "royalty", "economics", "law", "disaster", "health", "social",
    "education", "philosophy", "engineering",
]


def validate_tags(raw_tags: List[str]) -> Tuple[List[str], Optional[str]]:
    """Filter LLM-returned tags against TAG_TAXONOMY.

    Case-insensitive, de-duplicated, capped at 3, keeping the first 3 valid
    tags in the order given. Returns (valid_tags, rejection_reason) - reason
    is None unless no tag in raw_tags survived filtering.
    """
    valid: List[str] = []
    seen = set()
    for tag in raw_tags or []:
        normalized = str(tag).strip().lower()
        if normalized in TAG_TAXONOMY and normalized not in seen:
            valid.append(normalized)
            seen.add(normalized)
        if len(valid) == 3:
            break

    if not valid:
        return [], f"no valid tags in {raw_tags!r}"
    return valid, None


def load_births_lookup(path: Path = DATA_DIR / "top_1000_births.json") -> Dict[str, Dict]:
    """Load births data, indexed by normalize_name(name) for subject-correction lookups."""
    lookup: Dict[str, Dict] = {}
    for birth in load_json(path):
        try:
            name = birth["name"]
            lookup[normalize_name(name)] = {
                "name": name,
                "year": int(birth["year"]),
                "month": int(birth["month"]),
                "day": int(birth["day"]),
            }
        except (KeyError, ValueError, TypeError):
            continue
    return lookup


def resolve_subject(
    event: Dict,
    suggested_name: Optional[str],
    births_lookup: Dict[str, Dict],
) -> Tuple[Optional[Dict], Optional[str]]:
    """Validate an LLM-suggested subject correction for one event.

    `event` must have "text", "year", "month", "day" keys.

    Returns (correction, rejection_reason):
    - (None, None) if suggested_name is falsy (no correction requested).
    - (None, reason) if a correction was suggested but failed validation.
    - (correction, None) if valid, where correction is {"name": ..., "age_days": ...}.
    """
    if not suggested_name:
        return None, None

    if not name_matches_text(suggested_name, event["text"]):
        return None, f"suggested subject {suggested_name!r} not found in event text"

    birth = births_lookup.get(normalize_name(suggested_name))
    if birth is None:
        return None, f"suggested subject {suggested_name!r} not in known births list"

    age_days = calculate_age(
        birth["year"], birth["month"], birth["day"],
        int(event["year"]), int(event["month"]), int(event["day"]),
    )
    if age_days is None or not (0 <= age_days <= 120 * 365):
        return None, f"could not compute a valid age for {suggested_name!r}"

    return {"name": birth["name"], "age_days": age_days}, None
