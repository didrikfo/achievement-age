from datetime import date

from core.matching import events_by_age_days, full_sentence, name_matches_text, normalize_name


def test_true_positive_with_punctuation():
    assert name_matches_text("O'Brien", "Kevin O'Brien announced the results.")


def test_false_positive_avoided_short_name_inside_longer_word():
    # A plain substring check would wrongly match "Art" inside "parts".
    assert not name_matches_text("Art", "He organized many parts of the exhibit.")


def test_diacritic_normalization():
    assert name_matches_text("José", "The news mentioned Jose Rizal today.")


def test_normalize_name_strips_diacritics_and_punctuation():
    assert normalize_name("José O'Brien") == "jose o brien"


def _event(name, birth_date, event_date, event_phrase=""):
    return {
        "name": name,
        "age_days": (event_date - birth_date).days,
        "event_phrase": event_phrase,
    }


def test_events_by_age_days_groups_by_age():
    washington = _event("George Washington", date(1732, 2, 22), date(1776, 1, 1))
    einstein = _event("Albert Einstein", date(1879, 3, 14), date(1905, 11, 21))

    index = events_by_age_days([washington, einstein])

    assert index[washington["age_days"]] == [washington]
    assert index[einstein["age_days"]] == [einstein]


def test_events_by_age_days_same_age_shares_bucket():
    same_age_date = date(2000, 1, 1)
    person_a = _event("Person A", date(1990, 1, 1), same_age_date)
    person_b = _event("Person B", date(1985, 1, 1), date(1995, 1, 1))

    assert person_a["age_days"] == person_b["age_days"]

    index = events_by_age_days([person_a, person_b])

    assert index[person_a["age_days"]] == [person_a, person_b]


def test_full_sentence_combines_name_and_phrase():
    event = {"name": "George Washington", "event_phrase": "he hoisted the flag"}
    assert full_sentence(event) == "The same age that George Washington was when he hoisted the flag"
