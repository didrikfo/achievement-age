from ingest.migrate_to_supabase import _to_event_row, filter_new_entries


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
        "reword_prompt_version": 0,
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


def test_filter_new_entries_drops_already_migrated_events():
    entries = [
        {"name": "Marie Curie", "text": "she won a prize"},
        {"name": "Albert Einstein", "text": "he published a paper"},
    ]
    existing = {("Marie Curie", "she won a prize")}

    assert filter_new_entries(entries, existing) == [
        {"name": "Albert Einstein", "text": "he published a paper"}
    ]


def test_filter_new_entries_keeps_everything_when_supabase_is_empty():
    entries = [{"name": "Marie Curie", "text": "she won a prize"}]

    assert filter_new_entries(entries, set()) == entries


def test_filter_new_entries_deduplicates_within_the_input():
    entries = [
        {"name": "Marie Curie", "text": "she won a prize"},
        {"name": "Marie Curie", "text": "she won a prize"},
    ]

    assert len(filter_new_entries(entries, set())) == 1


from ingest.migrate_to_supabase import report_unmatched_legacy_entries


def test_to_event_row_defaults_the_prompt_version_to_zero():
    entry = {
        "name": "Ada Lovelace",
        "text": "published notes",
        "event_phrase": "she published notes",
        "year": "1843",
        "month": 1,
        "day": 1,
        "age": 50,
    }
    assert _to_event_row(entry, {})["reword_prompt_version"] == 0


def test_to_event_row_carries_a_stamped_prompt_version_through():
    entry = {
        "name": "Ada Lovelace",
        "text": "published notes",
        "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
        "reword_prompt_version": 1,
        "year": "1843",
        "month": 1,
        "day": 1,
        "age": 50,
    }
    assert _to_event_row(entry, {})["reword_prompt_version"] == 1


def test_report_unmatched_legacy_entries_is_empty_when_every_legacy_row_matches():
    entries = [
        {"name": "Ada Lovelace", "text": "published notes", "display_text": "The same age that..."},
        {"name": "New Person", "text": "did a thing", "event_phrase": "they did a thing"},
    ]
    existing = {("Ada Lovelace", "published notes")}
    assert report_unmatched_legacy_entries(entries, existing) == []


def test_report_unmatched_legacy_entries_flags_a_legacy_row_missing_from_supabase():
    # Happens if a Supabase-side subject correction renamed the row: the local
    # copy no longer key-matches, so migrating would insert a duplicate.
    entries = [{"name": "Mary Wollstonecraft", "text": "published Frankenstein", "display_text": "The same age that..."}]
    assert report_unmatched_legacy_entries(entries, set()) == entries


def test_report_unmatched_legacy_entries_ignores_new_style_records():
    # Records with event_phrase have never been migrated - being absent is expected.
    entries = [{"name": "New Person", "text": "did a thing", "event_phrase": "they did a thing"}]
    assert report_unmatched_legacy_entries(entries, set()) == []
