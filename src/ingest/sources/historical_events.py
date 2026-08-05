"""Client for the history.muffinlabs.com events endpoint."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

import requests

BASE_URL = "http://history.muffinlabs.com/date"


def fetch_events(month: int, day: int) -> List[Dict]:
    """Fetch historical events for the provided month/day combination."""
    try:
        response = requests.get(f"{BASE_URL}/{month}/{day}")
        response.raise_for_status()
        events = response.json().get("data", {}).get("Events", [])
        return [
            {"year": event["year"], "month": month, "day": day, "text": event["text"]}
            for event in events
        ]
    except Exception as exc:  # pragma: no cover - external service wrapper
        print(f"Failed to fetch {month}/{day}: {exc}")
        return []


def fetch_year_of_events(reference_year: int = 2000) -> List[Dict]:
    """Fetch one year's worth of events (default 366 days to include Feb 29)."""
    start = date(reference_year, 1, 1)
    end = date(reference_year, 12, 31)
    current = start
    collected: List[Dict] = []

    while current <= end:
        print(f"Fetching {current.month}/{current.day}")
        collected.extend(fetch_events(current.month, current.day))
        current += timedelta(days=1)

    return collected
