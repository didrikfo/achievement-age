import json
from unittest.mock import MagicMock, patch

from ingest.migrate_nobel_to_supabase import (
    _to_event_row,
    build_pending_records,
    build_person_rows,
    fetch_all_persons,
    fetch_existing_event_person_dates,
    find_duplicate_day_records,
    find_normalized_name_collisions,
    prepare_pending_records,
    wikipedia_url_updates,
)


def _record(**overrides):
    base = {
        "laureate_id": "6", "name": "Marie Curie", "category": "Chemistry",
        "award_year": 1911, "award_month": 12, "award_day": 10,
        "motivation": "in recognition of her services", "wikipedia_url": "https://en.wikipedia.org/wiki/Marie_Curie",
        "birth_year": 1867, "birth_month": 11, "birth_day": 7,
    }
    return {**base, **overrides}


# --- build_person_rows ---


def test_build_person_rows_is_one_row_per_distinct_name_sorted():
    records = [_record(name="Marie Curie"), _record(name="Albert Einstein"), _record(name="Marie Curie")]
    assert build_person_rows(records) == [{"name": "Albert Einstein"}, {"name": "Marie Curie"}]


# --- find_normalized_name_collisions ---


def test_find_normalized_name_collisions_flags_the_real_j_j_thomson_case():
    # The one real near-duplicate found against the live persons table during
    # design: Nobel's "J.J. Thomson" vs. the existing "J. J. Thomson".
    records = [_record(name="J.J. Thomson")]
    existing_persons = [{"id": 1, "name": "J. J. Thomson", "wikipedia_url": None}]

    collisions = find_normalized_name_collisions(records, existing_persons)

    assert len(collisions) == 1
    assert collisions[0]["name"] == "J.J. Thomson"
    assert "J. J. Thomson" in collisions[0]["detail"]


def test_find_normalized_name_collisions_ignores_exact_matches():
    # An exact-string match (Marie Curie already exists) is meant to reuse the
    # existing row via upsert, not be flagged as a collision.
    records = [_record(name="Marie Curie")]
    existing_persons = [{"id": 1, "name": "Marie Curie", "wikipedia_url": None}]
    assert find_normalized_name_collisions(records, existing_persons) == []


def test_find_normalized_name_collisions_ignores_genuinely_new_names():
    records = [_record(name="Someone New")]
    existing_persons = [{"id": 1, "name": "Marie Curie", "wikipedia_url": None}]
    assert find_normalized_name_collisions(records, existing_persons) == []


# --- wikipedia_url_updates ---


def test_wikipedia_url_updates_fills_in_a_null_existing_value():
    records = [_record(name="Marie Curie", wikipedia_url="https://en.wikipedia.org/wiki/Marie_Curie")]
    name_to_person_id = {"Marie Curie": 7}
    existing_wikipedia_by_id = {7: None}

    updates = wikipedia_url_updates(records, name_to_person_id, existing_wikipedia_by_id)

    assert updates == [(7, "https://en.wikipedia.org/wiki/Marie_Curie")]


def test_wikipedia_url_updates_never_overwrites_an_already_verified_value():
    records = [_record(name="Marie Curie", wikipedia_url="https://en.wikipedia.org/wiki/Marie_Curie_wrong")]
    name_to_person_id = {"Marie Curie": 7}
    existing_wikipedia_by_id = {7: "https://en.wikipedia.org/wiki/Marie_Curie"}

    assert wikipedia_url_updates(records, name_to_person_id, existing_wikipedia_by_id) == []


def test_wikipedia_url_updates_skips_records_with_no_url_to_offer():
    records = [_record(name="Marie Curie", wikipedia_url=None)]
    updates = wikipedia_url_updates(records, {"Marie Curie": 7}, {7: None})
    assert updates == []


# --- find_duplicate_day_records ---


def test_find_duplicate_day_records_blocks_a_real_exact_match():
    # A real collision found against the live DB during design: Jean-Paul
    # Sartre's 1964-10-22 Nobel Prize in Literature is already in `events`
    # (scraped from Wikipedia's "on this day" corpus, as events.id=979).
    # person_id (501) is a synthetic id for this test, distinct from that real
    # event id, to avoid implying it's Sartre's actual persons.id in production.
    records = [_record(name="Jean-Paul Sartre", award_year=1964, award_month=10, award_day=22)]
    name_to_person_id = {"Jean-Paul Sartre": 501}
    existing_event_keys = {(501, 1964, 10, 22): {"id": 979, "text": "Jean-Paul Sartre is awarded..."}}

    keep, blocked = find_duplicate_day_records(records, name_to_person_id, existing_event_keys)

    assert keep == []
    assert len(blocked) == 1
    assert blocked[0][0]["name"] == "Jean-Paul Sartre"
    assert blocked[0][1]["id"] == 979


def test_find_duplicate_day_records_keeps_a_different_day_same_person_same_year():
    # The accepted gap: Yasunari Kawabata's real Wikipedia-scraped event is one
    # day off from the Nobel dataset's date_awarded. Not blocked - a separate,
    # slightly redundant but individually truthful event is accepted.
    # person_id (502) and the existing event's id (957) are both synthetic /
    # illustrative here, kept distinct for the same reason as the test above.
    records = [_record(name="Yasunari Kawabata", award_year=1968, award_month=10, award_day=17)]
    name_to_person_id = {"Yasunari Kawabata": 502}
    existing_event_keys = {(502, 1968, 10, 16): {"id": 957, "text": "Yasunari Kawabata becomes..."}}

    keep, blocked = find_duplicate_day_records(records, name_to_person_id, existing_event_keys)

    assert [r["name"] for r in keep] == ["Yasunari Kawabata"]
    assert blocked == []


def test_find_duplicate_day_records_keeps_records_with_no_resolved_person_id():
    records = [_record(name="Nobody Upserted")]
    keep, blocked = find_duplicate_day_records(records, name_to_person_id={}, existing_event_keys={})
    assert [r["name"] for r in keep] == ["Nobody Upserted"]
    assert blocked == []


# --- build_pending_records ---


def test_build_pending_records_computes_age_days_and_person_id():
    records = [_record(name="Marie Curie", birth_year=1867, birth_month=11, birth_day=7, award_year=1911, award_month=12, award_day=10)]
    pending, implausible = build_pending_records(records, {"Marie Curie": 7})

    assert implausible == []
    assert pending[0]["person_id"] == 7
    assert pending[0]["age_days"] == 16103


def test_build_pending_records_excludes_an_implausible_age():
    # Birth date after the award date - calculate_age still returns a value
    # (a negative one), which must still be rejected by the bound.
    records = [_record(name="Time Traveler", birth_year=1950, birth_month=1, birth_day=1, award_year=1911, award_month=12, award_day=10)]
    pending, implausible = build_pending_records(records, {"Time Traveler": 1})

    assert pending == []
    assert [r["name"] for r in implausible] == ["Time Traveler"]


# --- fetch helpers (paginated Supabase reads) ---


def test_fetch_all_persons_paginates_past_the_page_size():
    mock_client = MagicMock()
    full_page = [{"id": i, "name": f"Person {i}", "wikipedia_url": None} for i in range(1000)]
    short_page = [{"id": 1000, "name": "Person 1000", "wikipedia_url": None}]
    mock_execute = mock_client.table.return_value.select.return_value.range.return_value.execute
    mock_execute.side_effect = [MagicMock(data=full_page), MagicMock(data=short_page)]

    result = fetch_all_persons(mock_client)

    assert len(result) == 1001
    range_calls = mock_client.table.return_value.select.return_value.range.call_args_list
    assert range_calls[0].args == (0, 999)
    assert range_calls[1].args == (1000, 1999)


def test_fetch_existing_event_person_dates_keys_by_person_id_and_date():
    mock_client = MagicMock()
    page = [{"person_id": 979, "year": 1964, "month": 10, "day": 22, "id": 979, "text": "Jean-Paul Sartre..."}]
    mock_client.table.return_value.select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=page)
    ]

    result = fetch_existing_event_person_dates(mock_client)

    assert result == {(979, 1964, 10, 22): {"id": 979, "text": "Jean-Paul Sartre..."}}


def test_fetch_existing_event_person_dates_skips_rows_with_no_person():
    mock_client = MagicMock()
    page = [{"person_id": None, "year": 2000, "month": 1, "day": 1, "id": 5, "text": "unrelated"}]
    mock_client.table.return_value.select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=page)
    ]
    assert fetch_existing_event_person_dates(mock_client) == {}


# --- prepare_pending_records (the phase-1 orchestrator) ---


def _make_mock_client(persons=None, upsert_data=None, event_dates_page=None, upsert_call_count=1):
    persons = persons if persons is not None else []
    table_mocks = {"persons": MagicMock(), "events": MagicMock()}
    table_mocks["persons"].select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=persons), MagicMock(data=[]),
    ]
    # Handle batched upsert calls - return the same data for each batch call
    table_mocks["persons"].upsert.return_value.execute.return_value.data = upsert_data or []
    table_mocks["events"].select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=event_dates_page or []), MagicMock(data=[]),
    ]

    client = MagicMock()
    client.table.side_effect = lambda name: table_mocks[name]
    client._table_mocks = table_mocks
    return client


def test_prepare_pending_records_writes_survivors_to_the_pending_file(tmp_path):
    records = [_record(name="Marie Curie")]
    mock_client = _make_mock_client(
        persons=[], upsert_data=[{"id": 7, "name": "Marie Curie", "wikipedia_url": None}]
    )
    output_path = tmp_path / "nobel_pending.json"

    with patch("ingest.migrate_nobel_to_supabase.get_client", return_value=mock_client):
        counts = prepare_pending_records(
            records,
            output_path=output_path,
            person_review_path=tmp_path / "person_review.json",
            duplicate_review_path=tmp_path / "duplicate_review.json",
            age_review_path=tmp_path / "age_review.json",
        )

    assert counts["pending"] == 1
    pending = json.loads(output_path.read_text(encoding="utf-8"))
    assert pending[0]["name"] == "Marie Curie"
    assert pending[0]["person_id"] == 7
    assert pending[0]["age_days"] == 16103
    mock_client._table_mocks["persons"].upsert.assert_called_once_with(
        [{"name": "Marie Curie"}], on_conflict="name"
    )


def test_prepare_pending_records_excludes_normalized_name_collisions_from_upsert_and_pending(tmp_path):
    """Finding 4.1: Name collisions excluded from persons.upsert AND pending file."""
    records = [
        _record(name="J.J. Thomson"),  # Will collide
        _record(name="Marie Curie"),   # Will succeed
    ]
    existing_persons = [{"id": 1, "name": "J. J. Thomson", "wikipedia_url": None}]
    mock_client = _make_mock_client(
        persons=existing_persons,
        upsert_data=[{"id": 7, "name": "Marie Curie", "wikipedia_url": None}],
    )
    output_path = tmp_path / "nobel_pending.json"

    with patch("ingest.migrate_nobel_to_supabase.get_client", return_value=mock_client):
        counts = prepare_pending_records(
            records,
            output_path=output_path,
            person_review_path=tmp_path / "person_review.json",
            duplicate_review_path=tmp_path / "duplicate_review.json",
            age_review_path=tmp_path / "age_review.json",
        )

    # Verify the colliding name was excluded from upsert
    upsert_call_args = mock_client._table_mocks["persons"].upsert.call_args
    upserted_names = {row["name"] for row in upsert_call_args[0][0]}
    assert "J.J. Thomson" not in upserted_names
    assert "Marie Curie" in upserted_names

    # Verify the colliding name is absent from pending file
    pending = json.loads(output_path.read_text(encoding="utf-8"))
    pending_names = {p["name"] for p in pending}
    assert "J.J. Thomson" not in pending_names
    assert "Marie Curie" in pending_names

    assert counts["name_collisions"] == 1
    assert counts["pending"] == 1


def test_prepare_pending_records_never_overwrites_existing_wikipedia_url(tmp_path):
    """Finding 4.2: persons.update NOT called for already-set wikipedia_url."""
    records = [
        _record(name="Marie Curie", wikipedia_url="https://en.wikipedia.org/wiki/Marie_Curie_new"),
    ]
    existing_persons = [{"id": 7, "name": "Marie Curie", "wikipedia_url": "https://en.wikipedia.org/wiki/Marie_Curie_old"}]
    mock_client = _make_mock_client(
        persons=existing_persons,
        upsert_data=[{"id": 7, "name": "Marie Curie", "wikipedia_url": "https://en.wikipedia.org/wiki/Marie_Curie_old"}],
    )
    output_path = tmp_path / "nobel_pending.json"

    with patch("ingest.migrate_nobel_to_supabase.get_client", return_value=mock_client):
        prepare_pending_records(
            records,
            output_path=output_path,
            person_review_path=tmp_path / "person_review.json",
            duplicate_review_path=tmp_path / "duplicate_review.json",
            age_review_path=tmp_path / "age_review.json",
        )

    # Verify persons.update was never called (no URL should be updated)
    mock_client._table_mocks["persons"].update.assert_not_called()


def test_prepare_pending_records_excludes_duplicate_day_records_from_pending(tmp_path):
    """Finding 4.3: Duplicate-day blocked record absent from pending file."""
    records = [
        _record(name="Jean-Paul Sartre", award_year=1964, award_month=10, award_day=22),
    ]
    mock_client = _make_mock_client(
        persons=[],
        upsert_data=[{"id": 501, "name": "Jean-Paul Sartre", "wikipedia_url": None}],
        event_dates_page=[{"person_id": 501, "year": 1964, "month": 10, "day": 22, "id": 979, "text": "existing"}],
    )
    output_path = tmp_path / "nobel_pending.json"

    with patch("ingest.migrate_nobel_to_supabase.get_client", return_value=mock_client):
        counts = prepare_pending_records(
            records,
            output_path=output_path,
            person_review_path=tmp_path / "person_review.json",
            duplicate_review_path=tmp_path / "duplicate_review.json",
            age_review_path=tmp_path / "age_review.json",
        )

    # Verify the blocked record is absent from pending
    pending = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(pending) == 0
    assert counts["duplicate_day_blocked"] == 1
    assert counts["pending"] == 0


# --- Integration: phase-1 → phrasing → phase-2 handoff ---


def test_pending_record_shape_is_consumable_by_the_phrasing_and_insert_stages(tmp_path):
    """Integration check across the Task 4 / Task 5 file boundary.

    prepare_pending_records's output must have every field prepare_nobel_chunks
    needs (category, for category_display and the tag lookup) and every field
    _to_event_row needs after phrasing (person_id, age_days, award_year/month/day,
    motivation, category, name) - this test fails loudly if either module's
    expectations drift from what the other produces.
    """
    from ingest.nobel_llm_utils import merge_nobel_chunk, prepare_nobel_chunks

    records = [_record(name="Marie Curie")]
    mock_client = _make_mock_client(
        persons=[], upsert_data=[{"id": 7, "name": "Marie Curie", "wikipedia_url": None}]
    )
    pending_path = tmp_path / "nobel_pending.json"

    with patch("ingest.migrate_nobel_to_supabase.get_client", return_value=mock_client):
        prepare_pending_records(
            records,
            output_path=pending_path,
            person_review_path=tmp_path / "person_review.json",
            duplicate_review_path=tmp_path / "duplicate_review.json",
            age_review_path=tmp_path / "age_review.json",
        )

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    chunk_dir = tmp_path / "chunks"
    with patch("ingest.nobel_llm_utils.CHUNK_DIR", chunk_dir):
        chunk_paths = prepare_nobel_chunks(pending, chunk_size=100)

    displayable_path = tmp_path / "nobel_displayable.json"
    merge_nobel_chunk(
        chunk_paths[0],
        tmp_path / "no_result_file.json",  # forces the fallback phrase - no subagent needed for this check
        displayable_path=displayable_path,
        review_path=tmp_path / "phrasing_review.json",
    )

    phrased = json.loads(displayable_path.read_text(encoding="utf-8"))
    row = _to_event_row(phrased[0])

    assert row["name"] == "Marie Curie"
    assert row["person_id"] == 7
    assert row["age_days"] == 16103
    assert row["year"] == 1911
    assert row["event_phrase"] == "Marie Curie was when they won the Nobel Prize in Chemistry."
    assert row["source"] == "nobel_prize_dataset"
