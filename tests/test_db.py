from datetime import date
from unittest.mock import MagicMock, patch

from core.db import (
    create_subscription,
    fetch_events,
    get_subscription,
    update_subscription_filters,
)
from core.preferences import (
    CATEGORY,
    SEQUENCE,
    Row,
    default_preferences,
    preferences_to_columns,
    toggle_calendar,
    toggle_notify,
    toggle_tag,
)


def test_fetch_events_selects_with_person_and_tag_joins():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.range.return_value.execute.return_value.data = [
        {"id": 1, "name": "Ada Lovelace", "persons": {"wikipedia_url": "https://en.wikipedia.org/wiki/Ada_Lovelace"}},
        {"id": 2, "name": "Unlinked Person", "persons": None},
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    mock_client.table.assert_called_with("events")
    mock_client.table.return_value.select.assert_called_with(
        "*, persons(wikipedia_url), event_tags(tags(name))"
    )
    assert result[0]["persons"]["wikipedia_url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"
    assert result[1]["persons"] is None


def test_fetch_events_paginates_past_the_page_size():
    mock_client = MagicMock()
    full_page = [{"id": i, "name": f"Person {i}", "persons": None} for i in range(1000)]
    short_page = [{"id": 1000, "name": "Person 1000", "persons": None}]

    mock_execute = mock_client.table.return_value.select.return_value.range.return_value.execute
    mock_execute.side_effect = [
        MagicMock(data=full_page),
        MagicMock(data=short_page),
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    assert len(result) == 1001
    range_calls = mock_client.table.return_value.select.return_value.range.call_args_list
    assert range_calls[0].args == (0, 999)
    assert range_calls[1].args == (1000, 1999)


def test_fetch_events_flattens_nested_tag_rows():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.range.return_value.execute.return_value.data = [
        {
            "id": 1,
            "name": "Ada Lovelace",
            "persons": None,
            "event_tags": [{"tags": {"name": "science"}}, {"tags": {"name": "technology"}}],
        },
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    assert result[0]["tags"] == ["science", "technology"]
    # The nested join shape is not left behind for callers to trip over.
    assert "event_tags" not in result[0]


def test_fetch_events_gives_untagged_events_an_empty_list():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.range.return_value.execute.return_value.data = [
        {"id": 1, "name": "No Tags", "persons": None, "event_tags": []},
        {"id": 2, "name": "Null Tags", "persons": None, "event_tags": None},
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    assert result[0]["tags"] == []
    assert result[1]["tags"] == []


def test_create_subscription_without_preferences_uses_the_defaults():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = {"token": "abc"}

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1))

    assert mock_client.rpc.call_args.args[0] == "create_subscription"
    inserted = mock_client.rpc.call_args.args[1]["p_preferences"]
    assert inserted["excluded_tags"] == []
    assert inserted["excluded_categories"] == []
    # Inclusions, not exclusions: empty means mathematical anniversaries are off.
    assert inserted["included_sequences"] == []
    # Mirroring is the default, so a new subscriber's notifications follow their calendar.
    assert inserted["notify_mirrors_calendar"] is True


def test_create_subscription_stores_all_six_preference_columns():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = {"token": "abc"}

    preferences = toggle_notify(
        toggle_tag(toggle_calendar(default_preferences(), Row(CATEGORY, "Sport")), "military"),
        Row(CATEGORY, "Science & Technology"),
    )

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1), preferences)

    inserted = mock_client.rpc.call_args.args[1]["p_preferences"]
    assert inserted["excluded_categories"] == ["Sport"]
    assert inserted["excluded_tags"] == ["military"]
    assert inserted["notify_mirrors_calendar"] is False
    # Only Science is MUTED. Sport was dropped from the calendar, which is a
    # different axis - the override never learns about it, and the intersection
    # in Preferences.notify is what stops a hidden row from notifying. Writing
    # the union of the two here would destroy a subscriber's mutes on the next
    # save; see the round-trip test below.
    assert inserted["notify_excluded_categories"] == ["Science & Technology"]


def test_update_subscription_filters_writes_all_six_columns_for_the_right_token():
    mock_client = MagicMock()
    preferences = toggle_calendar(default_preferences(), Row(SEQUENCE, "Primes"))

    with patch("core.db.get_client", return_value=mock_client):
        update_subscription_filters("tok123", preferences)

    assert mock_client.rpc.call_args.args == (
        "update_subscription_filters",
        {"p_token": "tok123", "p_preferences": preferences_to_columns(preferences)},
    )
    mock_client.rpc.return_value.execute.assert_called_once()
    # The table is closed to the anon role, so nothing may reach it directly.
    mock_client.table.assert_not_called()


def test_create_subscription_lets_the_database_mint_the_token():
    """The token and topic are the function's to generate - see SUPABASE_SETUP.md 15."""
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = {"token": "abc", "ntfy_topic": "t"}

    with patch("core.db.get_client", return_value=mock_client):
        created = create_subscription(date(2000, 1, 1))

    arguments = mock_client.rpc.call_args.args[1]
    assert arguments["p_birthday"] == "2000-01-01"
    assert "token" not in arguments["p_preferences"]
    assert "ntfy_topic" not in arguments["p_preferences"]
    # A composite-returning function gives one object back, not a list of them.
    assert created == {"token": "abc", "ntfy_topic": "t"}


def test_get_subscription_returns_none_when_the_token_matches_nothing():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = []

    with patch("core.db.get_client", return_value=mock_client):
        assert get_subscription("nope") is None

    assert mock_client.rpc.call_args.args == ("get_subscription", {"p_token": "nope"})


def test_get_subscription_unwraps_the_single_matching_row():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = [{"token": "tok123"}]

    with patch("core.db.get_client", return_value=mock_client):
        assert get_subscription("tok123") == {"token": "tok123"}
