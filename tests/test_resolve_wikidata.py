import json

from ingest import resolve_wikidata
from ingest.sources import wikidata


def _pending(tmp_path, entries):
    path = tmp_path / "wikidata_pending.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_resolved_lookup_becomes_a_matched_event(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1875, "month": 12, "day": 19, "qid": "Q1"
        },
    )
    pending_path = _pending(
        tmp_path,
        [{"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed it.", "subject": "Mileva Maric"}],
    )
    matched_path = tmp_path / "events_with_age.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=matched_path,
        review_path=tmp_path / "matching_review.json",
        cache_path=tmp_path / "cache.json",
    )

    assert counts["resolved"] == 1
    assert counts["appended"] == 1
    stored = json.loads(matched_path.read_text(encoding="utf-8"))
    assert stored[0]["name"] == "Mileva Maric"
    assert stored[0]["age"] == 10605


def test_ambiguous_lookup_goes_to_review_not_the_matched_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {"status": "ambiguous", "name": name, "candidates": []},
    )
    pending_path = _pending(
        tmp_path, [{"year": 1905, "month": 1, "day": 1, "text": "John Smith spoke.", "subject": "John Smith"}]
    )
    matched_path = tmp_path / "events_with_age.json"
    review_path = tmp_path / "matching_review.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=matched_path,
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )

    assert counts["ambiguous"] == 1
    assert counts["appended"] == 0
    assert not matched_path.exists() or json.loads(matched_path.read_text(encoding="utf-8")) == []
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "ambiguous"
    assert review[0]["stage"] == "stage_3"


def test_insufficient_precision_is_recorded_as_its_own_category(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {"status": "insufficient_precision", "name": name},
    )
    pending_path = _pending(
        tmp_path, [{"year": 1250, "month": 1, "day": 1, "text": "Old Figure ruled.", "subject": "Old Figure"}]
    )
    review_path = tmp_path / "matching_review.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=tmp_path / "events_with_age.json",
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )

    assert counts["insufficient_precision"] == 1
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "insufficient_precision"


def test_implausible_age_is_rejected_even_when_wikidata_resolved(tmp_path, monkeypatch):
    # Born 1879, event in 1800 - the bound must still reject it.
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1879, "month": 3, "day": 14, "qid": "Q1"
        },
    )
    pending_path = _pending(
        tmp_path, [{"year": 1800, "month": 1, "day": 1, "text": "Someone Odd appeared.", "subject": "Someone Odd"}]
    )
    review_path = tmp_path / "matching_review.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=tmp_path / "events_with_age.json",
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )

    assert counts["implausible"] == 1
    assert counts["appended"] == 0
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "implausible_age"


def test_cache_is_persisted_after_the_run(tmp_path, monkeypatch):
    def fake_lookup(name, event_year, cache):
        cache[name.lower()] = {"status": "not_found", "name": name}
        return cache[name.lower()]

    monkeypatch.setattr(wikidata, "lookup_birth_date", fake_lookup)
    pending_path = _pending(
        tmp_path, [{"year": 1905, "month": 1, "day": 1, "text": "Nobody spoke.", "subject": "Nobody"}]
    )
    cache_path = tmp_path / "cache.json"

    resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=tmp_path / "events_with_age.json",
        review_path=tmp_path / "matching_review.json",
        cache_path=cache_path,
    )

    assert json.loads(cache_path.read_text(encoding="utf-8"))["nobody"]["status"] == "not_found"
