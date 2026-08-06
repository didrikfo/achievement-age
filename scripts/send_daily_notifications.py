"""Daily notification cron entry point, run by .github/workflows/daily_notify.yml.

For every subscriber, checks whether today's age-in-days matches any event
and pushes a notification via ntfy.sh if so. Reads SUPABASE_URL/SUPABASE_KEY/
APP_BASE_URL from the environment (no Streamlit context here).

Matches are gathered from a small list of matcher callables rather than one
hardcoded lookup, so a future non-database matcher (e.g. "age-in-days is a
round number in base 10/binary" or "is prime") can be added later without
restructuring this script - it would just be another entry in MATCHERS.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Callable, Dict, List

import requests

from core.db import fetch_all_subscriptions, fetch_events
from core.matching import events_by_age_days

APP_BASE_URL = os.environ.get("APP_BASE_URL", "")

Matcher = Callable[[int], List[Dict]]


def _make_db_event_matcher() -> Matcher:
    index = events_by_age_days(fetch_events())

    def matcher(age_days: int) -> List[Dict]:
        return index.get(age_days, [])

    return matcher


MATCHERS: List[Matcher] = [_make_db_event_matcher()]


def _send_ntfy_notification(topic: str, event: Dict, token: str) -> None:
    headers = {"Title": f"You're now as old as {event['name']} was".encode("utf-8")}
    if token and APP_BASE_URL:
        headers["Click"] = f"{APP_BASE_URL}?u={token}"
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=event["display_text"].encode("utf-8"),
        headers=headers,
        timeout=10,
    )


def main() -> None:
    subscriptions = fetch_all_subscriptions()
    today = date.today()
    notified = 0

    for subscription in subscriptions:
        birthday = date.fromisoformat(subscription["birthday"])
        age_days = (today - birthday).days

        matches: List[Dict] = []
        for matcher in MATCHERS:
            matches.extend(matcher(age_days))

        for event in matches:
            _send_ntfy_notification(subscription["ntfy_topic"], event, subscription["token"])
            notified += 1

    print(f"Checked {len(subscriptions)} subscriptions, sent {notified} notifications.")


if __name__ == "__main__":
    main()
