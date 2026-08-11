from ingest.backfill_person_wikipedia import birth_years_by_person, year_matches


def _event(person_id, event_date, age_days):
    year, month, day = event_date
    return {"person_id": person_id, "year": year, "month": month, "day": day, "age_days": age_days}


def test_birth_year_is_derived_from_an_event_date_minus_age():
    # Einstein: born 1879-03-14, special relativity paper 1905-06-30.
    events = [_event(1, (1905, 6, 30), 9605)]

    years, conflicting = birth_years_by_person(events)

    assert years == {1: 1879}
    assert conflicting == []


def test_two_events_for_one_person_agree():
    events = [_event(1, (1905, 6, 30), 9605), _event(1, (1921, 11, 9), 15581)]

    years, conflicting = birth_years_by_person(events)

    assert years == {1: 1879}
    assert conflicting == []


def test_conflicting_events_are_reported_not_averaged():
    events = [_event(1, (1905, 6, 30), 9605), _event(1, (1905, 6, 30), 100)]

    years, conflicting = birth_years_by_person(events)

    assert 1 not in years
    assert conflicting == [1]


def test_events_with_an_unusable_date_are_skipped():
    # month 0 / day 0 placeholders exist in scraped source data; date() rejects
    # them, and one bad row must not take out the whole run.
    events = [_event(1, (1905, 0, 0), 100), _event(2, (1905, 6, 30), 9605)]

    years, conflicting = birth_years_by_person(events)

    assert years == {2: 1879}
    assert 1 not in years


def test_events_without_a_person_id_are_ignored():
    events = [_event(None, (1905, 6, 30), 9605)]

    assert birth_years_by_person(events) == ({}, [])


def test_year_matches_exactly():
    assert year_matches(1879, 1879)


def test_year_matches_within_one_year():
    # A Julian/Gregorian or timezone edge can shift a January or December birth
    # date across a year boundary; that is not a wrong-person signal.
    assert year_matches(1879, 1880)
    assert year_matches(1879, 1878)


def test_year_does_not_match_a_different_person():
    assert not year_matches(1879, 1955)


def test_year_does_not_match_when_wikidata_has_none():
    assert not year_matches(1879, None)
