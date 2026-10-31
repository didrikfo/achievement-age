from datetime import date
from unittest.mock import MagicMock, patch

from postgrest import ReturnMethod

from core.db import (
    create_subscription,
    fetch_events,
    get_client,
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
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1))

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == []
    assert inserted["excluded_categories"] == []
    # Inclusions, not exclusions: empty means mathematical anniversaries are off.
    assert inserted["included_sequences"] == []
    # Mirroring is the default, so a new subscriber's notifications follow their calendar.
    assert inserted["notify_mirrors_calendar"] is True


def test_create_subscription_stores_all_six_preference_columns():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    preferences = toggle_notify(
        toggle_tag(toggle_calendar(default_preferences(), Row(CATEGORY, "Sport")), "military"),
        Row(CATEGORY, "Science & Technology"),
    )

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1), preferences)

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_categories"] == ["Sport"]
    assert inserted["excluded_tags"] == ["military"]
    assert inserted["notify_mirrors_calendar"] is False
    # Only Science is MUTED. Sport was dropped from the calendar, which is a
    # different axis - the override never learns about it, and the intersection
    # in Preferences.notify is what stops a hidden row from notifying. Writing
    # the union of the two here would destroy a subscriber's mutes on the next
    # save; see the round-trip test below.
    assert inserted["notify_excluded_categories"] == ["Science & Technology"]


def test_create_subscription_does_not_ask_to_read_the_row_back():
    # Under RLS the publishable key can insert a subscription but not select
    # it, so a representation return would make every signup fail.
    mock_client = MagicMock()

    with patch("core.db.get_client", return_value=mock_client):
        created = create_subscription(date(2000, 1, 1))

    insert = mock_client.table.return_value.insert
    assert insert.call_args.kwargs["returning"] == ReturnMethod.minimal
    assert created == insert.call_args.args[0]
    assert created["token"] and created["ntfy_topic"].startswith("achage-")
    assert created["birthday"] == "2000-01-01"


def test_get_subscription_looks_up_the_token_through_the_sql_function():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = [{"token": "tok123"}]

    with patch("core.db.get_client", return_value=mock_client):
        result = get_subscription("tok123")

    mock_client.rpc.assert_called_with("get_subscription", {"p_token": "tok123"})
    mock_client.table.assert_not_called()
    assert result == {"token": "tok123"}


def test_get_subscription_returns_none_for_an_unknown_token():
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value.data = []

    with patch("core.db.get_client", return_value=mock_client):
        assert get_subscription("nope") is None


def test_update_subscription_filters_writes_all_six_columns_for_the_right_token():
    mock_client = MagicMock()
    preferences = toggle_calendar(default_preferences(), Row(SEQUENCE, "Primes"))

    with patch("core.db.get_client", return_value=mock_client):
        update_subscription_filters("tok123", preferences)

    mock_client.rpc.assert_called_with(
        "update_subscription_preferences",
        {"p_token": "tok123", "p_preferences": preferences_to_columns(preferences)},
    )
    mock_client.rpc.return_value.execute.assert_called_once()
    mock_client.table.assert_not_called()


def test_preference_columns_match_the_update_sql_function():
    # update_subscription_preferences (SUPABASE_SETUP.md section 16) names each
    # column explicitly. A key added here but not there is silently never saved,
    # so changing this set means changing that function too.
    assert set(preferences_to_columns(default_preferences())) == {
        "excluded_categories",
        "excluded_tags",
        "included_sequences",
        "notify_mirrors_calendar",
        "notify_excluded_categories",
        "notify_excluded_sequences",
    }


def test_get_client_prefers_the_secret_key_when_one_is_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_x")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_x")

    with patch("core.db.create_client") as create:
        get_client()

    create.assert_called_with("https://example.supabase.co", "sb_secret_x")


def test_get_client_falls_back_to_the_publishable_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_x")
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)

    with patch("core.db.create_client") as create:
        get_client()

    create.assert_called_with("https://example.supabase.co", "sb_publishable_x")
