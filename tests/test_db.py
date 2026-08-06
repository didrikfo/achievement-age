from unittest.mock import MagicMock, patch

from core.db import fetch_events


def test_fetch_events_selects_with_person_join():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.range.return_value.execute.return_value.data = [
        {"id": 1, "name": "Ada Lovelace", "persons": {"wikipedia_url": "https://en.wikipedia.org/wiki/Ada_Lovelace"}},
        {"id": 2, "name": "Unlinked Person", "persons": None},
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    mock_client.table.assert_called_with("events")
    mock_client.table.return_value.select.assert_called_with("*, persons(wikipedia_url)")
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
