"""Stage 2: ask a Claude Haiku subagent which person an event is actually about.

Two-phase like ingest.llm_utils, because a Claude Code subagent runs between
the two calls:

    python -c "from ingest.subject_extraction import prepare_subject_chunks; prepare_subject_chunks()"
    # ... dispatch a Haiku subagent per chunk file, using build_prompt() ...
    python -c "from ingest.subject_extraction import merge_subject_chunk; merge_subject_chunk('data/tmp/subject_chunks/chunk_0000.json', 'data/tmp/subject_chunks/chunk_0000_result.json')"

The subagent is never asked for a birth date - only for a name it can read in
the text. Everything it returns is validated in Python before use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from core.matching import name_matches_text, normalize_name
from ingest.enrichment import resolve_subject, write_review_entries
from ingest.match_events import (
    EVENTS_WITH_AGE_PATH,
    MATCHING_REVIEW_PATH,
    SUBJECT_PENDING_PATH,
    WIDENED_BIRTHS_PATH,
    append_matched_events,
    dedup_against_file,
    load_widened_births_lookup,
)

PROMPT_TEMPLATE_PATH = Path(__file__).parent / "subject_prompt.md"
SUBJECT_CHUNK_DIR = DATA_DIR / "tmp" / "subject_chunks"
CHUNK_SIZE = 100
NO_SUBJECT_CACHE_PATH = DATA_DIR / "no_subject_cache.json"
PROMPT_VERSION = 2


def load_no_subject_cache(path: Path = NO_SUBJECT_CACHE_PATH) -> Dict[str, int]:
    """text -> the PROMPT_VERSION in effect when it was confirmed to have no subject.

    A missing or corrupt cache file is treated as empty - same tolerance as
    ingest.sources.wikidata's cache, since Stage 2 runs into the same
    crash-mid-write risk during a long chunk-processing session.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_no_subject_cache(cache: Dict[str, int], path: Path = NO_SUBJECT_CACHE_PATH) -> None:
    """Write atomically (temp file + os.replace) so a crash mid-write can't corrupt the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def build_prompt() -> str:
    """Read subject_prompt.md. No placeholders to fill - the instructions are static."""
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def prepare_subject_chunks(
    pending_path: Path = SUBJECT_PENDING_PATH,
    chunk_size: int = CHUNK_SIZE,
    chunk_dir: Path = SUBJECT_CHUNK_DIR,
    cache_path: Path = NO_SUBJECT_CACHE_PATH,
) -> List[Path]:
    """Split the Stage 2 queue into numbered chunk files for a subagent to process.

    Events already confirmed to have no subject under the current
    PROMPT_VERSION are skipped - Stage 1 requeues everything unmatched on
    every run, with no memory of what Stage 2 already checked, so this
    filtering has to happen here.
    """
    pending = load_json(pending_path)
    cache = load_no_subject_cache(cache_path)
    pending = [event for event in pending if cache.get(event.get("text")) != PROMPT_VERSION]

    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        path = chunk_dir / f"chunk_{index:04d}.json"
        save_to_json(path, pending[start : start + chunk_size])
        paths.append(path)
    return paths


WIKIDATA_PENDING_PATH = DATA_DIR / "tmp" / "wikidata_pending.json"


def route_subject(
    event: Dict,
    suggested_name: Optional[str],
    births_lookup: Dict[str, Dict],
) -> Tuple[str, object]:
    """Decide what to do with one subagent-suggested subject.

    Returns (status, payload):
    - ("matched", event_record)      - known person, plausible age; ready to store.
    - ("wikidata_candidate", name)   - really in the text, but unknown to us; Stage 3.
    - ("no_subject", None)           - the subagent found no person (an expected outcome).
    - ("rejected", reason)           - failed validation; goes to the review report.
    """
    if not suggested_name:
        return "no_subject", None

    correction, reason = resolve_subject(event, suggested_name, births_lookup)
    if correction:
        return "matched", {**event, "name": correction["name"], "age": correction["age_days"]}

    # Separate "we don't know this person" (resolvable via Wikidata) from every
    # other rejection, without branching on resolve_subject's reason wording.
    if not name_matches_text(suggested_name, event["text"]):
        return "rejected", reason
    if normalize_name(suggested_name) not in births_lookup:
        return "wikidata_candidate", suggested_name
    return "rejected", reason


def merge_subject_chunk(
    chunk_path,
    result_path,
    births_path: Path = WIDENED_BIRTHS_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    wikidata_pending_path: Path = WIKIDATA_PENDING_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
    no_subject_cache_path: Path = NO_SUBJECT_CACHE_PATH,
) -> Dict[str, int]:
    """Validate one chunk's subagent output and route each event to its next step.

    Records are matched back to the chunk by `text`, not list position, so a
    subagent that reorders or drops records is still handled. A missing or
    malformed result file, or a record the subagent dropped, leaves that
    event as "no_subject" for this run - but only a genuine `subject: null`
    response is cached (no_subject_cache_path) as a confirmed verdict, so a
    technical failure (missing file) doesn't get mistaken for an LLM having
    actually looked and found nobody.
    """
    chunk = load_json(chunk_path)
    try:
        results = load_json(result_path)
        results_by_text = {result.get("text"): result for result in results}
    except (FileNotFoundError, json.JSONDecodeError):
        results_by_text = {}

    births_lookup = load_widened_births_lookup(births_path)

    counts = {"matched": 0, "wikidata_candidate": 0, "no_subject": 0, "rejected": 0}
    matched: List[Dict] = []
    wikidata_pending: List[Dict] = []
    review: List[Dict] = []
    no_subject_texts: List[str] = []

    for event in chunk:
        result = results_by_text.get(event.get("text"))
        status, payload = route_subject(event, (result or {}).get("subject"), births_lookup)
        counts[status] += 1

        if status == "matched":
            matched.append(payload)
        elif status == "wikidata_candidate":
            wikidata_pending.append({**event, "subject": payload})
        else:
            if status == "no_subject" and result is not None:
                no_subject_texts.append(event.get("text"))
            review.append(
                {
                    "stage": "stage_2",
                    "issue_type": status,
                    # The name the subagent proposed, so the report has the same
                    # {stage, issue_type, name, text, detail} shape as Stage 1
                    # and 3. None when it found nobody at all.
                    "name": result.get("subject") if result else None,
                    "text": event.get("text"),
                    "detail": payload if status == "rejected" else "no subject identified in the text",
                }
            )

    counts["appended"] = append_matched_events(matched, matched_path, review_path)
    _append_json_list(wikidata_pending_path, wikidata_pending)
    write_review_entries(dedup_against_file(review_path, review), review_path)

    if no_subject_texts:
        cache = load_no_subject_cache(no_subject_cache_path)
        for text in no_subject_texts:
            cache[text] = PROMPT_VERSION
        save_no_subject_cache(cache, no_subject_cache_path)

    return counts


def _append_json_list(path: Path, entries: List[Dict]) -> None:
    """Append entries to a JSON array file, creating it if missing.

    Entries already present are skipped, so re-merging a chunk (or rerunning a
    whole stage) doesn't queue the same event for Stage 3 twice.
    """
    try:
        existing = load_json(path)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []
    existing.extend(dedup_against_file(path, entries))
    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(path, existing)
