from datetime import date
from unittest.mock import MagicMock, patch

from core.db import create_subscription, fetch_events, update_subscription_filters


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


def test_create_subscription_defaults_to_no_filters_and_no_sequences():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1))

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == []
    assert inserted["excluded_categories"] == []
    # Inclusions, not exclusions: empty means mathematical anniversaries are off.
    assert inserted["included_sequences"] == []


def test_create_subscription_stores_all_three_preference_lists():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(
            date(2000, 1, 1), ["military"], ["Sport", "Disasters"], ["Powers of 2"]
        )

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == ["military"]
    assert inserted["excluded_categories"] == ["Sport", "Disasters"]
    assert inserted["included_sequences"] == ["Powers of 2"]


def test_update_subscription_filters_writes_all_three_columns_for_the_right_token():
    mock_client = MagicMock()

    with patch("core.db.get_client", return_value=mock_client):
        update_subscription_filters("tok123", ["disaster"], ["Sport"], ["Primes"])

    mock_client.table.assert_called_with("subscriptions")
    mock_client.table.return_value.update.assert_called_with(
        {
            "excluded_tags": ["disaster"],
            "excluded_categories": ["Sport"],
            "included_sequences": ["Primes"],
        }
    )
    mock_client.table.return_value.update.return_value.eq.assert_called_with("token", "tok123")
    mock_client.table.return_value.update.return_value.eq.return_value.execute.assert_called_once()
