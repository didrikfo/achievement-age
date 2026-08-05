import json

from ingest.llm_utils import merge_reworded_chunk


def test_merge_reworded_chunk_uses_result_and_falls_back(tmp_path):
    chunk = [
        {"name": "George Washington", "text": "hoisted the flag", "year": "1776", "month": 1, "day": 1, "age": 100},
        {"name": "Anton Chekhov", "text": "wrote a play", "year": "1904", "month": 1, "day": 17, "age": 200},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Only the first record comes back reworded; the second is missing entirely.
    result = [
        {**chunk[0], "display_text": "The same age that George Washington was when he hoisted the flag"},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(chunk_path, result_path, displayable_path=displayable_path)

    assert merged_count == 2
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_name = {event["name"]: event for event in merged}

    assert by_name["George Washington"]["display_text"] == (
        "The same age that George Washington was when he hoisted the flag"
    )
    assert by_name["Anton Chekhov"]["display_text"] == "The same age that Anton Chekhov was when wrote a play"


def test_merge_reworded_chunk_falls_back_on_missing_result_file(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result_path = tmp_path / "does_not_exist.json"
    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(chunk_path, result_path, displayable_path=displayable_path)

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["display_text"] == "The same age that Ada Lovelace was when published notes"


def test_merge_reworded_chunk_appends_to_existing_file(tmp_path):
    displayable_path = tmp_path / "displayable_events.json"
    displayable_path.write_text(
        json.dumps([{"name": "Existing Person", "text": "did something", "display_text": "already here"}]),
        encoding="utf-8",
    )

    chunk = [{"name": "New Person", "text": "did something else", "year": "2000", "month": 1, "day": 1, "age": 10}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
    result_path = tmp_path / "does_not_exist.json"

    merge_reworded_chunk(chunk_path, result_path, displayable_path=displayable_path)

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert len(merged) == 2
    assert {event["name"] for event in merged} == {"Existing Person", "New Person"}
