from ingest.enrichment import validate_tags


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
