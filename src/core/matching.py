"""Matching helpers between people and events."""

from __future__ import annotations

import re
import unicodedata
from typing import Collection, Dict, Iterable, List, Optional, Sequence

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
