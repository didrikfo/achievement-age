import json
import re
from datetime import date

from utils import load_json

def calculate_age(birth_year, birth_month, birth_day, event_year, event_month, event_day):
    try:
        dob = date(birth_year, birth_month, birth_day)
        event_date = date(event_year, event_month, event_day)
        age = event_date - dob
        return age.days
    except:
        return None

def match_births_to_events(births, events):
    results = []
    # Preprocess births into a dictionary for faster lookup
    name_to_birth = {}
    for birth in births:
        name = birth["name"]
        try:
            # Skip if name is a single word or empty
            if len(name.split()) <= 1 or (len(name.split()) == 2 and name.split()[1] in ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]):
                continue
            else:
                birth_year = int(birth["year"])
                birth_month = birth["month"]  
                birth_day = birth["day"] 
                name_to_birth[name] = {"year": birth_year, "month": birth_month, "day": birth_day, "name": name}
        except:
            continue
    
    print(f"Loaded {len(name_to_birth)} unique names from births data.")
    print(f"Matching {len(events)} events to names...")
    for event in events:
        # skip events where "year" is not a digit
        if not event.get("year") or not event["year"].isdigit():
            continue
        # Extract event details
        event_text = event["text"]
        event_year = int(event["year"])
        event_month = event["month"]
        event_day = event["day"]
        for name, birth_info in name_to_birth.items():
            # Check if the name is in the event text
            # Using regex to enusre we match whole names
            #if re.search(r'\b' + re.escape(name) + r'\b', event_text, re.IGNORECASE):
            if name.lower() in event_text.lower():
                # Calculate age at the time of the event
                age = calculate_age(
                    birth_info["year"],  # year of birth
                    birth_info["month"], 
                    birth_info["day"],
                    event_year, event_month, event_day
                )
                if age is not None and age >= 0 and age <= 120*365:  # Check if age is a reasonable value
                    new_event = event.copy()
                    new_event["name"] = name
                    new_event["age"] = age
                    results.append(new_event)
                    break  # Stop checking other names for this event
        if events.index(event) % 1000 == 0:  # Print progress every 100 events
            print(f"Processed {events.index(event)} events so far.")

    return results

def main():
    births = load_json('data/top_100_births.json', sort_by_field='name')
    events = load_json('data/historical_events.json')
    matched_events = match_births_to_events(births, events)
    
    with open('data/events_with_age.json', 'w', encoding='utf-8') as f:
        json.dump(matched_events, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
