"""Daily notification cron entry point, run by .github/workflows/daily_notify.yml.

For every subscriber, checks whether today's age-in-days matches any event
and pushes a notification via ntfy.sh if so. Reads SUPABASE_URL/SUPABASE_KEY/
APP_BASE_URL from the environment (no Streamlit context here).

Each subscriber's matches are filtered via core.matching.events_for_subscription,
which excludes matches against both the subscriber's stored tag preference
(subscriptions.excluded_tags) and their stored coarse category preference
(subscriptions.excluded_categories) before sending, so a subscriber only hears
about the tags and categories they kept.

Matches are gathered from a small list of matcher callables rather than one
hardcoded lookup, so a future matcher producing event-shaped records can be
added without restructuring this script.

Mathematical anniversaries - days when the subscriber's age in days is itself
an interesting number - are computed separately by core.sequences and kept in
their own list, because they share no fields with an event. They carry their
own opt-in preference (subscriptions.included_sequences), which is empty for
every subscriber until they choose otherwise.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Callable, Dict, List

import requests

from core.db import fetch_all_subscriptions, fetch_events
from core.matching import events_by_age_days, events_for_subscription, full_sentence
from core.sequences import (
    anniversary_matches,
    anniversary_sentence,
    included_sequences_for_subscription,
)

APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip()

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

    for subscription in subscriptions:
        birthday = date.fromisoformat(subscription["birthday"])
        age_days = (today - birthday).days

        matches: List[Dict] = []
        for matcher in MATCHERS:
            matches.extend(matcher(age_days))
        matches = events_for_subscription(matches, subscription)

        for event in matches:
            _send_ntfy_notification(subscription["ntfy_topic"], event, subscription["token"])
            notified += 1

        # Anniversaries are computed, not looked up, and carry their own opt-in
        # preference - the event category/tag filter has no bearing on them.
        anniversaries = anniversary_matches(
            age_days, included_sequences_for_subscription(subscription)
        )
        for anniversary in anniversaries:
            _send_anniversary_notification(
                subscription["ntfy_topic"], anniversary, subscription["token"]
            )
            notified += 1

    print(f"Checked {len(subscriptions)} subscriptions, sent {notified} notifications.")


if __name__ == "__main__":
    main()
