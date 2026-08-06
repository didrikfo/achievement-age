# tests/test_llm_utils.py
import json

from ingest.llm_utils import merge_reworded_chunk


def _empty_births_path(tmp_path):
    path = tmp_path / "births.json"
    path.write_text("[]", encoding="utf-8")
    return path


def test_merge_reworded_chunk_uses_result_and_falls_back(tmp_path):
    chunk = [
        {"name": "George Washington", "text": "hoisted the flag", "year": "1776", "month": 1, "day": 1, "age": 100},
        {"name": "Anton Chekhov", "text": "wrote a play", "year": "1904", "month": 1, "day": 17, "age": 200},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Only the first record comes back reworded; the second is missing entirely.
    result = [
        {**chunk[0], "event_phrase": "he hoisted the flag over Prospect Hill", "tags": ["military"]},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    assert merged_count == 2
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_name = {event["name"]: event for event in merged}

    assert by_name["George Washington"]["event_phrase"] == "he hoisted the flag over Prospect Hill"
    assert by_name["George Washington"]["tags"] == ["military"]
    assert by_name["Anton Chekhov"]["event_phrase"] == "wrote a play"
    assert by_name["Anton Chekhov"]["tags"] == []


def test_merge_reworded_chunk_falls_back_on_missing_result_file(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result_path = tmp_path / "does_not_exist.json"
    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "published notes"
    assert merged[0]["tags"] == []


def test_merge_reworded_chunk_appends_to_existing_file(tmp_path):
    displayable_path = tmp_path / "displayable_events.json"
    displayable_path.write_text(
        json.dumps([{"name": "Existing Person", "text": "did something", "event_phrase": "already here", "tags": []}]),
        encoding="utf-8",
    )

    chunk = [{"name": "New Person", "text": "did something else", "year": "2000", "month": 1, "day": 1, "age": 10}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
    result_path = tmp_path / "does_not_exist.json"

    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert len(merged) == 2
    assert {event["name"] for event in merged} == {"Existing Person", "New Person"}


def test_merge_reworded_chunk_applies_valid_subject_correction(tmp_path):
    chunk = [
        {
            "name": "George Washington",
            "text": "George Washington and John Adams hoisted the flag",
            "year": "1776",
            "month": 1,
            "day": 1,
            "age": 100,
        },
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {**chunk[0], "event_phrase": "he hoisted the flag", "tags": ["military"], "suggested_subject": "John Adams"},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "John Adams", "year": 1735, "month": 10, "day": 30}]), encoding="utf-8"
    )

    displayable_path = tmp_path / "displayable_events.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=births_path,
        review_path=tmp_path / "review.json",
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["name"] == "John Adams"
    assert merged[0]["age"] > 0


def test_merge_reworded_chunk_writes_review_entry_for_invalid_tags(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [{**chunk[0], "event_phrase": "she published her notes", "tags": ["not-a-real-tag"]}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=tmp_path / "displayable_events.json",
        births_path=_empty_births_path(tmp_path),
        review_path=review_path,
    )

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review == [
        {"name": "Ada Lovelace", "text": "published notes", "issue_type": "tags", "detail": "no valid tags in ['not-a-real-tag']"}
    ]
