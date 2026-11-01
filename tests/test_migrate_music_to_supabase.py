import json
from datetime import date
from unittest.mock import MagicMock, patch

from core.matching import _phrase_body
from ingest.enrichment import check_phrase_format
from ingest.migrate_music_to_supabase import (
    find_birth_date_conflicts,
    implied_birth_dates,
    insert_music_events,
    merge_same_day,
    prepare_music_events,
    split_duplicate_days,
    to_event_row,
)
from ingest.sources.music import BILLBOARD_200_SOURCE, HOT_100_SOURCE, MUSIC_TAG


def _resolved(kind="hot100_number_one", title="Anti-Hero", **overrides):
    base = {
        "kind": kind, "record_key": f"{kind}:{title}", "title": title, "credit": "Taylor Swift",
        "names": ["Taylor Swift"], "name": "Taylor Swift", "weeks_at_number_one": 8,
        "year": 2022, "month": 11, "day": 5, "age_days": 12015,
        "birth_year": 1989, "birth_month": 12, "birth_day": 13, "birth_source": "wikidata", "wikidata_qid": "Q26876",
    }
    return {**base, **overrides}


# --- merge_same_day ---


def test_merge_same_day_joins_a_single_and_an_album_topping_the_charts_the_same_week():
    # The real case: Midnights and "Anti-Hero" were both number one on the charts dated 2022-11-05.
    records = [_resolved(kind="billboard200_number_one", title="Midnights"), _resolved()]

    [event] = merge_same_day(records)

    assert event["event_phrase"] == (
        'Taylor Swift was when "Anti-Hero" reached number one on the Billboard Hot 100 and '
        "the album Midnights reached number one on the Billboard 200."
    )
    assert check_phrase_format(event["event_phrase"], "Taylor Swift") is None
    assert event["text"].startswith('"Anti-Hero" by Taylor Swift reached number one on the Billboard Hot 100')
    assert "Midnights by Taylor Swift reached number one on the Billboard 200" in event["text"]
    assert event["source"] == HOT_100_SOURCE
    assert event["record_keys"] == ["hot100_number_one:Anti-Hero", "billboard200_number_one:Midnights"]
    assert event["tags"] == [MUSIC_TAG]


def test_a_namesake_gets_a_suffixed_person_but_keeps_the_plain_name_in_the_event():
    # Jack Johnson the boxer is already in the calendar; persons.name is unique.
    [event] = merge_same_day([_resolved(name="Jack Johnson", names=["Jack Johnson"], credit="Jack Johnson")])

    assert event["name"] == "Jack Johnson (musician)"
    row = to_event_row({**event, "person_id": 7})
    assert row["name"] == "Jack Johnson"
    assert row["event_phrase"].startswith("Jack Johnson was when ")
    assert check_phrase_format(row["event_phrase"], row["name"]) is None
    # What core.matching._phrase_body needs: the phrase isn't rebuilt around the name.
    assert _phrase_body(row) == row["event_phrase"]


def test_a_namesake_no_longer_conflicts_with_the_person_who_has_the_plain_name():
    event = merge_same_day([_resolved(name="Jack Johnson", names=["Jack Johnson"], credit="Jack Johnson")])[0]
    boxer = [{"id": 931, "name": "Jack Johnson"}]

    assert find_birth_date_conflicts([event], boxer, {931: {date(1878, 3, 31)}}) == []


def test_merge_same_day_keeps_different_days_and_people_apart():
    records = [
        _resolved(),
        _resolved(kind="billboard200_number_one", title="1989", year=2014, month=11, day=15, age_days=9103),
        _resolved(name="Someone Else", names=["Taylor Swift", "Someone Else"]),
    ]

    events = merge_same_day(records)

    assert len(events) == 3
    assert {event["source"] for event in events} == {HOT_100_SOURCE, BILLBOARD_200_SOURCE}


# --- birth-date conflicts with existing persons ---


def test_implied_birth_dates_subtracts_age_days_and_skips_rows_without_a_person():
    rows = [
        {"person_id": 7, "year": 2022, "month": 11, "day": 5, "age_days": 12015},
        {"person_id": None, "year": 2000, "month": 1, "day": 1, "age_days": 100},
    ]
    assert implied_birth_dates(rows) == {7: {date(1989, 12, 13)}}


def test_find_birth_date_conflicts_flags_a_namesake_already_in_the_calendar():
    # The existing "Ray Charles" person's events imply the births file's 1918
    # date - a different Ray Charles from the singer born 1930.
    event = merge_same_day([_resolved(name="Ray Charles", names=["Ray Charles"], birth_year=1930,
                                      birth_month=9, birth_day=23, year=1960, month=11, day=14)])[0]
    existing_persons = [{"id": 3, "name": "Ray Charles", "wikipedia_url": None}]

    conflicts = find_birth_date_conflicts([event], existing_persons, {3: {date(1918, 9, 13)}})

    assert len(conflicts) == 1
    assert conflicts[0]["issue_type"] == "birth_date_conflict"
    assert "1930-09-23" in conflicts[0]["detail"]


def test_find_birth_date_conflicts_accepts_a_matching_or_undated_existing_person():
    event = merge_same_day([_resolved()])[0]
    persons = [{"id": 1, "name": "Taylor Swift"}]

    assert find_birth_date_conflicts([event], persons, {1: {date(1989, 12, 13)}}) == []
    assert find_birth_date_conflicts([event], persons, {}) == []


# --- duplicate-day guard and the insert row ---


def test_split_duplicate_days_blocks_a_person_who_already_has_an_event_that_day():
    events = [{**merge_same_day([_resolved()])[0], "person_id": 7}]
    existing = {(7, 2022, 11, 5): {"id": 99, "text": "already there"}}

    keep, blocked = split_duplicate_days(events, existing)

    assert keep == []
    assert blocked[0][1]["id"] == 99


def test_to_event_row_has_exactly_the_events_columns():
    event = {**merge_same_day([_resolved()])[0], "person_id": 7}
    row = to_event_row(event)
    assert set(row) == {"name", "person_id", "text", "event_phrase", "year", "month", "day", "age_days",
                        "event_type", "source"}
    assert row["event_type"] == "achievement"


# --- orchestrators ---


def _make_client(persons=None, event_rows=None, upserted=None, tags=None, inserted=None):
    tables = {"persons": MagicMock(), "events": MagicMock(), "tags": MagicMock(), "event_tags": MagicMock()}
    tables["persons"].select.return_value.order.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=persons or []),
    ]
    tables["persons"].upsert.return_value.execute.return_value.data = upserted or []
    tables["events"].select.return_value.order.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=event_rows or []),
    ]
    tables["events"].insert.return_value.execute.return_value.data = inserted or []
    tables["tags"].select.return_value.execute.return_value.data = tags or []
    client = MagicMock()
    client.table.side_effect = lambda name: tables[name]
    client._tables = tables
    return client


def test_prepare_music_events_skips_conflicts_and_duplicates_and_writes_the_rest(tmp_path):
    records = [
        _resolved(),
        _resolved(name="Ray Charles", names=["Ray Charles"], credit="Ray Charles", title="Georgia on My Mind",
                  birth_year=1930, birth_month=9, birth_day=23, year=1960, month=11, day=14, age_days=11010),
        _resolved(name="Adele", names=["Adele"], credit="Adele", title="Hello", year=2015, month=11, day=14,
                  birth_year=1988, birth_month=5, birth_day=5, age_days=10054),
    ]
    client = _make_client(
        persons=[{"id": 3, "name": "Ray Charles", "wikipedia_url": None}],
        event_rows=[
            {"id": 50, "person_id": 3, "year": 1960, "month": 1, "day": 1, "age_days": 15085, "text": "x"},
            {"id": 51, "person_id": 8, "year": 2015, "month": 11, "day": 14, "age_days": 10054, "text": "Adele..."},
        ],
        upserted=[{"id": 7, "name": "Taylor Swift"}, {"id": 8, "name": "Adele"}],
    )
    output = tmp_path / "music_displayable.json"

    with patch("ingest.migrate_music_to_supabase.get_client", return_value=client):
        stats = prepare_music_events(
            records, output_path=output, person_review_path=tmp_path / "person.json",
            duplicate_review_path=tmp_path / "duplicate.json",
        )

    assert stats == {"resolved_records": 3, "events": 3, "name_collisions": 0, "birth_date_conflicts": 1,
                     "duplicate_day_blocked": 1, "pending": 1}
    upserted_names = [row["name"] for row in client._tables["persons"].upsert.call_args[0][0]]
    assert upserted_names == ["Adele", "Taylor Swift"]  # the conflicting Ray Charles is never upserted
    written = json.loads(output.read_text(encoding="utf-8"))
    assert [(event["name"], event["person_id"]) for event in written] == [("Taylor Swift", 7)]


def test_insert_music_events_tags_every_row_and_skips_one_already_inserted():
    events = [
        {**merge_same_day([_resolved()])[0], "person_id": 7},
        {**merge_same_day([_resolved(name="Adele", names=["Adele"], year=2015, month=11, day=14)])[0], "person_id": 8},
    ]
    client = _make_client(
        event_rows=[{"id": 1, "person_id": 8, "year": 2015, "month": 11, "day": 14, "age_days": 1, "text": "x"}],
        tags=[{"id": 4, "name": "music"}],
        inserted=[{"id": 101}],
    )

    with patch("ingest.migrate_music_to_supabase.get_client", return_value=client):
        count = insert_music_events(events)

    assert count == 1
    assert [row["name"] for row in client._tables["events"].insert.call_args[0][0]] == ["Taylor Swift"]
    assert client._tables["event_tags"].insert.call_args[0][0] == [{"event_id": 101, "tag_id": 4}]
