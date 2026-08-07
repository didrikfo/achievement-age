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
    assert payload["implausible"] == []
    assert len(payload["matched"]) == 1
    record = payload["matched"][0]
    assert record["name"] == "Albert Einstein"
    # 1879-03-14 to 1905-11-21, verified: (date(1905,11,21) - date(1879,3,14)).days
    assert record["age"] == 9748
    assert record["text"] == "Albert Einstein published a paper."


def test_two_known_names_both_become_separate_matched_records():
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Marie Curie wrote to Albert Einstein."), automaton, lookup)

    assert status == "matched"
    assert payload["implausible"] == []
    names = sorted(record["name"] for record in payload["matched"])
    assert names == ["Albert Einstein", "Marie Curie"]


def test_multi_candidate_event_can_partially_fail_the_plausibility_bound():
    # 1870 predates Einstein's 1879 birth (a negative, implausible age) while
    # Curie (born 1867) already existed and gets a plausible age.
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("Marie Curie wrote to Albert Einstein.", year=1870), automaton, lookup
    )

    assert status == "matched"
    assert [record["name"] for record in payload["matched"]] == ["Marie Curie"]
    assert payload["implausible"] == ["Albert Einstein"]


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
    assert payload == ["Albert Einstein"]


def test_non_numeric_year_is_unusable():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(
        {"year": "c. 1300", "month": 1, "day": 1, "text": "Albert Einstein appears."}, automaton, lookup
    )

    assert status == "unusable"


def test_named_after_phrase_routes_to_possible_reference_with_a_plausible_single_match():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("Einstein Elementary School, named after Albert Einstein, opens its doors."),
        automaton,
        lookup,
    )

    assert status == "possible_reference"
    assert payload == ["albert einstein"]


def test_named_after_phrase_routes_to_possible_reference_with_no_known_candidates():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("A new bridge, named after a local poet, opens to traffic."), automaton, lookup
    )

    assert status == "possible_reference"
    assert payload == []


def test_named_after_phrase_routes_to_possible_reference_with_multiple_known_candidates():
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("The Einstein-Curie Prize, named after Albert Einstein and Marie Curie, is awarded."),
        automaton,
        lookup,
    )

    assert status == "possible_reference"
    assert sorted(payload) == ["albert einstein", "marie curie"]


def test_anniversary_phrase_also_routes_to_possible_reference():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(
        _event("On the 10th anniversary of Albert Einstein's famous paper, a conference is held."),
        automaton,
        lookup,
    )

    assert status == "possible_reference"


def test_ordinary_text_without_a_commemorative_phrase_is_unaffected():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(_event("Albert Einstein published a paper."), automaton, lookup)

    assert status == "matched"


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


def test_widened_lookup_drops_names_two_different_people_share(tmp_path):
    # Two "John Smith" records with genuinely different birth dates: the name is
    # unresolvable, so it must become unknown rather than silently pick a winner.
    births = tmp_path / "births.json"
    births.write_text(
        '[{"name": "John Smith", "year": 1800, "month": 1, "day": 3},'
        ' {"name": "John Smith", "year": 1920, "month": 5, "day": 9},'
        ' {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7},'
        ' {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7}]',
        encoding="utf-8",
    )

    lookup = load_widened_births_lookup(births)

    assert "john smith" not in lookup
    # A name repeated with the *same* date is not a collision - it still resolves.
    assert lookup["marie curie"]["year"] == 1867


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
            ]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}]),
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

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert len(pending) == 1
    assert pending[0]["reason"] == "unmatched"

    matched = json.loads(matched_path.read_text(encoding="utf-8"))
    assert matched[0]["name"] == "Albert Einstein"


def test_run_stage_one_auto_matches_every_candidate_in_a_multi_person_event(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [{"year": 1905, "month": 1, "day": 1, "text": "Marie Curie wrote to Albert Einstein."}]
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

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=matched_path,
        pending_path=tmp_path / "subject_pending.json",
        review_path=tmp_path / "matching_review.json",
    )

    assert counts["matched"] == 1  # one EVENT classified matched...
    assert counts["appended"] == 2  # ...producing two separate person-records

    matched = json.loads(matched_path.read_text(encoding="utf-8"))
    names = sorted(record["name"] for record in matched)
    assert names == ["Albert Einstein", "Marie Curie"]


def test_run_stage_one_routes_named_after_text_to_possible_reference(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [{"year": 1950, "month": 1, "day": 1, "text": "A new school, named after Albert Einstein, opens."}]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}]),
        encoding="utf-8",
    )
    pending_path = tmp_path / "subject_pending.json"

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        pending_path=pending_path,
        review_path=tmp_path / "matching_review.json",
    )

    assert counts["possible_reference"] == 1
    assert counts["appended"] == 0

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending[0]["reason"] == "possible_reference"
    assert pending[0]["candidates"] == ["albert einstein"]


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


def test_rerunning_stage_one_does_not_duplicate_review_entries(tmp_path):
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
    kwargs = dict(
        events_path=events_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        pending_path=tmp_path / "subject_pending.json",
        review_path=review_path,
    )

    run_stage_one(**kwargs)
    run_stage_one(**kwargs)

    assert len(json.loads(review_path.read_text(encoding="utf-8"))) == 1


def test_same_text_under_a_different_name_is_reviewed_not_appended(tmp_path):
    path = tmp_path / "events_with_age.json"
    review_path = tmp_path / "matching_review.json"
    path.write_text(
        json.dumps([{"name": "Marie Curie", "text": "she won a prize", "age": 1}]),
        encoding="utf-8",
    )

    added = append_matched_events(
        [{"name": "Pierre Curie", "text": "she won a prize", "age": 2}],
        path,
        review_path=review_path,
    )

    assert added == 0
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "conflicting_subject"
    assert review[0]["name"] == "Pierre Curie"
