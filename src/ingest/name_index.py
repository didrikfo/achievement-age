"""Aho-Corasick index for finding every known person name inside an event text.

Replaces the per-name regex loop in ingest.pipeline.match_births_to_events,
which was O(events x names) - ~17 minutes at 39,297 names. One automaton is
built once, then each event text is scanned in time proportional to the text
length rather than the name count.

Ingest-only: `ahocorasick` comes from requirements-ingest.txt, which the
deployed Streamlit app does not install. Nothing under src/app/ or src/core/
may import this module.
"""

from __future__ import annotations

from typing import Iterable, List

import ahocorasick

from core.matching import normalize_name


def build_name_index(names: Iterable[str]) -> ahocorasick.Automaton:
    """Build an automaton over normalized names. Blank/unnormalizable names are skipped."""
    automaton = ahocorasick.Automaton()
    for name in names:
        normalized = normalize_name(name)
        if normalized:
            automaton.add_word(normalized, normalized)
    if len(automaton) > 0:
        automaton.make_automaton()
    return automaton


def _is_whole_word(text: str, start: int, end: int) -> bool:
    """True if text[start:end + 1] is bounded by non-word characters on both sides.

    normalize_name leaves only word characters and single spaces, so checking
    the neighbouring character is enough to stop a short name matching inside
    a longer word.
    """
    before = text[start - 1] if start > 0 else " "
    after = text[end + 1] if end + 1 < len(text) else " "
    return not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")


def find_names_in_text(automaton: ahocorasick.Automaton, text: str) -> List[str]:
    """Return every indexed name occurring as a whole word in text, normalized and sorted."""
    if len(automaton) == 0:
        return []

    normalized_text = normalize_name(text)
    found = set()
    for end_index, name in automaton.iter(normalized_text):
        start_index = end_index - len(name) + 1
        if _is_whole_word(normalized_text, start_index, end_index):
            found.add(name)
    return sorted(found)
