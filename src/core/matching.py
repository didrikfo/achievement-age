"""Matching helpers between people and events."""

from __future__ import annotations

from typing import Iterable, List

from .models.event import Event


def find_matching_events(events: Iterable[Event], age_in_days: int) -> List[Event]:
    """Placeholder; use for refactored matching logic."""
    return [event for event in events if event.age_at_event == age_in_days]
