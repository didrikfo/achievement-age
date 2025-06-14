import requests
import json
import re
from datetime import date, timedelta

BASE_URL = 'http://history.muffinlabs.com/date'

def fetch_births(month, day):
    try:
        response = requests.get(f"{BASE_URL}/{month}/{day}")
        response.raise_for_status()
        births = response.json().get("data", {}).get("Births", [])
        return [{
            "year": birth["year"],
            "month": month,
            "day": day,
            # Get name by fetching "text" from the birth, splitting at the first comma or death year parenthesis e.g. (d. 1937), and returning the first part
            # Then return the rest of the text as "text"
            "name": re.split(',|\(d',birth["text"])[0].strip(),
            "text": birth["text"].split(",", 1)[-1].strip(),
        } for birth in births]
    except Exception as e:
        print(f"Failed to fetch {month}/{day}: {e}")
        return []

def extract_name(text):
    """Extracts the name (everything before the first comma)"""
    return text.split(',', 1)[0].strip()

def clean_births(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        births = json.load(f)

    seen_names = {}
    
    for entry in births:
        # Only process entries where "year" does not contain a non-digit character
        if not entry.get("year") or not entry["year"].isdigit():
            continue
        # Extract name and year
        name = extract_name(entry["text"])
        year = int(entry["year"]) if entry["year"].isdigit() else float('inf')

        if name not in seen_names or year < seen_names[name]["year"]:
            seen_names[name] = {"entry": entry, "year": year}

    cleaned_data = [value["entry"] for value in seen_names.values()]

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)


def main():
    start = date(2000, 1, 1)  # Arbitrary leap year to include Feb 29
    end = date(2000, 12, 31)

    all_births = []
    current = start
    while current <= end:
        print(f"Fetching {current.month}-{current.day}")
        births = fetch_births(current.month, current.day)
        all_births.extend(births)
        current += timedelta(days=1)

    with open("historical_births.json", "w", encoding="utf-8") as f:
        json.dump(all_births, f, ensure_ascii=False, indent=2)

    clean_births("historical_births.json", "historical_births_cleaned.json")

if __name__ == "__main__":
    main()
