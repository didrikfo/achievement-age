"""Lightweight JSON helpers used during the transition to SQLite."""

from __future__ import annotations

import json
from ast import literal_eval
from datetime import date
from typing import Dict, List

from .models.event import Event
from .models.person import Person


def load_json(filename: str, sort_by_field: str | None = None) -> List[Dict]:
    """Load a JSON document and optionally sort it by sort_by_field."""
    with open(filename, "r", encoding="utf-8") as handle:
        json_data = json.load(handle)
        if sort_by_field:
            json_data.sort(key=lambda item: len(item.get(sort_by_field, "")), reverse=True)
        return json_data


def load_persons_from_json(filepath: str) -> Dict[str, Person]:
    """Load persons from a JSON file and return a dictionary keyed by name."""
    with open(filepath, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    persons: Dict[str, Person] = {}
    for entry in data:
        try:
            name = entry["name"]
            birth_date = date(int(entry["year"]), int(entry["month"]), int(entry["day"]))
            persons[name] = Person(
                name=name,
                birth_date=birth_date,
                description=entry.get("text", ""),
                occupation=entry.get("occupation", ""),
                industry=entry.get("industry", ""),
                domain=entry.get("domain", ""),
            )
        except Exception as exc:  # pragma: no cover - legacy behaviour
            print(f"Skipping invalid person entry: {entry} ({exc})")

    return persons


def load_events_from_json(filepath: str, persons: Dict[str, Person]) -> List[Event]:
    """Load events and link them to Person objects via name."""
    with open(filepath, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    events: List[Event] = []
    for entry in data:
        try:
            person = persons.get(entry["name"])
            if not person:
                continue

            event_date = date(int(entry["year"]), int(entry["month"]), int(entry["day"]))
            events.append(
                Event(
                    date=event_date,
                    person=person,
                    description=entry["text"],
                    display_text=entry["display_text"],
                )
            )
        except Exception as exc:  # pragma: no cover - legacy behaviour
            print(f"Skipping invalid event entry: {entry} ({exc})")

    return events


def save_to_json(filepath: str, data: List[Dict]) -> None:
    """Persist a list of dictionaries to filepath using UTF-8 encoding."""
    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def parse_llm_output(llm_output_string: str) -> List[Dict]:
    """Parse the LLM output string produced by llm_utils helpers."""
    llm_output_parsed = literal_eval(llm_output_string)
    assert isinstance(llm_output_parsed, list)
    assert isinstance(llm_output_parsed[0], dict)
    return llm_output_parsed
