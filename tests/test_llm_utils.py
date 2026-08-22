# tests/test_llm_utils.py
import json

from ingest.llm_utils import get_pending_events, merge_reworded_chunk


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
    assert by_name["Anton Chekhov"]["event_phrase"] == "Anton Chekhov was when wrote a play"
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
    assert merged[0]["event_phrase"] == "Ada Lovelace was when published notes"
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
    # The pre-correction name is kept so get_pending_events can tell this record
    # apart from a genuinely new co-subject and stop re-queueing the source record.
    assert merged[0]["original_name"] == "George Washington"


def test_merge_reworded_chunk_writes_review_entry_for_invalid_tags(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {
            **chunk[0],
            "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
            "tags": ["not-a-real-tag"],
        }
    ]
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


def test_merge_reworded_chunk_flags_a_malformed_phrase(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # A subagent that produced the old suffix-only shape instead of a full sentence.
    result = [{**chunk[0], "event_phrase": "she published her notes", "tags": ["science"]}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    displayable_path = tmp_path / "displayable_events.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=review_path,
    )

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["format"]
    # Advisory only - the phrase is still written through unchanged.
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "she published her notes"


def test_merge_reworded_chunk_stamps_the_prompt_version_only_on_subagent_output(tmp_path):
    from ingest.enrichment import REWORD_PROMPT_VERSION

    chunk = [
        {"name": "Ada Lovelace", "text": "Ada Lovelace published notes.", "year": "1843", "month": 1, "day": 1, "age": 50},
        {"name": "Anton Chekhov", "text": "wrote a play", "year": "1904", "month": 1, "day": 17, "age": 200},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Only the first comes back; the second falls back and must stay re-queueable.
    result = [
        {
            **chunk[0],
            "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
            "tags": ["science"],
        }
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "displayable_events.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_name = {event["name"]: event for event in merged}
    assert by_name["Ada Lovelace"]["reword_prompt_version"] == REWORD_PROMPT_VERSION
    assert "reword_prompt_version" not in by_name["Anton Chekhov"]


def test_merge_reworded_chunk_gives_each_co_subject_its_own_event_phrase(tmp_path):
    # One text, two legitimate co-subject records (see match_events.classify_event).
    # Keying the subagent's response by text alone collapses them into one entry and
    # gives both records the same event_phrase.
    text = "Marie Curie wrote to Albert Einstein."
    chunk = [
        {"name": "Marie Curie", "text": text, "year": "1905", "month": 1, "day": 1, "age": 1},
        {"name": "Albert Einstein", "text": text, "year": "1905", "month": 1, "day": 1, "age": 2},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {**chunk[0], "event_phrase": "she wrote to Albert Einstein", "tags": ["science"]},
        {**chunk[1], "event_phrase": "he received a letter from Marie Curie", "tags": ["science"]},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "displayable_events.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_name = {event["name"]: event for event in merged}
    assert by_name["Marie Curie"]["event_phrase"] == "she wrote to Albert Einstein"
    assert by_name["Albert Einstein"]["event_phrase"] == "he received a letter from Marie Curie"


def test_get_pending_events_treats_a_new_co_subject_of_a_processed_text_as_pending(tmp_path):
    # The text is already displayable under one name, but a second co-subject was
    # matched to it later and still needs its own event_phrase.
    text = "Marie Curie wrote to Albert Einstein."
    events_path = tmp_path / "events_with_age.json"
    events_path.write_text(
        json.dumps(
            [
                {"name": "Albert Einstein", "text": text, "year": "1905", "month": 1, "day": 1, "age": 1},
                {"name": "Marie Curie", "text": text, "year": "1905", "month": 1, "day": 1, "age": 2},
            ]
        ),
        encoding="utf-8",
    )

    displayable_path = tmp_path / "displayable_events.json"
    displayable_path.write_text(
        json.dumps(
            [{"name": "Albert Einstein", "text": text, "event_phrase": "he got a letter", "tags": []}]
        ),
        encoding="utf-8",
    )

    processed, pending = get_pending_events(events_path=events_path, displayable_path=displayable_path)

    assert len(processed) == 1
    assert [event["name"] for event in pending] == ["Marie Curie"]


def test_get_pending_events_not_stuck_pending_after_subject_correction(tmp_path):
    # events_with_age.json always holds the original matched name for an event...
    events_path = tmp_path / "events_with_age.json"
    events_path.write_text(
        json.dumps(
            [
                {
                    "name": "George Washington",
                    "text": "George Washington and John Adams hoisted the flag",
                    "year": "1776",
                    "month": 1,
                    "day": 1,
                    "age": 100,
                }
            ]
        ),
        encoding="utf-8",
    )

    # ...but once a subject correction is accepted, displayable_events.json holds the
    # corrected name for the same event (same text). Keying on (name, text) alone would
    # make this look like a different, still-pending event forever, so merge_reworded_chunk
    # stamps the pre-correction name on the record for get_pending_events to recognize.
    displayable_path = tmp_path / "displayable_events.json"
    displayable_path.write_text(
        json.dumps(
            [
                {
                    "name": "John Adams",
                    "original_name": "George Washington",
                    "text": "George Washington and John Adams hoisted the flag",
                    "event_phrase": "he hoisted the flag",
                    "tags": ["military"],
                    "age": 12345,
                }
            ]
        ),
        encoding="utf-8",
    )

    processed, pending = get_pending_events(events_path=events_path, displayable_path=displayable_path)

    assert pending == []
    assert len(processed) == 1
