from unittest.mock import MagicMock, patch

from core.db import fetch_events


def test_fetch_events_selects_with_person_join():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.execute.return_value.data = [
        {"id": 1, "name": "Ada Lovelace", "persons": {"wikipedia_url": "https://en.wikipedia.org/wiki/Ada_Lovelace"}},
        {"id": 2, "name": "Unlinked Person", "persons": None},
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    mock_client.table.assert_called_once_with("events")
    mock_client.table.return_value.select.assert_called_once_with("*, persons(wikipedia_url)")
    assert result[0]["persons"]["wikipedia_url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"
    assert result[1]["persons"] is None
