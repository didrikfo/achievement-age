"""Daily notification cron entry point, run by .github/workflows/daily_notify.yml.

For every subscriber, checks whether today's age-in-days matches any event
and pushes a notification via ntfy.sh if so. Reads SUPABASE_URL/SUPABASE_KEY/
APP_BASE_URL from the environment (no Streamlit context here).

Each subscriber's matches are filtered through the NOTIFICATION channel of
core.preferences - which is their calendar selection unless they have turned
mirroring off, in which case it is their narrower notification selection
intersected with it. A subscriber can therefore mark prime days on the calendar
without being pushed one every nine days, or browse every category while being
notified about science alone.

Matches are gathered from a small list of matcher callables rather than one
hardcoded lookup, so a future matcher producing event-shaped records can be
added without restructuring this script.

Mathematical anniversaries are computed rather than looked up, so they are
gathered separately from the matchers - they share no fields with an event -
but they are filtered through the same notification channel.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Callable, Dict, List

import requests

from core.db import fetch_all_subscriptions, fetch_events
from core.matching import events_by_age_days, filter_events, full_sentence
from core.preferences import preferences_from_subscription
from core.sequences import anniversary_matches, anniversary_sentence

APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip()

Matcher = Callable[[int], List[Dict]]


def _make_db_event_matcher() -> Matcher:
    index = events_by_age_days(fetch_events())

    def matcher(age_days: int) -> List[Dict]:
        return index.get(age_days, [])

    return matcher


def build_matchers() -> List[Matcher]:
    """The matchers to run, built at call time rather than at import.

    Deferred so that importing this module is free of side effects: at module
    scope this opened a Supabase connection and pulled the entire corpus, which
    made the script impossible to import in a test and meant any import error
    downstream surfaced as a database error.
    """
    return [_make_db_event_matcher()]


def _send_ntfy_notification(topic: str, event: Dict, token: str) -> None:
    headers = {"Title": f"You're now as old as {event['name']} was".encode("utf-8")}
    if token and APP_BASE_URL:
        headers["Click"] = f"{APP_BASE_URL}?u={token}"
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=full_sentence(event).encode("utf-8"),
        headers=headers,
        timeout=10,
    )


def _send_anniversary_notification(topic: str, anniversary: Dict, token: str) -> None:
    """Push one mathematical anniversary.

    A sibling of _send_ntfy_notification rather than a branch inside it: that
    function builds its title from event['name'], and an anniversary has no
    name, no person and no date - only a number and what's interesting about it.
    """
    headers = {"Title": "You've hit a mathematical anniversary".encode("utf-8")}
    if token and APP_BASE_URL:
        headers["Click"] = f"{APP_BASE_URL}?u={token}"
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=anniversary_sentence(anniversary).encode("utf-8"),
        headers=headers,
        timeout=10,
    )


def main() -> None:
    subscriptions = fetch_all_subscriptions()
    today = date.today()
    notified = 0
    matchers = build_matchers()

    for subscription in subscriptions:
        birthday = date.fromisoformat(subscription["birthday"])
        age_days = (today - birthday).days
        notify_channel = preferences_from_subscription(subscription).notify

        matches: List[Dict] = []
        for matcher in matchers:
            matches.extend(matcher(age_days))
        matches = filter_events(matches, notify_channel.categories, notify_channel.tags)

        for event in matches:
            _send_ntfy_notification(subscription["ntfy_topic"], event, subscription["token"])
            notified += 1

        for anniversary in anniversary_matches(age_days, notify_channel.sequences):
            _send_anniversary_notification(
                subscription["ntfy_topic"], anniversary, subscription["token"]
            )
            notified += 1

    print(f"Checked {len(subscriptions)} subscriptions, sent {notified} notifications.")


if __name__ == "__main__":
    main()
