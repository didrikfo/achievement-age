"""Client for the history.muffinlabs.com births endpoint."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Dict, List

import requests

BASE_URL = "http://history.muffinlabs.com/date"


def fetch_births(month: int, day: int) -> List[Dict]:
    """Fetch historical births for the provided month/day combination."""
    try:
        response = requests.get(f"{BASE_URL}/{month}/{day}")
        response.raise_for_status()
        births = response.json().get("data", {}).get("Births", [])
        return [
            {
                "year": birth["year"],
                "month": month,
                "day": day,
                "name": re.split(',|\(d', birth["text"])[0].strip(),
                "text": birth["text"].split(",", 1)[-1].strip(),
            }
            for birth in births
        ]
    except Exception as exc:  # pragma: no cover - external service wrapper
        print(f"Failed to fetch {month}/{day}: {exc}")
        return []


def extract_name(text: str) -> str:
    """Extract the name (everything before the first comma)."""
    return text.split(",", 1)[0].strip()


def clean_births(input_file: str, output_file: str) -> None:
    """Deduplicate births by keeping the earliest recorded year for each person."""
    with open(input_file, "r", encoding="utf-8") as handle:
        births = json.load(handle)

    seen_names: Dict[str, Dict[str, object]] = {}
    for entry in births:
        if not entry.get("year") or not str(entry["year"]).isdigit():
            continue
        name = extract_name(entry["text"])
        year = int(entry["year"])
        if name not in seen_names or year < seen_names[name]["year"]:
            seen_names[name] = {"entry": entry, "year": year}

    cleaned_data = [value["entry"] for value in seen_names.values()]

    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(cleaned_data, handle, ensure_ascii=False, indent=2)


def main() -> None:  # pragma: no cover - manual helper
    start = date(2000, 1, 1)
    end = date(2000, 12, 31)

    all_births: List[Dict] = []
    current = start
    while current <= end:
        print(f"Fetching {current.month}-{current.day}")
        all_births.extend(fetch_births(current.month, current.day))
        current += timedelta(days=1)

    with open("historical_births.json", "w", encoding="utf-8") as handle:
        json.dump(all_births, handle, ensure_ascii=False, indent=2)

    clean_births("historical_births.json", "historical_births_cleaned.json")


if __name__ == "__main__":  # pragma: no cover - manual helper
    main()
