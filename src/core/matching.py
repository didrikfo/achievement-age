"""Matching helpers between people and events."""

from __future__ import annotations

import re
import unicodedata
from typing import Collection, Dict, Iterable, List, Optional, Sequence

from core.age import Tense
from core.config import TAG_CATEGORIES, TAG_TAXONOMY


def primary_category(event: Dict) -> Optional[str]:
    """The event's single coarse category, or None if it has no recognized tag.

    Derived from the event's tags at read time rather than stored, so it costs
    no schema change and self-corrects when an event's tags are edited. The
    first category in TAG_CATEGORIES order that the event has any tag in wins -
    see the precedence note there.
    """
    tags = set(event.get("tags") or ())
    for category, category_tags in TAG_CATEGORIES.items():
        if tags.intersection(category_tags):
            return category
    return None


def included_from_excluded(
    excluded: Collection[str], taxonomy: Sequence[str] = TAG_TAXONOMY
) -> List[str]:
    """Return the taxonomy entries a subscriber should see, given what they excluded.

    Ordered by `taxonomy` so the result is stable regardless of input order.
    Defaults to the tag taxonomy; pass CATEGORY_NAMES for the coarse one.
    """
    excluded_set = set(excluded or ())
    return [entry for entry in taxonomy if entry not in excluded_set]


def excluded_from_included(
    included: Collection[str], taxonomy: Sequence[str] = TAG_TAXONOMY
) -> List[str]:
    """Return what to store for a UI selection: the taxonomy minus that selection.

    Exclusions are stored rather than inclusions so an entry added to the
    taxonomy later is visible to existing subscribers by default instead of
    silently hidden.
    """
    included_set = set(included or ())
    return [entry for entry in taxonomy if entry not in included_set]


def filter_events(
    events: Iterable[Dict],
    included_categories: Collection[str],
    included_tags: Collection[str],
) -> List[Dict]:
    """Keep events whose coarse category is included and that keep at least one tag.

    Two levels, deliberately asymmetric:

    - An event with no recognized category always survives. The corpus is tagged
      by a manually-run backfill, so untagged rows are a normal transient state -
      hiding them would drop real matches because of backfill lag rather than
      user intent.
    - Otherwise the event's single primary_category must be included, AND at
      least one of its tags must be included. The category gate comes first, so
      an event can never leak back in from a hidden category on a secondary tag;
      the tag check can only narrow within kept categories. With every tag
      selected (the default) this reduces to pure category filtering.
    """
    categories = set(included_categories or ())
    tags = set(included_tags or ())
    kept: List[Dict] = []
    for event in events:
        category = primary_category(event)
        if category is None:
            kept.append(event)
            continue
        if category in categories and any(tag in tags for tag in event.get("tags") or ()):
            kept.append(event)
    return kept


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
PHRASE_HINGE = " was when "

TENSE_OPENERS = {"past": "You were", "today": "You're", "future": "You'll be"}


def _phrase_body(event: Dict) -> str:
    """Return event_phrase normalized to its name-onward clause, no tensed opening.

    Three shapes of event_phrase coexist in the database while the reprocessing
    pass (docs/superpowers/specs/2026-08-22-tense-aware-display-text-design.md)
    is still running manually:

    1. New format, already name-onward ("Sir Richard Owen was when ..."): used
       as-is.
    2. Current full-sentence format, opening with LEGACY_PHRASE_PREFIX ("The
       same age that Sir Richard Owen was when ..."): the fixed prefix is
       stripped off.
    3. Oldest suffix-only format (predates both prompts, no recognizable
       opening): reconstructed as "{name} was when {phrase}", the same
       fallback this module has always used for pre-2026-08-08 rows.
    """
    phrase = event["event_phrase"]
    stripped = phrase.lstrip()
    if stripped.lower().startswith(LEGACY_PHRASE_PREFIX.lower()):
        return stripped[len(LEGACY_PHRASE_PREFIX):]

    hinge_at = stripped.lower().find(PHRASE_HINGE)
    if hinge_at != -1 and normalize_name(event["name"]) in normalize_name(stripped[:hinge_at]):
        return stripped

    return f"{event['name']}{PHRASE_HINGE}{stripped}"


def full_sentence(event: Dict, tense: Tense) -> str:
    """Return the event's tensed display sentence.

    tense is the caller's own comparison of the viewed day to the real today
    (core.age.tense_for) - not recomputed here, since a caller rendering
    several events for the same day should compute it once and reuse it.
    """
    return f"{TENSE_OPENERS[tense]} the same age {_phrase_body(event)}"


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
