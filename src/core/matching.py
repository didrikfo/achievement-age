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

#: The hinge as it actually occurs in stored phrases: any whitespace between
#: the words, and any punctuation after "when". Matching the literal
#: PHRASE_HINGE (trailing space required) missed "Louis XIV was when, in
#: Dover, ..." and sent it down the suffix-only fallback, which prepended a
#: second "{name} was when" in front of an already name-onward phrase.
PHRASE_HINGE_PATTERN = re.compile(r"\bwas\s+when\b", re.IGNORECASE)

#: Longest subject span (in words) still read as "the person, maybe with a
#: title" - "Field Marshal Arthur Wellesley, 1st Duke of Wellington" is 8.
#: A hinge further in than this is part of a suffix-only phrase's own text.
MAX_SUBJECT_WORDS = 12

TENSE_OPENERS = {"past": "You were", "today": "You're", "future": "You'll be"}


def find_phrase_hinge(phrase: str) -> Optional[re.Match]:
    """The first "was when" in phrase that ends a plausible subject span, or None.

    Shared by display (_phrase_body) and the ingest-time format check
    (ingest.enrichment.check_phrase_format) so the two can never disagree
    about whether a phrase is name-onward.
    """
    match = PHRASE_HINGE_PATTERN.search(phrase)
    if match is None or len(phrase[: match.start()].split()) > MAX_SUBJECT_WORDS:
        return None
    return match


def _phrase_body(event: Dict) -> str:
    """Return event_phrase normalized to its name-onward clause, no tensed opening.

    Three shapes of event_phrase coexist in the database:

    1. Name-onward ("Sir Richard Owen was when ..."): used as-is. Recognised
       by a "was when" hinge after a short subject span - deliberately *not*
       also requiring the subject span to contain `name`. A phrase that names
       the person differently ("King Louis of France" for "Louis XIV") reads
       fine as written; rebuilding it would print the hinge twice. Such rows
       are caught by check_phrase_format at ingest and by
       scripts/audit_event_phrases.py instead.
    2. Full-sentence format, opening with LEGACY_PHRASE_PREFIX ("The same age
       that Sir Richard Owen was when ..."): the fixed prefix is stripped off.
    3. Oldest suffix-only format (no hinge, e.g. "he hoisted the flag"):
       reconstructed as "{name} was when {phrase}".
    """
    stripped = event["event_phrase"].strip()
    if stripped.lower().startswith(LEGACY_PHRASE_PREFIX.lower()):
        return stripped[len(LEGACY_PHRASE_PREFIX):]

    if find_phrase_hinge(stripped) is not None:
        return stripped

    return f"{event['name']}{PHRASE_HINGE}{stripped}"


def phrase_shape(event: Dict) -> str:
    """Which of _phrase_body's three shapes an event_phrase is: "name_onward",
    "legacy_prefix" or "suffix_only". Used by the phrase audit."""
    stripped = (event.get("event_phrase") or "").strip()
    if stripped.lower().startswith(LEGACY_PHRASE_PREFIX.lower()):
        return "legacy_prefix"
    if find_phrase_hinge(stripped) is not None:
        return "name_onward"
    return "suffix_only"


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
