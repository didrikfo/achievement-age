from ingest.match_events import classify_event, load_widened_births_lookup
from ingest.name_index import build_name_index

CURIE = {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7}
EINSTEIN = {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}


def _lookup(*people):
    return {
        __import__("core.matching", fromlist=["normalize_name"]).normalize_name(person["name"]): person
        for person in people
    }


def _event(text, year=1905, month=11, day=21):
    return {"year": year, "month": month, "day": day, "text": text}


def test_single_known_name_matches_and_computes_age():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Albert Einstein published a paper."), automaton, lookup)

    assert status == "matched"
    assert payload["name"] == "Albert Einstein"
    # 1879-03-14 to 1905-11-21, verified: (date(1905,11,21) - date(1879,3,14)).days
    assert payload["age"] == 9748
    assert payload["text"] == "Albert Einstein published a paper."


def test_two_known_names_are_ambiguous_not_guessed():
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Marie Curie wrote to Albert Einstein."), automaton, lookup)

    assert status == "ambiguous"
    assert payload == ["albert einstein", "marie curie"]


def test_no_known_name_is_unmatched():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(_event("A treaty was signed in Vienna."), automaton, lookup)

    assert status == "unmatched"


def test_event_before_the_persons_birth_is_implausible():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Albert Einstein appears.", year=1800), automaton, lookup)

    assert status == "implausible"
    assert payload == "Albert Einstein"


def test_non_numeric_year_is_unusable():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(
        {"year": "c. 1300", "month": 1, "day": 1, "text": "Albert Einstein appears."}, automaton, lookup
    )

    assert status == "unusable"


def test_widened_lookup_drops_single_token_names(tmp_path):
    births = tmp_path / "births.json"
    births.write_text(
        '[{"name": "Cicero", "year": -106, "month": 1, "day": 3},'
        ' {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7}]',
        encoding="utf-8",
    )

    lookup = load_widened_births_lookup(births)

    assert "marie curie" in lookup
    assert "cicero" not in lookup


import json

from ingest.match_events import append_matched_events, run_stage_one


def test_append_matched_events_creates_the_file(tmp_path):
    path = tmp_path / "events_with_age.json"

    added = append_matched_events([{"name": "Marie Curie", "text": "she won a prize", "age": 1}], path)

    assert added == 1
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "Marie Curie"


def test_append_matched_events_skips_duplicates_by_name_and_text(tmp_path):
    path = tmp_path / "events_with_age.json"
    existing = [{"name": "Marie Curie", "text": "she won a prize", "age": 1}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    added = append_matched_events(
        [
            {"name": "Marie Curie", "text": "she won a prize", "age": 1},
            {"name": "Albert Einstein", "text": "he published a paper", "age": 2},
        ],
        path,
    )

    assert added == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored) == 2


def test_run_stage_one_splits_events_and_writes_pending(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [
                {"year": 1905, "month": 11, "day": 21, "text": "Albert Einstein published a paper."},
                {"year": 1911, "month": 12, "day": 10, "text": "A committee met in Oslo."},
                {"year": 1905, "month": 1, "day": 1, "text": "Marie Curie wrote to Albert Einstein."},
            ]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps(
            [
                {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14},
                {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7},
            ]
        ),
        encoding="utf-8",
    )
    matched_path = tmp_path / "events_with_age.json"
    pending_path = tmp_path / "subject_pending.json"
    review_path = tmp_path / "matching_review.json"

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=matched_path,
        pending_path=pending_path,
        review_path=review_path,
    )

    assert counts["matched"] == 1
    assert counts["unmatched"] == 1
    assert counts["ambiguous"] == 1

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert len(pending) == 2
    assert {entry["reason"] for entry in pending} == {"unmatched", "ambiguous"}


def test_run_stage_one_records_implausible_matches_for_review(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps([{"year": 1800, "month": 1, "day": 1, "text": "Albert Einstein appears."}]),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}]),
        encoding="utf-8",
    )
    review_path = tmp_path / "matching_review.json"

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        pending_path=tmp_path / "subject_pending.json",
        review_path=review_path,
    )

    assert counts["implausible"] == 1
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "implausible_age"
    assert review[0]["stage"] == "stage_1"
