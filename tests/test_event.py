import pytest
from datetime import date
from models.person import Person
from models.event import Event

@pytest.fixture
def person():
    return Person(
        name="Einstein",
        birth_date=date(1879, 3, 14),
        description="Physicist",
        occupation="Scientist",
        industry="Academia",
        domain="Science"
    )

def test_event_age(person):
    event_date = date(1905, 11, 21)
    event = Event(date=event_date, person=person, description="E=mc^2 paper published")
    assert event.age_at_event == (event_date - person.birth_date).days

def test_event_to_dict(person):
    event_date = date(1905, 11, 21)
    event = Event(date=event_date, person=person, description="E=mc^2 paper published")
    result = event.to_dict()
    assert result["year"] == 1905
    assert result["name"] == "Einstein"
    assert "E=mc^2" in result["text"]
    assert result["age"] == event.age_at_event
