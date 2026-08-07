"""Seam check across Stage 1 -> Stage 2 -> Stage 3.

Each stage has its own unit tests; this one only asserts that the three fit
together and that events_with_age.json ends up with the right people. The
Stage 2 subagent and the Stage 3 network call are both stubbed.
"""

import json

from ingest import resolve_wikidata
from ingest.match_events import run_stage_one
from ingest.sources import wikidata
from ingest.subject_extraction import merge_subject_chunk, prepare_subject_chunks

EVENTS = [
    # Stage 1: exactly one known person named.
    {"year": 1905, "month": 11, "day": 21, "text": "Albert Einstein published a paper."},
    # Stage 1: "John Smith" is contained in "John Smith Senior" - one person, not two.
    {"year": 1863, "month": 11, "day": 19, "text": "John Smith Senior addressed the assembly."},
    # Stage 1: two known people -> ambiguous -> Stage 2 picks one.
    {"year": 1905, "month": 1, "day": 1, "text": "Marie Curie wrote to Albert Einstein."},
    # Stage 1: nobody known -> Stage 2 names someone we don't have -> Stage 3.
    {"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed the draft."},
    # Stage 1: nobody known -> Stage 2 finds no subject -> review only.
    {"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed at Versailles."},
]

BIRTHS = [
    {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14},
    {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7},
    {"name": "John Smith", "year": 1800, "month": 1, "day": 3},
    {"name": "John Smith Senior", "year": 1820, "month": 5, "day": 9},
]

SUBAGENT_RESULTS = [
    {"text": "Marie Curie wrote to Albert Einstein.", "subject": "Marie Curie"},
    {"text": "Mileva Maric reviewed the draft.", "subject": "Mileva Maric"},
    {"text": "A treaty was signed at Versailles.", "subject": None},
]


def test_events_flow_from_stage_one_through_stage_three(tmp_path, monkeypatch):
    events_path = tmp_path / "historical_events.json"
    events_path.write_text(json.dumps(EVENTS), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps(BIRTHS), encoding="utf-8")

    matched_path = tmp_path / "events_with_age.json"
    pending_path = tmp_path / "subject_pending.json"
    review_path = tmp_path / "matching_review.json"
    wikidata_pending_path = tmp_path / "wikidata_pending.json"

    stage_one = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=matched_path,
        pending_path=pending_path,
        review_path=review_path,
    )
    assert stage_one["matched"] == 2  # Einstein, and John Smith Senior (not ambiguous)
    assert stage_one["ambiguous"] == 1
    assert stage_one["unmatched"] == 2

    chunks = prepare_subject_chunks(
        pending_path=pending_path, chunk_size=100, chunk_dir=tmp_path / "subject_chunks"
    )
    assert len(chunks) == 1
    result_path = chunks[0].with_name("chunk_0000_result.json")
    result_path.write_text(json.dumps(SUBAGENT_RESULTS), encoding="utf-8")

    stage_two = merge_subject_chunk(
        chunks[0],
        result_path,
        births_path=births_path,
        matched_path=matched_path,
        wikidata_pending_path=wikidata_pending_path,
        review_path=review_path,
    )
    assert stage_two["matched"] == 1
    assert stage_two["wikidata_candidate"] == 1
    assert stage_two["no_subject"] == 1

    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1875, "month": 12, "day": 19, "qid": "Q1"
        },
    )
    stage_three = resolve_wikidata.run_stage_three(
        pending_path=wikidata_pending_path,
        matched_path=matched_path,
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )
    assert stage_three["resolved"] == 1

    stored = json.loads(matched_path.read_text(encoding="utf-8"))
    assert {event["name"] for event in stored} == {
        "Albert Einstein", "John Smith Senior", "Marie Curie", "Mileva Maric"
    }
    # One record per event text, each with a computable age.
    assert len({event["text"] for event in stored}) == len(stored) == 4
    for event in stored:
        assert set(event) >= {"year", "month", "day", "text", "name", "age"}
        assert isinstance(event["age"], int) and event["age"] > 0

    # The one event nobody could be found for is in review, not in the output.
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["no_subject"]
    assert review[0]["text"] == "A treaty was signed at Versailles."
