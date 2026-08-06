"""Shared LLM-event-enrichment logic: tag taxonomy, prompt building, and
validation of what a reword subagent returns (tags, subject corrections).

Used by both ingest.llm_utils (new events, local JSON) and
ingest.backfill_event_enrichment (existing Supabase events), so the two
entry points can never disagree about what a valid tag or subject
correction looks like.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

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
