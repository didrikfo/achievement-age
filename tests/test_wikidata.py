import json

import pytest

from ingest.sources import wikidata


def test_parse_birth_claim_accepts_day_precision():
    claim = {"mainsnak": {"datavalue": {"value": {"time": "+1879-03-14T00:00:00Z", "precision": 11}}}}

    status, parsed = wikidata.parse_birth_claim(claim)

    assert status == "resolved"
    assert parsed == {"year": 1879, "month": 3, "day": 14}


@pytest.mark.parametrize("precision", [9, 10])
def test_parse_birth_claim_rejects_coarser_precision(precision):
    claim = {"mainsnak": {"datavalue": {"value": {"time": "+1879-01-01T00:00:00Z", "precision": precision}}}}

    status, parsed = wikidata.parse_birth_claim(claim)

    assert status == "insufficient_precision"
    assert parsed is None


def test_parse_birth_claim_rejects_bc_dates():
    claim = {"mainsnak": {"datavalue": {"value": {"time": "-0106-01-03T00:00:00Z", "precision": 11}}}}

    status, _ = wikidata.parse_birth_claim(claim)

    assert status == "insufficient_precision"


def _fake_api(monkeypatch, search, entities):
    def fake_get_json(params):
        if params["action"] == "wbsearchentities":
            return {"search": search}
        return {"entities": entities}

    monkeypatch.setattr(wikidata, "_get_json", fake_get_json)


def test_lookup_resolves_a_single_candidate(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[{"id": "Q937", "label": "Albert Einstein", "description": "physicist"}],
        entities={
            "Q937": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1879-03-14T00:00:00Z", "precision": 11}}}}]
                }
            }
        },
    )

    result = wikidata.lookup_birth_date("Albert Einstein", event_year=1905, cache={})

    assert result["status"] == "resolved"
    assert (result["year"], result["month"], result["day"]) == (1879, 3, 14)
    assert result["qid"] == "Q937"


def test_lookup_reports_not_found_when_search_is_empty(monkeypatch):
    _fake_api(monkeypatch, search=[], entities={})

    result = wikidata.lookup_birth_date("Nobody At All", event_year=1905, cache={})

    assert result["status"] == "not_found"


def test_lookup_narrows_candidates_by_event_year(monkeypatch):
    # Only the second candidate could have been alive in 1905.
    _fake_api(
        monkeypatch,
        search=[
            {"id": "Q1", "label": "John Smith", "description": "medieval monk"},
            {"id": "Q2", "label": "John Smith", "description": "chemist"},
        ],
        entities={
            "Q1": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1400-01-02T00:00:00Z", "precision": 11}}}}]
                }
            },
            "Q2": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1870-05-06T00:00:00Z", "precision": 11}}}}]
                }
            },
        },
    )

    result = wikidata.lookup_birth_date("John Smith", event_year=1905, cache={})

    assert result["status"] == "resolved"
    assert result["qid"] == "Q2"


def test_lookup_reports_ambiguous_when_several_candidates_fit(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[
            {"id": "Q1", "label": "John Smith", "description": "chemist"},
            {"id": "Q2", "label": "John Smith", "description": "poet"},
        ],
        entities={
            "Q1": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1870-05-06T00:00:00Z", "precision": 11}}}}]
                }
            },
            "Q2": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1872-08-09T00:00:00Z", "precision": 11}}}}]
                }
            },
        },
    )

    result = wikidata.lookup_birth_date("John Smith", event_year=1905, cache={})

    assert result["status"] == "ambiguous"


def test_lookup_reports_insufficient_precision(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[{"id": "Q1", "label": "Old Figure", "description": "ruler"}],
        entities={
            "Q1": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1200-01-01T00:00:00Z", "precision": 9}}}}]
                }
            }
        },
    )

    result = wikidata.lookup_birth_date("Old Figure", event_year=1250, cache={})

    assert result["status"] == "insufficient_precision"


def test_lookup_uses_the_cache_and_makes_no_request(monkeypatch):
    def explode(params):
        raise AssertionError("network call made despite a cache hit")

    monkeypatch.setattr(wikidata, "_get_json", explode)
    cache = {"albert einstein": {"status": "not_found", "name": "Albert Einstein"}}

    result = wikidata.lookup_birth_date("Albert Einstein", event_year=1905, cache=cache)

    assert result["status"] == "not_found"


def test_lookup_writes_every_outcome_into_the_cache(monkeypatch):
    _fake_api(monkeypatch, search=[], entities={})
    cache = {}

    wikidata.lookup_birth_date("Nobody At All", event_year=1905, cache=cache)

    assert cache["nobody at all"]["status"] == "not_found"


def test_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "cache.json"
    wikidata.save_cache({"marie curie": {"status": "resolved"}}, path)

    assert wikidata.load_cache(path) == {"marie curie": {"status": "resolved"}}


def test_load_cache_returns_empty_when_missing(tmp_path):
    assert wikidata.load_cache(tmp_path / "absent.json") == {}


def test_load_cache_survives_a_truncated_file(tmp_path):
    # A run interrupted mid-write must not poison every later run.
    path = tmp_path / "cache.json"
    path.write_text('{"marie curie": {"status": "reso', encoding="utf-8")

    assert wikidata.load_cache(path) == {}


def test_save_cache_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "cache.json"
    wikidata.save_cache({"marie curie": {"status": "resolved"}}, path)

    assert [item.name for item in tmp_path.iterdir()] == ["cache.json"]
