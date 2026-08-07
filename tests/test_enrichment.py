import json

from ingest.enrichment import TAG_TAXONOMY, validate_tags, resolve_subject, build_prompt, build_tag_rows, write_review_entries


def _births_lookup():
    return {
        "john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30},
    }


def test_resolve_subject_returns_none_none_when_no_suggestion():
    event = {"text": "George Washington hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, None, _births_lookup())
    assert correction is None
    assert reason is None


def test_resolve_subject_rejects_name_not_in_text():
    event = {"text": "George Washington hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, "John Adams", _births_lookup())
    assert correction is None
    assert reason == "suggested subject 'John Adams' not found in event text"


def test_resolve_subject_rejects_name_not_in_births_lookup():
    event = {"text": "George Washington and John Doe hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, "John Doe", _births_lookup())
    assert correction is None
    assert reason == "suggested subject 'John Doe' not in known births list"


def test_resolve_subject_returns_correction_when_valid():
    event = {"text": "George Washington and John Adams hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, "John Adams", _births_lookup())
    assert reason is None
    assert correction == {"name": "John Adams", "age_days": correction["age_days"]}
    assert correction["age_days"] > 0


def test_load_births_lookup_indexes_by_normalized_name(tmp_path):
    from ingest.enrichment import load_births_lookup

    births_path = tmp_path / "births.json"
    births_path.write_text(
        '[{"name": "Ada Lovelace", "year": "1815", "month": "12", "day": "10"}]', encoding="utf-8"
    )
    lookup = load_births_lookup(births_path)
    assert lookup == {"ada lovelace": {"name": "Ada Lovelace", "year": 1815, "month": 12, "day": 10}}


def test_validate_tags_keeps_valid_tags_case_insensitive():
    valid, reason = validate_tags(["Science", "MILITARY"])
    assert valid == ["science", "military"]
    assert reason is None


def test_validate_tags_drops_unknown_tags():
    valid, reason = validate_tags(["science", "not-a-real-tag"])
    assert valid == ["science"]
    assert reason is None


def test_validate_tags_caps_at_three_keeping_first_three():
    valid, reason = validate_tags(["science", "military", "law", "arts"])
    assert valid == ["science", "military", "law"]
    assert reason is None


def test_validate_tags_returns_reason_when_none_valid():
    valid, reason = validate_tags(["not-a-real-tag", "also-fake"])
    assert valid == []
    assert reason == "no valid tags in ['not-a-real-tag', 'also-fake']"


def test_validate_tags_handles_empty_input():
    valid, reason = validate_tags([])
    assert valid == []
    assert reason == "no valid tags in []"


def test_build_prompt_substitutes_tag_list():
    prompt = build_prompt()
    assert "{tags}" not in prompt
    for tag in TAG_TAXONOMY:
        assert tag in prompt


def test_write_review_entries_creates_file(tmp_path):
    review_path = tmp_path / "sub" / "review.json"
    write_review_entries([{"issue_type": "tags", "detail": "x"}], review_path)
    assert json.loads(review_path.read_text(encoding="utf-8")) == [{"issue_type": "tags", "detail": "x"}]


def test_write_review_entries_appends_to_existing(tmp_path):
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps([{"issue_type": "tags", "detail": "first"}]), encoding="utf-8")
    write_review_entries([{"issue_type": "subject", "detail": "second"}], review_path)
    result = json.loads(review_path.read_text(encoding="utf-8"))
    assert result == [
        {"issue_type": "tags", "detail": "first"},
        {"issue_type": "subject", "detail": "second"},
    ]


def test_write_review_entries_noop_on_empty_list(tmp_path):
    review_path = tmp_path / "review.json"
    write_review_entries([], review_path)
    assert not review_path.exists()


def test_build_tag_rows_maps_names_to_ids():
    rows = build_tag_rows(42, ["science", "military"], {"science": 5, "military": 9})
    assert rows == [{"event_id": 42, "tag_id": 5}, {"event_id": 42, "tag_id": 9}]


def test_build_tag_rows_skips_unknown_tag_names():
    rows = build_tag_rows(42, ["science", "not-seeded-yet"], {"science": 5})
    assert rows == [{"event_id": 42, "tag_id": 5}]
