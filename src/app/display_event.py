"""Console helpers for displaying age-matched events."""

from __future__ import annotations

from datetime import date
from typing import Iterable, List

from core.age import age_breakdown
from core.config import DATA_DIR
from core.io import load_events_from_json, load_persons_from_json
from core.matching import find_matching_events
from core.models.event import Event


def get_user_age_in_days() -> tuple[int, date]:
    """Prompt the user for their birthday and calculate their age in days."""
    while True:
        try:
            year = int(input("Enter your birth year (YYYY): "))
            month = int(input("Enter your birth month (1-12): "))
            day = int(input("Enter your birth day (1-31): "))
            birthday = date(year, month, day)
            today = date.today()
            age_in_days = (today - birthday).days
            return age_in_days, birthday
        except ValueError:
            print("Invalid input. Please enter numeric values for year, month, and day.")


def search_for_event_matching_age(events: Iterable[Event], user_age: int) -> List[Event]:
    """Search for events where the age in days matches the user's age."""
    return find_matching_events(events, user_age)


def display_matching_events(matching_events: Iterable[Event], birthday: date) -> None:
    """Display the user's age and matching events."""
    years, months, days = age_breakdown(birthday, date.today())
    print(f"\nYour age is {years} years, {months} months, and {days} days old.")

    events = list(matching_events)
    if not events:
        print("No events found matching your age.")
        return

    for event in events:
        print(event.display_text)


def main() -> None:
    persons = load_persons_from_json(DATA_DIR / "top_1000_births.json")
    events = load_events_from_json(DATA_DIR / "displayable_events.json", persons)
    user_age, birthday = get_user_age_in_days()
    matching_events = search_for_event_matching_age(events, user_age)
    display_matching_events(matching_events, birthday)


if __name__ == "__main__":  # pragma: no cover - manual invocation helper
    main()
