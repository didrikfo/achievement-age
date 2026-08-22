"""Shared LLM-event-enrichment logic: tag taxonomy, prompt building, and
validation of what a reword subagent returns (tags, subject corrections).

Used by both ingest.llm_utils (new events, local JSON) and
ingest.backfill_event_enrichment (existing Supabase events), so the two
entry points can never disagree about what a valid tag or subject
correction looks like.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR, TAG_TAXONOMY
from core.io import load_json, save_to_json
from core.matching import name_matches_text, normalize_name
from ingest.pipeline import calculate_age

PROMPT_TEMPLATE_PATH = Path(__file__).parent / "reword_prompt.md"

#: Bumped by hand whenever reword_prompt.md changes in a way that could change
#: results. Rows in Supabase carry the version they were written under
#: (events.reword_prompt_version, default 0 for everything predating this), so a
#: prompt revision makes the affected rows re-queueable instead of one-shot.
REWORD_PROMPT_VERSION = 2


def build_prompt() -> str:
    """Read reword_prompt.md and substitute the {tags} placeholder with TAG_TAXONOMY."""
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("{tags}", ", ".join(TAG_TAXONOMY))


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


PHRASE_HINGE = " was when "

_PARENTHESISED = re.compile(r"\([^)]*\)")
_PROPER_NOUN = re.compile(r"\b[A-Z][^\W\d_]*\b")
_NUMERAL = re.compile(r"\b\d[\d,]*\b")


def check_phrase_format(event_phrase: str, name: str) -> Optional[str]:
    """Structural check on an event_phrase. Advisory - never blocks a write.

    Returns a rejection reason, or None when the phrase is well formed:
    contains PHRASE_HINGE, names `name` before it (so a title prefix passes
    but a substituted person doesn't), and ends with terminal punctuation.
    There's no fixed opening to check for - the phrase starts directly with
    the person, whatever their name or title.
    """
    phrase = (event_phrase or "").strip()
    if not phrase:
        return "phrase is empty"

    lowered = phrase.lower()
    hinge_at = lowered.find(PHRASE_HINGE.lower())
    if hinge_at == -1:
        return f"phrase does not contain {PHRASE_HINGE.strip()!r}"

    subject_span = phrase[:hinge_at]
    if normalize_name(name) not in normalize_name(subject_span):
        return f"opening names {subject_span!r}, expected it to contain {name!r}"

    if phrase[-1] not in ".!?":
        return "phrase does not end with terminal punctuation"

    return None


def check_facts_preserved(text: str, event_phrase: str) -> List[str]:
    """Tokens present in `text` but missing from `event_phrase`. Advisory heuristic.

    Deliberately over-sensitive - it flags roughly one record in six, and its
    output is a triage queue rather than a defect count. It exists because the
    two commonest ways a reword loses content are dropping a Wikipedia topic
    prefix ("Cuban Revolution:") and dropping a title ("Sir", "General"), both
    of which show up cleanly as a proper noun that vanished.

    Unlike an earlier sketch, the subject's own name is NOT exempt: under the
    full-sentence format the name and any title belong inside the phrase, so
    including them is what catches the dropped-title case.
    """
    source = _PARENTHESISED.sub(" ", text or "")
    phrase = event_phrase or ""
    phrase_lower = phrase.lower()

    words = source.split()
    first_word = words[0].strip(",.:;") if words else ""

    missing: List[str] = []
    seen = set()
    for token in _PROPER_NOUN.findall(source):
        if token == first_word or token in seen:
            continue
        if token.lower() not in phrase_lower:
            missing.append(token)
            seen.add(token)
    for token in _NUMERAL.findall(source):
        if token in seen:
            continue
        if token not in phrase:
            missing.append(token)
            seen.add(token)
    return missing


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


def write_review_entries(entries: List[Dict], review_path: Path) -> None:
    """Append entries to the enrichment review report, creating it if missing."""
    if not entries:
        return
    try:
        existing = load_json(review_path)
    except FileNotFoundError:
        existing = []
    existing.extend(entries)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(review_path, existing)


def build_tag_rows(event_id: int, tags: List[str], tag_name_to_id: Dict[str, int]) -> List[Dict]:
    """Build event_tags insert rows for one event's validated tag names."""
    return [{"event_id": event_id, "tag_id": tag_name_to_id[tag]} for tag in tags if tag in tag_name_to_id]
