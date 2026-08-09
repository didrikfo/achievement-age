"""Stage 0/1 of matching expansion: find every known person named in an event,
using one Aho-Corasick pass over a widened births pool.

Supersedes ingest.pipeline.match_births_to_events, which gated on the top 1,000
Pantheon-ranked people (1,232 matched events) and silently kept whichever name
its dict iteration reached first when a text named several known people. Here a
text naming several known people yields one matched record per person - every
co-subject is kept rather than one being guessed at and the rest discarded (see
classify_event). Only commemorative phrasing ("named after", "anniversary of")
still defers to Stage 2 rather than being auto-matched.
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
    """Natural key for a matched event: (name, text).

    A text can legitimately be stored more than once - once per co-subject
    genuinely named in it - so `text` alone doesn't identify a record.
    llm_utils._event_key and migrate_to_supabase.filter_new_entries use the
    same (name, text) key, so all three stages agree about what counts as the
    same record. Which same-text records are really the same person recorded
    twice, rather than distinct co-subjects, is decided by
    append_matched_events's token-boundary truncation rule below.
    """
    return (event.get("name"), event.get("text"))


def _contains_token_run(shorter: List[str], longer: List[str]) -> bool:
    """True if `shorter` appears as a contiguous run of whole tokens inside `longer`."""
    if not shorter or len(shorter) > len(longer):
        return False
    return any(
        longer[start : start + len(shorter)] == shorter
        for start in range(len(longer) - len(shorter) + 1)
    )


def _is_truncation_of(norm_a: str, norm_b: str) -> bool:
    """True if one normalized name is the other truncated at a token boundary.

    Raw character containment is the wrong test here: "otto i" is a substring
    of "otto ii", but Otto I and his son Otto II are two people, as are
    "mehmed v" and "mehmed vi". Comparing whole tokens rejects those while
    still catching the case this rule exists for - "muhammad ali" inside
    "muhammad ali jinnah", a genuine mis-truncation of one person's name.
    """
    tokens_a = norm_a.split()
    tokens_b = norm_b.split()
    if len(tokens_a) <= len(tokens_b):
        return _contains_token_run(tokens_a, tokens_b)
    return _contains_token_run(tokens_b, tokens_a)


def append_matched_events(
    new_events: List[Dict],
    path: Path = EVENTS_WITH_AGE_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> int:
    """Append matched events to path, applying the truncation correction rule.

    A text can legitimately carry more than one matched record (several real
    people named in the same event - see classify_event's multi-candidate
    case). When a new (name, text) candidate's text already has a *different*
    stored name, the two normalized names are compared against every existing
    name recorded for that text:
    - Same normalized name (differs only in casing/diacritics/punctuation
      from the raw-string `seen` check above) -> already present under a
      near-duplicate spelling. Skipped silently: not appended, not counted
      in `added`, no review entry.
    - One a token-boundary truncation of the other (_is_truncation_of) -> the
      same person, mis-truncated in one of the two records. The name with more
      tokens wins: if the new candidate is fuller it replaces that one shorter
      existing record and logs the correction to review (issue_type
      "corrected_subject"), since a previously-matched - possibly
      already-migrated - record is being removed; otherwise the new candidate
      is rejected and logged to review (issue_type "shorter_duplicate").
    - No truncation relationship with any existing name for that text -> a
      genuinely different person, and a legitimate additional co-subject.
      Both are kept ("Otto I" alongside "Otto II").

    Returns how many records were actually added (a correction counts as 1,
    same as a fresh addition). Safe to call repeatedly - Stage 1, 2 and 3 all
    append to the same file across separate runs.
    """
    try:
        existing = load_json(path)
    except FileNotFoundError:
        existing = []

    seen = {_event_key(event) for event in existing}
    names_by_text: Dict[object, Set[str]] = {}
    for event in existing:
        names_by_text.setdefault(event.get("text"), set()).add(event.get("name"))

    added = 0
    review_entries: List[Dict] = []
    for event in new_events:
        if _event_key(event) in seen:
            continue

        text = event.get("text")
        new_name = event.get("name")
        norm_new = normalize_name(new_name)

        replaced_name = None
        rejected_reason = None
        already_present = False
        for existing_name in names_by_text.get(text, set()):
            norm_existing = normalize_name(existing_name)
            if norm_new == norm_existing:
                already_present = True
                break
            if _is_truncation_of(norm_new, norm_existing):
                if len(norm_new.split()) > len(norm_existing.split()):
                    replaced_name = existing_name
                else:
                    rejected_reason = f"a fuller name is already recorded: {existing_name!r}"
                break

        if already_present:
            continue

        if rejected_reason:
            review_entries.append(
                {
                    "stage": "append",
                    "issue_type": "shorter_duplicate",
                    "name": new_name,
                    "text": text,
                    "detail": rejected_reason,
                }
            )
            continue

        if replaced_name is not None:
            existing = [
                e for e in existing if not (e.get("text") == text and e.get("name") == replaced_name)
            ]
            seen.discard((replaced_name, text))
            names_by_text[text].discard(replaced_name)
            review_entries.append(
                {
                    "stage": "append",
                    "issue_type": "corrected_subject",
                    "name": new_name,
                    "text": text,
                    "detail": f"replaced shorter match {replaced_name!r}",
                }
            )

        existing.append(event)
        seen.add(_event_key(event))
        names_by_text.setdefault(text, set()).add(new_name)
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(path, existing)
    write_review_entries(dedup_against_file(review_path, review_entries), review_path)
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
