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


def test_lookup_candidate_filter_drops_rejected_candidates_before_fetching_claims(monkeypatch):
    fetched = []
    _fake_api(
        monkeypatch,
        search=[
            {"id": "Q1", "label": "Prince", "description": "son of a monarch"},
            {"id": "Q7542", "label": "Prince", "description": "American musician"},
        ],
        entities={},
    )
    monkeypatch.setattr(
        wikidata,
        "_fetch_entity",
        lambda qid: fetched.append(qid)
        or {"claims": {"P569": [_time_claim("+1958-06-07T00:00:00Z")]}},
    )

    result = wikidata.lookup_birth_date(
        "Prince", event_year=1984, cache={}, candidate_filter=lambda candidate: "musician" in candidate["description"]
    )

    assert fetched == ["Q7542"]
    assert result["status"] == "resolved"
    assert result["qid"] == "Q7542"


def test_lookup_candidate_filter_rejecting_everything_is_not_found(monkeypatch):
    _fake_api(monkeypatch, search=[{"id": "Q1", "label": "ABBA", "description": "Swedish pop group"}], entities={})

    result = wikidata.lookup_birth_date("ABBA", event_year=1974, cache={}, candidate_filter=lambda candidate: False)

    assert result["status"] == "not_found"


def _time_claim(time, precision=11):
    return {"mainsnak": {"datavalue": {"value": {"time": time, "precision": precision}}}}


def test_parse_death_claim_keeps_coarse_precision():
    assert wikidata.parse_death_claim(_time_claim("+1970-10-04T00:00:00Z")) == {
        "year": 1970, "month": 10, "day": 4, "precision": 11,
    }
    assert wikidata.parse_death_claim(_time_claim("+1970-00-00T00:00:00Z", precision=9)) == {
        "year": 1970, "month": None, "day": None, "precision": 9,
    }
    assert wikidata.parse_death_claim(_time_claim("+1900-00-00T00:00:00Z", precision=8)) is None
    assert wikidata.parse_death_claim({"mainsnak": {"snaktype": "somevalue"}}) is None


def test_lookup_reports_the_death_date_of_a_resolved_person(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[{"id": "Q1203", "label": "John Lennon", "description": "English musician"}],
        entities={
            "Q1203": {
                "claims": {
                    "P569": [_time_claim("+1940-10-09T00:00:00Z")],
                    "P570": [_time_claim("+1980-12-08T00:00:00Z")],
                }
            }
        },
    )

    result = wikidata.lookup_birth_date("John Lennon", event_year=1971, cache={})

    assert result["death"] == {"year": 1980, "month": 12, "day": 8, "precision": 11}


def test_lookup_reports_no_death_date_for_a_living_person(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[{"id": "Q26876", "label": "Taylor Swift", "description": "American singer-songwriter"}],
        entities={"Q26876": {"claims": {"P569": [_time_claim("+1989-12-13T00:00:00Z")]}}},
    )

    assert wikidata.lookup_birth_date("Taylor Swift", event_year=2022, cache={})["death"] is None


def test_lookup_exact_name_accepts_the_label_or_any_english_alias(monkeypatch):
    # The search reports only its best-matching term: Beyonce matched her full
    # name, Brandy matched the label "Brandy Norwood" - neither equals the credit.
    _fake_api(
        monkeypatch,
        search=[
            {"id": "Q36153", "label": "Beyoncé", "match": {"text": "Beyoncé Giselle Knowles Carter"}},
            {"id": "Q690974", "label": "Brandy Norwood", "match": {"text": "Brandy Norwood"}},
        ],
        entities={
            "Q36153": {"claims": {"P569": [_time_claim("+1981-09-04T00:00:00Z")]}},
            "Q690974": {
                "claims": {"P569": [_time_claim("+1979-02-11T00:00:00Z")]},
                "aliases": {"en": [{"value": "Brandy"}]},
            },
        },
    )

    beyonce = wikidata.lookup_birth_date("Beyonce", event_year=2003, cache={}, require_exact_name=True)
    brandy = wikidata.lookup_birth_date("Brandy", event_year=1998, cache={}, require_exact_name=True)

    assert (beyonce["status"], beyonce["qid"]) == ("resolved", "Q36153")
    assert (brandy["status"], brandy["qid"]) == ("resolved", "Q690974")


def test_lookup_exact_name_rejects_a_prefix_match(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[{"id": "Q1", "label": "Prince Buster", "match": {"text": "Prince Buster"}}],
        entities={"Q1": {"claims": {"P569": [_time_claim("+1938-05-24T00:00:00Z")]}}},
    )

    assert wikidata.lookup_birth_date("Prince", 1984, cache={}, require_exact_name=True)["status"] == "not_found"


def test_lookup_ambiguous_result_lists_each_candidates_dates(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[
            {"id": "Q155700", "label": "Chris Brown", "description": "American singer and rapper"},
            {"id": "Q115390006", "label": "Chris Brown", "description": "American singer, CCM artist"},
        ],
        entities={
            "Q155700": {
                "claims": {"P569": [_time_claim("+1989-05-05T00:00:00Z")]},
                "sitelinks": {"enwiki": {}, "dewiki": {}},
            },
            "Q115390006": {"claims": {"P569": [_time_claim("+1960-01-01T00:00:00Z")]}},
        },
    )

    result = wikidata.lookup_birth_date("Chris Brown", event_year=2008, cache={})

    assert result["status"] == "ambiguous"
    assert [(c["qid"], c["year"], c["death"], c["sitelinks"]) for c in result["candidates"]] == [
        ("Q155700", 1989, None, 2), ("Q115390006", 1960, None, 0),
    ]
