import json

from ingest.subject_extraction import build_prompt, merge_subject_chunk, prepare_subject_chunks, route_subject


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
    )

    assert counts["matched"] == 1
    assert counts["wikidata_candidate"] == 1
    assert counts["no_subject"] == 1

    assert json.loads(matched_path.read_text(encoding="utf-8"))[0]["name"] == "Albert Einstein"
    assert json.loads(wikidata_path.read_text(encoding="utf-8"))[0]["subject"] == "Mileva Maric"
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "no_subject"


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
