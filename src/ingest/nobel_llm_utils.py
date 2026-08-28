"""LLM phrasing for Nobel Prize laureate records.

Nobel records are structured data with a known subject and no free-text
ambiguity, so this mirrors ingest.llm_utils's chunk/dispatch/merge shape but
skips what that module needs for messy scraped text: no suggested_subject
step, and tags come from ingest.sources.nobel.NOBEL_CATEGORY_TAGS rather than
being chosen by the LLM. See
docs/superpowers/specs/2026-08-28-nobel-prize-ingestion-design.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from ingest.enrichment import check_facts_preserved, check_phrase_format, write_review_entries
from ingest.sources.nobel import NOBEL_CATEGORY_TAGS, build_event_text, category_display_name

CHUNK_SIZE = 100
CHUNK_DIR = DATA_DIR / "tmp" / "nobel_reword_chunks"
DISPLAYABLE_PATH = DATA_DIR / "nobel_displayable.json"
REVIEW_PATH = DATA_DIR / "tmp" / "nobel_enrichment_review.json"
PROMPT_PATH = Path(__file__).parent / "nobel_reword_prompt.md"

#: Bumped by hand whenever nobel_reword_prompt.md changes in a way that could
#: change results. Shares the events.reword_prompt_version column with the
#: historical corpus's REWORD_PROMPT_VERSION - safe because
#: ingest.backfill_event_enrichment.pending_phrasing_events excludes
#: Nobel-sourced rows from that counter's selection entirely (Task 3).
NOBEL_REWORD_PROMPT_VERSION = 1


def build_nobel_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _record_key(record: Dict) -> Tuple[object, object]:
    """(laureate_id, award_year) - the unique key for one CSV row.

    laureate_id alone identifies the *person*, not the award: John Bardeen,
    Frederick Sanger, and K. Barry Sharpless each won the same category twice
    in different years, so laureate_id repeats across their rows. award_year
    disambiguates.
    """
    return (record.get("laureate_id"), record.get("award_year"))


def _fallback_event_phrase(record: Dict) -> str:
    """Deterministic phrase used when a subagent can't produce usable output.

    Deliberately not stamped with NOBEL_REWORD_PROMPT_VERSION by the caller,
    so a later rerun re-queues it - matches ingest.llm_utils's fallback
    convention exactly.
    """
    return f'{record["name"]} was when they won {category_display_name(record["category"])}.'


def prepare_nobel_chunks(records: List[Dict], chunk_size: int = CHUNK_SIZE) -> List[Path]:
    """Split pending Nobel records into numbered chunk files for a subagent to process.

    Each chunk record carries every field the input record had, plus
    category_display (computed here so the prompt never has to reconstruct
    the Economic Sciences special case itself).
    """
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(records), chunk_size)):
        chunk = [
            {**record, "category_display": category_display_name(record["category"])}
            for record in records[start : start + chunk_size]
        ]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths


def merge_nobel_chunk(
    chunk_path: Path,
    result_path: Path,
    displayable_path: Path = DISPLAYABLE_PATH,
    review_path: Path = REVIEW_PATH,
) -> int:
    """Merge one subagent's reworded chunk into displayable_path.

    Records are matched back to the chunk by (laureate_id, award_year), not
    list order, so a subagent that drops or reorders a record is still
    handled correctly. A record with no usable event_phrase gets the
    deterministic fallback rather than being dropped. Returns how many
    records were merged (always len(chunk) - every input record produces an
    output record, real or fallback).
    """
    chunk = load_json(chunk_path)

    reworded_by_key: Dict[Tuple, Dict] = {}
    try:
        reworded = load_json(result_path)
        reworded_by_key = {_record_key(r): r for r in reworded}
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    review_entries: List[Dict] = []
    merged: List[Dict] = []

    for record in chunk:
        key = _record_key(record)
        result = reworded_by_key.get(key)
        tag = NOBEL_CATEGORY_TAGS[record["category"]]

        if not result or not result.get("event_phrase"):
            merged.append({**record, "event_phrase": _fallback_event_phrase(record), "tags": [tag]})
            continue

        event_phrase = result["event_phrase"]
        merged_record = {
            **record,
            "event_phrase": event_phrase,
            "tags": [tag],
            "reword_prompt_version": NOBEL_REWORD_PROMPT_VERSION,
        }

        format_reason = check_phrase_format(event_phrase, record["name"])
        if format_reason:
            review_entries.append(
                {"name": record["name"], "category": record["category"], "issue_type": "format", "detail": format_reason}
            )

        missing = check_facts_preserved(build_event_text(record), event_phrase)
        if missing:
            review_entries.append(
                {
                    "name": record["name"],
                    "category": record["category"],
                    "issue_type": "facts",
                    "detail": f"missing from phrase: {', '.join(missing)}",
                }
            )

        merged.append(merged_record)

    write_review_entries(review_entries, review_path)

    try:
        existing = load_json(displayable_path)
    except FileNotFoundError:
        existing = []
    existing.extend(merged)
    save_to_json(displayable_path, existing)

    return len(merged)
