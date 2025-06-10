import requests
import json
from datetime import date, timedelta

BASE_URL = 'http://history.muffinlabs.com/date'

def fetch_events(month, day):
    try:
        response = requests.get(f"{BASE_URL}/{month}/{day}")
        response.raise_for_status()
        events = response.json().get("data", {}).get("Events", [])
        return [{
            "year": event["year"],
            "month": month,
            "day": day,
            "text": event["text"]
        } for event in events]
    except Exception as e:
        print(f"Failed to fetch {month}/{day}: {e}")
        return []

def main():
    start = date(2000, 1, 1)  # Arbitrary leap year to include Feb 29
    end = date(2000, 12, 31)

    all_events = []
    current = start
    while current <= end:
        print(f"Fetching {current.month}/{current.day}")
        events = fetch_events(current.month, current.day)
        all_events.extend(events)
        current += timedelta(days=1)

    with open("historical_events.json", "w", encoding="utf-8") as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
