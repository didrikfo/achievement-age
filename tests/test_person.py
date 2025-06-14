import pytest
from datetime import date
from models.person import Person

def test_person_attributes():
    p = Person(
        name="Albert Einstein",
        birth_date=date(1879, 3, 14),
        description="Physicist",
        occupation="Scientist",
        industry="Academia",
        domain="Science"
    )
    assert p.name == "Albert Einstein"
    assert p.birth_date.year == 1879
    assert p.domain == "Science"

def test_age_on():
    p = Person(
        name="Ada Lovelace",
        birth_date=date(1815, 12, 10),
        description="Mathematician",
        occupation="Scientist",
        industry="Academia",
        domain="Mathematics"
    )
    event_date = date(1835, 12, 10)
    assert p.age_on(event_date) == 7305  # 20 years * 365 + 5 leap days
