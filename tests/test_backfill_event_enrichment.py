# tests/test_backfill_event_enrichment.py
import json
from unittest.mock import MagicMock, patch

from ingest.backfill_event_enrichment import (
    _fetch_tagged_event_ids,
    merge_chunk,
    pending_events,
    resolve_event_update,
)


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


def test_fetch_tagged_event_ids_paginates_past_the_page_size():
    mock_client = MagicMock()
    full_page = [{"event_id": i} for i in range(1000)]
    short_page = [{"event_id": 1000}]

    mock_execute = mock_client.table.return_value.select.return_value.range.return_value.execute
    mock_execute.side_effect = [
        MagicMock(data=full_page),
        MagicMock(data=short_page),
    ]

    result = _fetch_tagged_event_ids(mock_client)

    assert result == set(range(1001))
    range_calls = mock_client.table.return_value.select.return_value.range.call_args_list
    assert range_calls[0].args == (0, 999)
    assert range_calls[1].args == (1000, 1999)


def _make_mock_client(tags=None, persons_upsert_data=None):
    """A MagicMock Supabase client with distinct, independently-assertable sub-mocks per table."""
    tags = tags if tags is not None else []
    table_mocks = {
        "tags": MagicMock(),
        "persons": MagicMock(),
        "events": MagicMock(),
        "event_tags": MagicMock(),
    }
    table_mocks["tags"].select.return_value.execute.return_value.data = tags
    if persons_upsert_data is not None:
        table_mocks["persons"].upsert.return_value.execute.return_value.data = persons_upsert_data

    client = MagicMock()
    client.table.side_effect = lambda name: table_mocks[name]
    client._table_mocks = table_mocks
    return client


def test_merge_chunk_falls_back_to_name_text_match_when_result_has_no_id(tmp_path):
    chunk = [{"id": 5, "name": "Ada Lovelace", "text": "did X", "year": 2000, "month": 1, "day": 1}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # A subagent that faithfully followed a prompt lacking `id` in its output schema -
    # no "id" key at all, so the id-keyed lookup alone would miss this event entirely.
    result = [{"name": "Ada Lovelace", "text": "did X", "event_phrase": "she did X", "tags": ["science"]}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    mock_client = _make_mock_client(tags=[{"id": 1, "name": "science"}])

    with patch("ingest.backfill_event_enrichment.get_client", return_value=mock_client), patch(
        "ingest.backfill_event_enrichment.load_births_lookup", return_value={}
    ):
        merged_count = merge_chunk(chunk_path, result_path, review_path=review_path)

    assert merged_count == 1
    mock_client._table_mocks["events"].update.assert_called_once_with({"event_phrase": "she did X"})
    mock_client._table_mocks["events"].update.return_value.eq.assert_called_once_with("id", 5)
    mock_client._table_mocks["event_tags"].insert.assert_called_once_with([{"event_id": 5, "tag_id": 1}])
    assert not review_path.exists()


def test_merge_chunk_continues_past_a_per_event_failure_and_still_flushes_review(tmp_path):
    chunk = [
        {"id": 1, "name": "Person One", "text": "did A", "year": 2000, "month": 1, "day": 1},
        {"id": 2, "name": "Person Two", "text": "did B", "year": 2000, "month": 1, "day": 1},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {"id": 1, "event_phrase": "they did A", "tags": ["science"]},
        {"id": 2, "event_phrase": "they did B", "tags": ["science"]},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    mock_client = _make_mock_client(tags=[{"id": 1, "name": "science"}])
    # Simulate event id=1's events.update() blowing up (e.g. the duplicate-tag-insert
    # crash fix 2 describes), while event id=2 should still be processed normally.
    mock_client._table_mocks["events"].update.return_value.eq.return_value.execute.side_effect = [
        Exception("boom"),
        MagicMock(),
    ]

    with patch("ingest.backfill_event_enrichment.get_client", return_value=mock_client), patch(
        "ingest.backfill_event_enrichment.load_births_lookup", return_value={}
    ):
        merged_count = merge_chunk(chunk_path, result_path, review_path=review_path)

    assert merged_count == 2
    # Both events were attempted despite the first raising.
    assert mock_client._table_mocks["events"].update.call_count == 2

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review == [{"event_id": 1, "issue_type": "error", "detail": "boom"}]


def test_merge_chunk_upserts_person_and_sets_person_id_on_accepted_correction(tmp_path):
    chunk = [
        {
            "id": 10,
            "name": "George Washington",
            "text": "George Washington and John Adams hoisted the flag",
            "year": 1776,
            "month": 1,
            "day": 1,
        }
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {
            "id": 10,
            "event_phrase": "he hoisted the flag",
            "tags": ["military"],
            "suggested_subject": "John Adams",
        }
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    births_lookup = {"john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30}}
    mock_client = _make_mock_client(
        tags=[{"id": 2, "name": "military"}],
        persons_upsert_data=[{"id": 99, "name": "John Adams"}],
    )

    with patch("ingest.backfill_event_enrichment.get_client", return_value=mock_client), patch(
        "ingest.backfill_event_enrichment.load_births_lookup", return_value=births_lookup
    ):
        merge_chunk(chunk_path, result_path, review_path=tmp_path / "review.json")

    mock_client._table_mocks["persons"].upsert.assert_called_once_with({"name": "John Adams"}, on_conflict="name")

    update_call = mock_client._table_mocks["events"].update.call_args
    update_payload = update_call.args[0]
    assert update_payload["name"] == "John Adams"
    assert update_payload["person_id"] == 99
    assert update_payload["age_days"] > 0
    assert update_payload["event_phrase"] == "he hoisted the flag"


from ingest.backfill_event_enrichment import pending_phrasing_events
from ingest.enrichment import REWORD_PROMPT_VERSION


def test_pending_phrasing_events_selects_rows_below_the_current_version():
    events = [
        {"id": 1, "reword_prompt_version": 0},
        {"id": 2, "reword_prompt_version": REWORD_PROMPT_VERSION},
        {"id": 3},  # column default never read back - treat a missing value as 0
    ]
    result = pending_phrasing_events(events, REWORD_PROMPT_VERSION)
    assert [event["id"] for event in result] == [1, 3]


def test_resolve_event_update_phrasing_mode_writes_only_phrase_and_version():
    event = {"id": 4, "name": "Ada Lovelace", "text": "Ada Lovelace published notes.", "year": 1843, "month": 1, "day": 1}
    result = {
        "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
        "tags": ["science"],
        "suggested_subject": None,
    }

    update, tags, review_entries = resolve_event_update(event, result, births_lookup={}, mode="phrasing")

    assert update == {
        "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
        "reword_prompt_version": REWORD_PROMPT_VERSION,
    }
    # Tags are already assigned for these rows; the phrasing pass must not touch them.
    assert tags == []
    assert review_entries == []


def test_resolve_event_update_phrasing_mode_records_but_does_not_apply_a_subject_correction():
    event = {
        "id": 5,
        "name": "George Washington",
        "text": "George Washington and John Adams hoisted the flag",
        "year": 1776,
        "month": 1,
        "day": 1,
    }
    result = {
        # Names both people, so the fact check stays quiet and this test isolates
        # the subject behaviour.
        "event_phrase": "The same age that George Washington was when he and John Adams hoisted the flag.",
        "suggested_subject": "John Adams",
    }
    births_lookup = {"john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30}}

    update, _tags, review_entries = resolve_event_update(event, result, births_lookup, mode="phrasing")

    assert "name" not in update
    assert "age_days" not in update
    assert [entry["issue_type"] for entry in review_entries] == ["subject"]
    assert "John Adams" in review_entries[0]["detail"]


def test_resolve_event_update_phrasing_mode_flags_a_malformed_phrase():
    event = {"id": 6, "name": "Ada Lovelace", "text": "published notes", "year": 1843, "month": 1, "day": 1}
    result = {"event_phrase": "she published her notes"}

    update, _tags, review_entries = resolve_event_update(event, result, births_lookup={}, mode="phrasing")

    assert update["event_phrase"] == "she published her notes"
    assert [entry["issue_type"] for entry in review_entries] == ["format"]


def test_merge_chunk_phrasing_mode_skips_tags_and_persons(tmp_path):
    chunk = [
        {
            "id": 20,
            "name": "George Washington",
            "text": "George Washington and John Adams hoisted the flag",
            "year": 1776,
            "month": 1,
            "day": 1,
        }
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {
            "id": 20,
            # Names both people, so the fact check stays quiet and the review
            # assertion below isolates the subject entry.
            "event_phrase": "The same age that George Washington was when he and John Adams hoisted the flag.",
            "tags": ["military"],
            "suggested_subject": "John Adams",
        }
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    births_lookup = {"john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30}}
    mock_client = _make_mock_client(tags=[{"id": 2, "name": "military"}])

    with patch("ingest.backfill_event_enrichment.get_client", return_value=mock_client), patch(
        "ingest.backfill_event_enrichment.load_births_lookup", return_value=births_lookup
    ):
        merge_chunk(chunk_path, result_path, review_path=review_path, mode="phrasing")

    mock_client._table_mocks["events"].update.assert_called_once_with(
        {
            "event_phrase": "The same age that George Washington was when he and John Adams hoisted the flag.",
            "reword_prompt_version": REWORD_PROMPT_VERSION,
        }
    )
    mock_client._table_mocks["event_tags"].insert.assert_not_called()
    mock_client._table_mocks["persons"].upsert.assert_not_called()

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["subject"]
