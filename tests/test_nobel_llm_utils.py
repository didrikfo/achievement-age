import json

from ingest.nobel_llm_utils import (
    NOBEL_REWORD_PROMPT_VERSION,
    merge_nobel_chunk,
    prepare_nobel_chunks,
)


def _record(**overrides):
    base = {
        "laureate_id": "6", "name": "Marie Curie", "category": "Chemistry",
        "award_year": 1911, "award_month": 12, "award_day": 10,
        "motivation": "in recognition of her services", "wikipedia_url": None,
        "birth_year": 1867, "birth_month": 11, "birth_day": 7,
        "person_id": 42, "age_days": 16103,
    }
    return {**base, **overrides}


def test_prepare_nobel_chunks_adds_category_display(tmp_path, monkeypatch):
    import ingest.nobel_llm_utils as nobel_llm_utils

    monkeypatch.setattr(nobel_llm_utils, "CHUNK_DIR", tmp_path)
    paths = prepare_nobel_chunks([_record()], chunk_size=100)

    assert len(paths) == 1
    chunk = json.loads(paths[0].read_text(encoding="utf-8"))
    assert chunk[0]["category_display"] == "the Nobel Prize in Chemistry"
    assert chunk[0]["name"] == "Marie Curie"
    assert chunk[0]["person_id"] == 42  # carried through untouched


def test_prepare_nobel_chunks_splits_at_chunk_size(tmp_path, monkeypatch):
    import ingest.nobel_llm_utils as nobel_llm_utils

    monkeypatch.setattr(nobel_llm_utils, "CHUNK_DIR", tmp_path)
    records = [_record(laureate_id=str(i), award_year=1900 + i) for i in range(5)]
    paths = prepare_nobel_chunks(records, chunk_size=2)

    assert len(paths) == 3
    assert len(json.loads(paths[0].read_text(encoding="utf-8"))) == 2
    assert len(json.loads(paths[2].read_text(encoding="utf-8"))) == 1


def test_merge_nobel_chunk_uses_result_and_assigns_the_deterministic_tag(tmp_path):
    chunk = [_record()]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [{"laureate_id": "6", "name": "Marie Curie", "award_year": 1911, "event_phrase": "Marie Curie was when she won the Nobel Prize in Chemistry for her work on radioactivity."}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "nobel_displayable.json"
    merged_count = merge_nobel_chunk(
        chunk_path, result_path, displayable_path=displayable_path, review_path=tmp_path / "review.json"
    )

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "Marie Curie was when she won the Nobel Prize in Chemistry for her work on radioactivity."
    assert merged[0]["tags"] == ["science"]
    assert merged[0]["reword_prompt_version"] == NOBEL_REWORD_PROMPT_VERSION
    assert merged[0]["person_id"] == 42
    assert merged[0]["age_days"] == 16103


def test_merge_nobel_chunk_falls_back_on_missing_result_file(tmp_path):
    chunk = [_record()]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    displayable_path = tmp_path / "nobel_displayable.json"
    merged_count = merge_nobel_chunk(
        chunk_path,
        tmp_path / "does_not_exist.json",
        displayable_path=displayable_path,
        review_path=tmp_path / "review.json",
    )

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "Marie Curie was when they won the Nobel Prize in Chemistry."
    assert merged[0]["tags"] == ["science"]
    # Deliberately unstamped so a later rerun re-queues it, matching
    # ingest.llm_utils._fallback_event_phrase's convention.
    assert "reword_prompt_version" not in merged[0]


def test_merge_nobel_chunk_gives_each_repeat_award_its_own_phrase(tmp_path):
    # John Bardeen won Physics twice (1956 and 1972) - (laureate_id, category)
    # would collide between his two awards, so the key must include award_year.
    chunk = [
        _record(laureate_id="66", name="John Bardeen", category="Physics", award_year=1956, person_id=1, age_days=100),
        _record(laureate_id="66", name="John Bardeen", category="Physics", award_year=1972, person_id=1, age_days=200),
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {"laureate_id": "66", "name": "John Bardeen", "award_year": 1956, "event_phrase": "his 1956 phrase."},
        {"laureate_id": "66", "name": "John Bardeen", "award_year": 1972, "event_phrase": "his 1972 phrase."},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "nobel_displayable.json"
    merge_nobel_chunk(chunk_path, result_path, displayable_path=displayable_path, review_path=tmp_path / "review.json")

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_year = {m["award_year"]: m["event_phrase"] for m in merged}
    assert by_year == {1956: "his 1956 phrase.", 1972: "his 1972 phrase."}


def test_merge_nobel_chunk_flags_a_malformed_phrase(tmp_path):
    chunk = [_record()]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Old suffix-only shape, not name-onward.
    result = [{"laureate_id": "6", "name": "Marie Curie", "award_year": 1911, "event_phrase": "she won the prize"}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    merge_nobel_chunk(chunk_path, result_path, displayable_path=tmp_path / "nobel_displayable.json", review_path=review_path)

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["format"]


def test_merge_nobel_chunk_appends_to_existing_displayable_file(tmp_path):
    displayable_path = tmp_path / "nobel_displayable.json"
    displayable_path.write_text(
        json.dumps([{"laureate_id": "1", "name": "Existing Laureate", "award_year": 1950, "event_phrase": "already here", "tags": ["science"], "category": "Physics"}]),
        encoding="utf-8",
    )

    chunk = [_record(laureate_id="6", award_year=1911)]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    merge_nobel_chunk(
        chunk_path, tmp_path / "does_not_exist.json", displayable_path=displayable_path, review_path=tmp_path / "review.json"
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert len(merged) == 2
    assert {m["name"] for m in merged} == {"Existing Laureate", "Marie Curie"}
