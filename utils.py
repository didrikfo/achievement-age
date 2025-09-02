import json
from datetime import date
from ast import literal_eval
from models.person import Person
from models.event import Event

def load_json(filename: str, sort_by_field: bool = None) -> list[dict]:
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

def load_events_from_json(filepath: str, persons: dict) -> list[dict]:
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
            display_text = entry["display_text"]
            event = Event(date=event_date, 
                          person=person, 
                          description=description, 
                          display_text=display_text)
            events.append(event)
        except Exception as e:
            print(f"Skipping invalid event entry: {entry} ({e})")

    return events

def save_to_json(filepath: str, data: list[dict]) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def parse_llm_output(llm_output_string: str) -> list[dict]:
    llm_output_parsed = literal_eval(llm_output_string)
    assert(type(llm_output_parsed) == list)
    assert(type(llm_output_parsed[0] == dict))

    return llm_output_parsed