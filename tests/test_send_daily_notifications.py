"""The cron job's filtering, exercised without touching the network.

Only the selection logic is under test - _send_ntfy_notification and
_send_anniversary_notification are patched out, and what matters is which
matches reach them.
"""

from datetime import date, timedelta
from unittest.mock import patch

import scripts.send_daily_notifications as notify


def _event(name, tags, age_days):
    return {"id": 1, "name": name, "tags": tags, "age_days": age_days, "event_phrase": "x"}


def _run(subscription, events, age_days):
    birthday = (date.today() - timedelta(days=age_days)).isoformat()
    subscription = {"ntfy_topic": "t", "token": "tok", "birthday": birthday, **subscription}

    with patch.object(notify, "fetch_all_subscriptions", return_value=[subscription]), \
         patch.object(notify, "build_matchers", return_value=[lambda age: list(events)]), \
         patch.object(notify, "_send_ntfy_notification") as send_event, \
         patch.object(notify, "_send_anniversary_notification") as send_anniversary:
        notify.main()

    sent_events = [call.args[1]["name"] for call in send_event.call_args_list]
    sent_sequences = [call.args[1]["sequence"] for call in send_anniversary.call_args_list]
    return sent_events, sent_sequences


def test_a_mirroring_subscriber_is_notified_about_everything_they_marked():
    # 2048 is a power of two, so the anniversary matcher has something to find.
    events = [_event("Ada", ["science"], 2048), _event("Wellington", ["military"], 2048)]
    subscription = {
        "excluded_categories": ["War & Conflict"],
        "included_sequences": ["Powers of 2"],
        "notify_mirrors_calendar": True,
    }

    sent_events, sent_sequences = _run(subscription, events, 2048)

    assert sent_events == ["Ada"]
    assert sent_sequences == ["Powers of 2"]


def test_a_subscriber_can_mark_a_sequence_without_being_notified_about_it():
    subscription = {
        "included_sequences": ["Powers of 2"],
        "notify_mirrors_calendar": False,
        "notify_excluded_categories": [],
        # Both notify_excluded_* columns are exclusions (see core/preferences.py),
        # so naming the sequence here is what mutes it - an empty list would mean
        # every marked sequence still notifies.
        "notify_excluded_sequences": ["Powers of 2"],
    }

    _, sent_sequences = _run(subscription, [], 2048)

    assert sent_sequences == []


def test_a_subscriber_can_be_notified_about_one_category_only():
    events = [_event("Ada", ["science"], 2048), _event("Wellington", ["military"], 2048)]
    subscription = {
        "excluded_categories": [],
        "notify_mirrors_calendar": False,
        "notify_excluded_categories": [
            name for name in ["Sport", "Disasters", "Exploration & Space", "Arts & Culture",
                              "Society & Belief", "War & Conflict", "Politics & Power"]
        ],
        "notify_excluded_sequences": [],
    }

    sent_events, _ = _run(subscription, events, 2048)

    assert sent_events == ["Ada"]


def test_an_unmigrated_row_falls_back_to_the_calendar_channel():
    # No notify_* columns at all: the pre-feature behaviour, not silence.
    events = [_event("Ada", ["science"], 2048)]

    sent_events, _ = _run({"excluded_categories": []}, events, 2048)

    assert sent_events == ["Ada"]
