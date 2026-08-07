# tests/test_backfill_event_enrichment.py
from ingest.backfill_event_enrichment import pending_events, resolve_event_update


def test_pending_events_excludes_already_tagged():
    events = [{"id": 1}, {"id": 2}, {"id": 3}]
    result = pending_events(events, tagged_event_ids={2})
    assert result == [{"id": 1}, {"id": 3}]


def test_resolve_event_update_flags_missing_event_phrase():
    event = {"id": 1, "text": "did a thing", "year": 2000, "month": 1, "day": 1}
    update, tags, review_entries = resolve_event_update(event, result=None, births_lookup={})
    assert update is None
    assert tags == []
    assert review_entries == [
        {"event_id": 1, "issue_type": "reword", "detail": "no usable event_phrase returned"}
    ]


def test_resolve_event_update_applies_valid_tags_and_subject_correction():
    event = {"id": 2, "text": "George Washington and John Adams hoisted the flag", "year": 1776, "month": 1, "day": 1}
    result = {"event_phrase": "he hoisted the flag", "tags": ["military"], "suggested_subject": "John Adams"}
    births_lookup = {"john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30}}

    update, tags, review_entries = resolve_event_update(event, result, births_lookup)

    assert update["event_phrase"] == "he hoisted the flag"
    assert update["name"] == "John Adams"
    assert update["age_days"] > 0
    assert tags == ["military"]
    assert review_entries == []


def test_resolve_event_update_flags_invalid_tags_but_still_writes_phrase():
    event = {"id": 3, "text": "did a thing", "year": 2000, "month": 1, "day": 1}
    result = {"event_phrase": "he did a thing", "tags": ["not-a-real-tag"], "suggested_subject": None}

    update, tags, review_entries = resolve_event_update(event, result, births_lookup={})

    assert update == {"event_phrase": "he did a thing"}
    assert tags == []
    assert len(review_entries) == 1
    assert review_entries[0]["issue_type"] == "tags"
