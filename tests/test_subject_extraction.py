import json

from ingest.subject_extraction import (
    PROMPT_VERSION,
    build_prompt,
    load_no_subject_cache,
    merge_subject_chunk,
    prepare_subject_chunks,
    route_subject,
    save_no_subject_cache,
)


def test_build_prompt_describes_the_subject_field():
    prompt = build_prompt()
    assert "verbatim as it appears in `text`" in prompt
    assert "`subject`" in prompt


def test_prepare_subject_chunks_splits_by_chunk_size(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text(
        json.dumps([{"text": f"Event {index}", "reason": "unmatched"} for index in range(5)]),
        encoding="utf-8",
    )
    chunk_dir = tmp_path / "subject_chunks"

    paths = prepare_subject_chunks(pending_path=pending_path, chunk_size=2, chunk_dir=chunk_dir)

    assert len(paths) == 3
    assert paths[0].name == "chunk_0000.json"
    assert len(json.loads(paths[0].read_text(encoding="utf-8"))) == 2
    assert len(json.loads(paths[2].read_text(encoding="utf-8"))) == 1


def test_prepare_subject_chunks_handles_an_empty_queue(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text("[]", encoding="utf-8")

    paths = prepare_subject_chunks(pending_path=pending_path, chunk_dir=tmp_path / "chunks")

    assert paths == []


EINSTEIN = {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}
LOOKUP = {"albert einstein": EINSTEIN}
EVENT = {"year": 1905, "month": 11, "day": 21, "text": "Albert Einstein published a paper."}


def test_route_subject_matches_a_known_person():
    status, payload = route_subject(EVENT, "Albert Einstein", LOOKUP)

    assert status == "matched"
    assert payload["name"] == "Albert Einstein"
    assert payload["age"] == 9748


def test_route_subject_sends_an_unknown_but_real_name_to_wikidata():
    event = {"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed the draft."}

    status, payload = route_subject(event, "Mileva Maric", LOOKUP)

    assert status == "wikidata_candidate"
    assert payload == "Mileva Maric"


def test_route_subject_rejects_a_name_absent_from_the_text():
    status, payload = route_subject(EVENT, "Niels Bohr", LOOKUP)

    assert status == "rejected"
    assert "not found in event text" in payload


def test_route_subject_accepts_null_as_no_subject():
    status, _ = route_subject(EVENT, None, LOOKUP)

    assert status == "no_subject"


def test_merge_subject_chunk_routes_each_outcome(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(
        json.dumps(
            [
                EVENT,
                {"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed the draft."},
                {"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed at Versailles."},
            ]
        ),
        encoding="utf-8",
    )
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps(
            [
                {"text": "Albert Einstein published a paper.", "subject": "Albert Einstein"},
                {"text": "Mileva Maric reviewed the draft.", "subject": "Mileva Maric"},
                {"text": "A treaty was signed at Versailles.", "subject": None},
            ]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")

    matched_path = tmp_path / "events_with_age.json"
    wikidata_path = tmp_path / "wikidata_pending.json"
    review_path = tmp_path / "matching_review.json"

    counts = merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=matched_path,
        wikidata_pending_path=wikidata_path,
        review_path=review_path,
        no_subject_cache_path=tmp_path / "no_subject_cache.json",
    )

    assert counts["matched"] == 1
    assert counts["wikidata_candidate"] == 1
    assert counts["no_subject"] == 1

    assert json.loads(matched_path.read_text(encoding="utf-8"))[0]["name"] == "Albert Einstein"
    assert json.loads(wikidata_path.read_text(encoding="utf-8"))[0]["subject"] == "Mileva Maric"
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "no_subject"


def test_stage_2_review_entries_carry_the_attempted_name(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps([EVENT]), encoding="utf-8")
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps([{"text": EVENT["text"], "subject": "Niels Bohr"}]), encoding="utf-8"
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")
    review_path = tmp_path / "matching_review.json"

    merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=review_path,
    )

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "rejected"
    assert review[0]["name"] == "Niels Bohr"


def test_merging_the_same_chunk_twice_does_not_duplicate_the_queues(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(
        json.dumps(
            [
                {"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed the draft."},
                {"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed at Versailles."},
            ]
        ),
        encoding="utf-8",
    )
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps(
            [
                {"text": "Mileva Maric reviewed the draft.", "subject": "Mileva Maric"},
                {"text": "A treaty was signed at Versailles.", "subject": None},
            ]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")
    wikidata_path = tmp_path / "wikidata_pending.json"
    review_path = tmp_path / "matching_review.json"
    kwargs = dict(
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=wikidata_path,
        review_path=review_path,
        no_subject_cache_path=tmp_path / "no_subject_cache.json",
    )

    merge_subject_chunk(chunk_path, result_path, **kwargs)
    merge_subject_chunk(chunk_path, result_path, **kwargs)

    assert len(json.loads(wikidata_path.read_text(encoding="utf-8"))) == 1
    assert len(json.loads(review_path.read_text(encoding="utf-8"))) == 1


def test_merge_subject_chunk_survives_a_missing_result_file(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps([EVENT]), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")

    counts = merge_subject_chunk(
        chunk_path,
        tmp_path / "does_not_exist.json",
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
    )

    assert counts["no_subject"] == 1
    assert counts["matched"] == 0


def test_merge_subject_chunk_survives_a_wrong_shaped_result_file(tmp_path):
    # Valid JSON, but a list of strings rather than a list of result objects: not a
    # JSONDecodeError, so it has to be caught on shape, not on parsing.
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps([EVENT]), encoding="utf-8")
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(["Albert Einstein published a paper.", 42]), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    counts = merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    assert counts["no_subject"] == 1
    assert counts["matched"] == 0
    # A technical failure is not a confirmed "nobody in this text" verdict.
    assert load_no_subject_cache(cache_path) == {}


def test_merge_subject_chunk_survives_a_result_file_that_is_not_a_list(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps([EVENT]), encoding="utf-8")
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps({"text": EVENT["text"], "subject": "Albert Einstein"}), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    counts = merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    assert counts["no_subject"] == 1
    assert load_no_subject_cache(cache_path) == {}


def test_load_no_subject_cache_returns_empty_for_valid_json_that_is_not_a_dict(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(["A treaty was signed."]), encoding="utf-8")

    assert load_no_subject_cache(path) == {}


def test_load_no_subject_cache_returns_empty_when_missing(tmp_path):
    assert load_no_subject_cache(tmp_path / "absent.json") == {}


def test_no_subject_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "cache.json"
    save_no_subject_cache({"A treaty was signed.": 1}, path)

    assert load_no_subject_cache(path) == {"A treaty was signed.": 1}


def test_load_no_subject_cache_survives_a_truncated_file(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert load_no_subject_cache(path) == {}


def test_save_no_subject_cache_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "cache.json"
    save_no_subject_cache({"x": 1}, path)

    assert not path.with_name(path.name + ".tmp").exists()


def test_prepare_subject_chunks_skips_a_text_already_confirmed_at_the_current_prompt_version(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text(
        json.dumps(
            [
                {"text": "Already checked.", "reason": "unmatched"},
                {"text": "Never checked.", "reason": "unmatched"},
            ]
        ),
        encoding="utf-8",
    )
    cache_path = tmp_path / "no_subject_cache.json"
    save_no_subject_cache({"Already checked.": PROMPT_VERSION}, cache_path)

    paths = prepare_subject_chunks(
        pending_path=pending_path, chunk_dir=tmp_path / "chunks", cache_path=cache_path
    )

    chunked = json.loads(paths[0].read_text(encoding="utf-8"))
    assert [entry["text"] for entry in chunked] == ["Never checked."]


def test_prepare_subject_chunks_includes_a_text_cached_at_a_stale_prompt_version(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text(
        json.dumps([{"text": "Checked under an older prompt.", "reason": "unmatched"}]),
        encoding="utf-8",
    )
    cache_path = tmp_path / "no_subject_cache.json"
    save_no_subject_cache({"Checked under an older prompt.": PROMPT_VERSION - 1}, cache_path)

    paths = prepare_subject_chunks(
        pending_path=pending_path, chunk_dir=tmp_path / "chunks", cache_path=cache_path
    )

    chunked = json.loads(paths[0].read_text(encoding="utf-8"))
    assert len(chunked) == 1


def test_merge_subject_chunk_caches_a_confirmed_no_subject_result(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(
        json.dumps([{"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed."}]),
        encoding="utf-8",
    )
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps([{"text": "A treaty was signed.", "subject": None}]), encoding="utf-8"
    )
    births_path = tmp_path / "births.json"
    births_path.write_text("[]", encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    cache = load_no_subject_cache(cache_path)
    assert cache["A treaty was signed."] == PROMPT_VERSION


def test_merge_subject_chunk_does_not_cache_a_rejected_suggestion(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(
        json.dumps([{"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed."}]),
        encoding="utf-8",
    )
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps([{"text": "A treaty was signed.", "subject": "Someone Not In The Text"}]),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text("[]", encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    assert load_no_subject_cache(cache_path) == {}


def test_merge_subject_chunk_does_not_cache_when_the_result_file_is_missing(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps([EVENT]), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    merge_subject_chunk(
        chunk_path,
        tmp_path / "does_not_exist.json",
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    assert load_no_subject_cache(cache_path) == {}


def test_build_prompt_explains_named_after_and_anniversary_references():
    prompt = build_prompt()
    assert "named after" in prompt.lower()
    assert "anniversary" in prompt.lower()


def test_build_prompt_asks_for_the_persons_full_name():
    prompt = build_prompt()
    assert "full name" in prompt.lower()


def test_build_prompt_documents_the_possible_reference_reason():
    prompt = build_prompt()
    assert "possible_reference" in prompt


def test_prompt_version_was_bumped_for_the_commemorative_instructions():
    from ingest.subject_extraction import PROMPT_VERSION

    assert PROMPT_VERSION == 2
