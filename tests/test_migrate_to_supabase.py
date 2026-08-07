from ingest.migrate_to_supabase import _to_event_row


def test_to_event_row_includes_person_id_and_age():
    entry = {
        "name": "Ada Lovelace",
        "text": "published notes",
        "event_phrase": "she published her notes",
        "year": "1843",
        "month": 1,
        "day": 1,
        "age": 12345,
    }
    row = _to_event_row(entry, {"Ada Lovelace": 7})
    assert row == {
        "name": "Ada Lovelace",
        "person_id": 7,
        "text": "published notes",
        "event_phrase": "she published her notes",
        "year": 1843,
        "month": 1,
        "day": 1,
        "age_days": 12345,
        "event_type": "achievement",
        "source": "initial_migration",
    }


def test_to_event_row_person_id_none_when_name_not_upserted_yet():
    entry = {
        "name": "Unknown Person",
        "text": "did something",
        "event_phrase": "did something",
        "year": "2000",
        "month": 1,
        "day": 1,
        "age": 1,
    }
    row = _to_event_row(entry, {})
    assert row["person_id"] is None
