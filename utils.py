import json
from datetime import date
from models.person import Person
from models.event import Event

def load_json(filename, sort_by_field=None):
    with open(filename, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        if sort_by_field:
            # Sort the data by the specified field in descending order
            json_data.sort(key=lambda x: len(x.get(sort_by_field, '')), reverse=True)
        return json_data    

def load_persons_from_json(filepath: str) -> dict:
    """
    Load persons from a JSON file and return a dict keyed by name.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    persons = {}
    for entry in data:
        try:
            name = entry["name"]
            birth_date = date(int(entry["year"]), int(entry["month"]), int(entry["day"]))
            person = Person(
                name=name,
                birth_date=birth_date,
                description=entry.get("text", ""),
                occupation=entry.get("occupation", ""),
                industry=entry.get("industry", ""),
                domain=entry.get("domain", "")
            )
            persons[name] = person
        except Exception as e:
            print(f"Skipping invalid person entry: {entry} ({e})")

    return persons

def load_events_from_json(filepath: str, persons: dict) -> list:
    """
    Load events and link them to Person objects via name.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    events = []
    for entry in data:
        try:
            name = entry["name"]
            person = persons.get(name)
            if not person:
                continue  # skip if person not found

            event_date = date(int(entry["year"]), int(entry["month"]), int(entry["day"]))
            description = entry["text"]
            event = Event(date=event_date, person=person, description=description)
            events.append(event)
        except Exception as e:
            print(f"Skipping invalid event entry: {entry} ({e})")

    return events
