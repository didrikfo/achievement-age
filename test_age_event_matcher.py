import pytest
from datetime import date, timedelta
from unittest.mock import patch
from io import StringIO
from display_event import search_for_event_matching_age, display_matching_events


@pytest.fixture
def einstein_event():
    return {
        "year": "1905",
        "month": 11,
        "day": 21,
        "text": "Albert Einstein's paper that leads to the mass-energy equivalence formula, E = mc², is published in the journal Annalen der Physik.",
        "name": "Albert Einstein",
        "age": 9748
    }

def test_search_for_event_matching_age(einstein_event):
    result = search_for_event_matching_age([einstein_event], 9748)
    assert len(result) == 1
    assert result[0]["name"] == "Albert Einstein"

def test_search_no_matching_event(einstein_event):
    result = search_for_event_matching_age([einstein_event], 10000)
    assert result == []

@patch("display_event.date")
@patch("sys.stdout", new_callable=StringIO)
def test_display_matching_event_output(mock_stdout, mock_date, einstein_event):
    mock_date.today.return_value = date.today()  # Today
    birthday = date.today() - timedelta(days=9748)  # Make birthday by subtracting exactly 9748 days from today


    display_matching_events([einstein_event], birthday)

    output = mock_stdout.getvalue()
    assert "Your age is" in output
    assert "Albert Einstein" in output
    assert "9748 days" in output
