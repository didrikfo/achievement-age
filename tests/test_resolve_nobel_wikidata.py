import json

from ingest import resolve_nobel_wikidata
from ingest.sources import wikidata


def _record(**overrides):
    base = {
        "laureate_id": "234", "name": "Hans Bethe", "category": "Physics",
        "award_year": 1967, "award_month": 10, "award_day": 30,
        "motivation": "for his contributions", "wikipedia_url": None,
        "birth_year": None, "birth_month": None, "birth_day": None,
    }
    return {**base, **overrides}


def test_resolved_lookup_fills_in_the_birth_date(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1906, "month": 7, "day": 2, "qid": "Q57181",
        },
    )
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates(
        [_record()], cache={}, review_path=tmp_path / "review.json"
    )
    assert len(resolved) == 1
    assert (resolved[0]["birth_year"], resolved[0]["birth_month"], resolved[0]["birth_day"]) == (1906, 7, 2)
    assert resolved[0]["name"] == "Hans Bethe"
    assert not (tmp_path / "review.json").exists()


def test_ambiguous_lookup_is_excluded_and_logged_to_review(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata, "lookup_birth_date", lambda name, event_year, cache: {"status": "ambiguous", "name": name}
    )
    review_path = tmp_path / "review.json"
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates([_record()], cache={}, review_path=review_path)

    assert resolved == []
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "ambiguous"
    assert review[0]["name"] == "Hans Bethe"


def test_not_found_lookup_is_excluded_and_logged_to_review(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata, "lookup_birth_date", lambda name, event_year, cache: {"status": "not_found", "name": name}
    )
    review_path = tmp_path / "review.json"
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates([_record()], cache={}, review_path=review_path)

    assert resolved == []
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "not_found"


def test_insufficient_precision_is_excluded_and_logged_to_review(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {"status": "insufficient_precision", "name": name},
    )
    review_path = tmp_path / "review.json"
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates([_record()], cache={}, review_path=review_path)

    assert resolved == []
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "insufficient_precision"


def test_passes_the_award_year_to_the_lookup_for_plausibility_filtering(monkeypatch, tmp_path):
    seen_years = []

    def fake_lookup(name, event_year, cache):
        seen_years.append(event_year)
        return {"status": "not_found", "name": name}

    monkeypatch.setattr(wikidata, "lookup_birth_date", fake_lookup)
    resolve_nobel_wikidata.resolve_missing_birth_dates(
        [_record(award_year=1967)], cache={}, review_path=tmp_path / "review.json"
    )
    assert seen_years == [1967]


def test_resolves_multiple_records_independently(monkeypatch, tmp_path):
    def fake_lookup(name, event_year, cache):
        if name == "Resolvable Person":
            return {"status": "resolved", "name": name, "year": 1900, "month": 1, "day": 1, "qid": "Q1"}
        return {"status": "not_found", "name": name}

    monkeypatch.setattr(wikidata, "lookup_birth_date", fake_lookup)
    records = [_record(name="Resolvable Person"), _record(name="Unresolvable Person")]
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates(
        records, cache={}, review_path=tmp_path / "review.json"
    )
    assert [r["name"] for r in resolved] == ["Resolvable Person"]
