"""Matching helpers between people and events."""

from __future__ import annotations

import re
import unicodedata
from typing import Collection, Dict, Iterable, List

from core.config import TAG_TAXONOMY


def included_from_excluded(excluded_tags: Collection[str]) -> List[str]:
    """Return the taxonomy tags a subscriber should see, given what they excluded.

    Ordered by TAG_TAXONOMY so the result is stable regardless of input order.
    """
    excluded = set(excluded_tags or ())
    return [tag for tag in TAG_TAXONOMY if tag not in excluded]


def excluded_from_included(included_tags: Collection[str]) -> List[str]:
    """Return what to store for a UI selection: the taxonomy minus that selection.

    Exclusions are stored rather than inclusions so a tag added to TAG_TAXONOMY
    later is visible to existing subscribers by default instead of silently
    hidden.
    """
    included = set(included_tags or ())
    return [tag for tag in TAG_TAXONOMY if tag not in included]


def filter_events_by_tags(events: Iterable[Dict], included_tags: Collection[str]) -> List[Dict]:
    """Keep events that are untagged, or carry at least one tag in included_tags.

    Two deliberately permissive rules:

    - An untagged event always survives. The corpus is tagged by a manually-run
      backfill, so untagged rows are a normal transient state - hiding them would
      drop real matches because of backfill lag rather than user intent.
    - A tagged event survives on any single surviving tag, not all of them, so
      unchecking one box can't remove events the user never asked to hide.
    """
    included = set(included_tags or ())
    kept: List[Dict] = []
    for event in events:
        tags = event.get("tags") or []
        if not tags or any(tag in included for tag in tags):
            kept.append(event)
    return kept


def events_for_subscription(events: Iterable[Dict], subscription: Dict) -> List[Dict]:
    """Filter events by a subscription's stored tag preference.

    Reads excluded_tags defensively: if the column hasn't been added to the
    database yet, every subscriber's row is missing the key, and raising here
    would kill the whole daily notification run rather than just one subscriber.
    Absent or null means no filtering, which is the pre-feature behavior.
    """
    excluded = subscription.get("excluded_tags") or []
    return filter_events_by_tags(events, included_from_excluded(excluded))


def find_matching_events(events: Iterable[Dict], age_in_days: int) -> List[Dict]:
    """Return events where the person's age at the event matches age_in_days exactly."""
    return [event for event in events if event["age_days"] == age_in_days]


def events_by_age_days(events: Iterable[Dict]) -> Dict[int, List[Dict]]:
    """Group events by age_days so any day's age can be looked up in O(1)."""
    index: Dict[int, List[Dict]] = {}
    for event in events:
        index.setdefault(event["age_days"], []).append(event)
    return index


LEGACY_PHRASE_PREFIX = "The same age that "


def full_sentence(event: Dict) -> str:
    """Return the event's display sentence, rebuilding the opening for legacy rows.

    event_phrase now stores the complete sentence - the reword subagent writes
    it end to end so it can put a title next to the name ("Sir Richard Owen").
    Rows written before that change store only the fragment after "...was when ",
    so anything that doesn't already open the sentence gets the old static
    prefix rebuilt around it.

    Kept as a normalizer rather than a plain field read because the reprocessing
    backfill is run manually: both formats coexist in the database for as long
    as that takes, and a subagent that ignores the template would otherwise
    render with no opening at all.
    """
    phrase = event["event_phrase"]
    if phrase.lstrip().lower().startswith(LEGACY_PHRASE_PREFIX.lower()):
        return phrase
    return f"{LEGACY_PHRASE_PREFIX}{event['name']} was when {phrase}"


def normalize_name(text: str) -> str:
    """Casefold text and strip diacritics/punctuation for robust name comparison."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_diacritics = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Replace (rather than drop) punctuation so e.g. "O'Brien" stays two matchable tokens.
    letters_and_spaces = re.sub(r"[^\w\s]", " ", without_diacritics)
    collapsed = re.sub(r"\s+", " ", letters_and_spaces).strip()
    return collapsed.casefold()


def compile_name_pattern(name: str) -> re.Pattern[str] | None:
    """Compile a word-boundary regex for a normalized name, or None if name is empty.

    Callers matching one name against many texts (or vice versa) should compile
    once and reuse the pattern/normalized text rather than calling name_matches_text
    repeatedly, which renormalizes and recompiles on every call.
    """
    normalized_name = normalize_name(name)
    if not normalized_name:
        return None
    return re.compile(r"\b" + re.escape(normalized_name) + r"\b")


def name_matches_text(name: str, text: str) -> bool:
    """Return True if name appears as a whole word (or phrase) in text.

    Both sides are normalized first (casefold, diacritics/punctuation stripped),
    then matched with word boundaries so short names can't match inside unrelated
    longer words (e.g. "Art" no longer matches inside "parts").
    """
    pattern = compile_name_pattern(name)
    if pattern is None:
        return False
    return pattern.search(normalize_name(text)) is not None
