from ingest.backfill_persons_and_phrases import build_person_rows, expected_prefix, strip_prefix


def test_expected_prefix_uses_name():
    assert expected_prefix("George Washington") == "The same age that George Washington was when "


def test_strip_prefix_returns_suffix_when_prefix_matches():
    full_text = "The same age that George Washington was when he hoisted the flag"
    assert strip_prefix("George Washington", full_text) == "he hoisted the flag"


def test_strip_prefix_returns_none_when_prefix_does_not_match():
    assert strip_prefix("George Washington", "He hoisted the flag as a young man") is None


def test_build_person_rows_dedupes_and_sorts():
    rows = build_person_rows(["Ada Lovelace", "George Washington", "Ada Lovelace"])
    assert rows == [{"name": "Ada Lovelace"}, {"name": "George Washington"}]
