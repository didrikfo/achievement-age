"""Legacy ingestion pipeline that produces intermediate JSON files."""

from __future__ import annotations

import json
from datetime import date
from typing import Dict, Iterable, List

from core.io import load_json



def calculate_age(birth_year: int, birth_month: int, birth_day: int, event_year: int, event_month: int, event_day: int) -> int | None:
    """Return the age in days between the birth date and event date."""
    try:
        dob = date(birth_year, birth_month, birth_day)
        event_date = date(event_year, event_month, event_day)
        return (event_date - dob).days
    except Exception:
        return None


def _load_birth_lookup(births: Iterable[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    """Convert birth records into a dictionary keyed by name."""
    name_to_birth: Dict[str, Dict[str, object]] = {}
    for birth in births:
        try:
            name = birth["name"]
            if len(name.split()) <= 1:
                continue
            birth_year = int(birth["year"])
            birth_month = int(birth["month"])
            birth_day = int(birth["day"])
            name_to_birth[name] = {
                "year": birth_year,
                "month": birth_month,
                "day": birth_day,
                "name": name,
            }
        except Exception:
            continue
    return name_to_birth


def match_births_to_events(births: List[Dict[str, object]], events: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Attach ages (in days) to events where we can match a person name."""
    name_to_birth = _load_birth_lookup(births)
    print(f"Loaded {len(name_to_birth)} unique names from births data.")
    print(f"Matching {len(events)} events to names...")

    results: List[Dict[str, object]] = []
    for idx, event in enumerate(events):
        if not event.get("year") or not str(event["year"]).isdigit():
            continue

        event_year = int(event["year"])  # type: ignore[arg-type]
        event_month = int(event["month"])  # type: ignore[arg-type]
        event_day = int(event["day"])  # type: ignore[arg-type]
        event_text = event["text"]

        for name, birth_info in name_to_birth.items():
            if name.lower() in str(event_text).lower():
                age = calculate_age(
                    birth_info["year"],
                    birth_info["month"],
                    birth_info["day"],
                    event_year,
                    event_month,
                    event_day,
                )
                if age is not None and 0 <= age <= 120 * 365:
                    new_event = dict(event)
                    new_event["name"] = name
                    new_event["age"] = age
                    results.append(new_event)
                    break
        if idx % 1000 == 0:
            print(f"Processed {idx} events so far.")

    return results


def only_name_and_text_from_json(event_json_path: str) -> List[Dict[str, object]]:
    events_with_age = load_json(event_json_path)
    return [{"name": event.get("name"), "text": event.get("text")} for event in events_with_age]


def main() -> None:  # pragma: no cover - manual helper
    births = load_json("data/top_1000_births.json", sort_by_field="name")
    events = load_json("data/historical_events.json")
    matched_events = match_births_to_events(births, events)

    with open("data/events_with_age.json", "w", encoding="utf-8") as handle:
        json.dump(matched_events, handle, ensure_ascii=False, indent=2)

    only_names_and_text = only_name_and_text_from_json("data/events_with_age.json")

    with open("data/events_only_name_and_text.json", "w", encoding="utf-8") as handle:
        json.dump(only_names_and_text, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":  # pragma: no cover - manual helper
    main()
