from ingest.sources import wikipedia


def _fake_api(monkeypatch, api_payload=None, statements_payload=None):
    """Stub the module's single network seam.

    Dispatches on URL: the MediaWiki api.php call versus the Wikidata REST
    statements call.
    """
    def fake_get_json(url, params):
        if "api.php" in url:
            return api_payload or {}
        return statements_payload or {}

    monkeypatch.setattr(wikipedia, "_get_json", fake_get_json)


def test_resolve_titles_returns_url_and_qid_for_a_plain_hit(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "pages": {
                    "736": {
                        "title": "Albert Einstein",
                        "fullurl": "https://en.wikipedia.org/wiki/Albert_Einstein",
                        "pageprops": {"wikibase_item": "Q937"},
                    }
                }
            }
        },
    )

    result = wikipedia.resolve_titles(["Albert Einstein"])

    assert result["Albert Einstein"] == {
        "status": "found",
        "title": "Albert Einstein",
        "url": "https://en.wikipedia.org/wiki/Albert_Einstein",
        "qid": "Q937",
    }


def test_resolve_titles_follows_a_redirect_back_to_the_requested_name(monkeypatch):
    # A redirected page comes back under its canonical title. Keying results by
    # the returned title would silently lose the name that was asked for.
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "redirects": [{"from": "Ada Byron", "to": "Ada Lovelace"}],
                "pages": {
                    "1": {
                        "title": "Ada Lovelace",
                        "fullurl": "https://en.wikipedia.org/wiki/Ada_Lovelace",
                        "pageprops": {"wikibase_item": "Q7259"},
                    }
                },
            }
        },
    )

    result = wikipedia.resolve_titles(["Ada Byron"])

    assert result["Ada Byron"]["status"] == "found"
    assert result["Ada Byron"]["title"] == "Ada Lovelace"


def test_resolve_titles_follows_normalization_then_redirect(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "normalized": [{"from": "ada byron", "to": "Ada byron"}],
                "redirects": [{"from": "Ada byron", "to": "Ada Lovelace"}],
                "pages": {
                    "1": {
                        "title": "Ada Lovelace",
                        "fullurl": "https://en.wikipedia.org/wiki/Ada_Lovelace",
                        "pageprops": {"wikibase_item": "Q7259"},
                    }
                },
            }
        },
    )

    assert wikipedia.resolve_titles(["ada byron"])["ada byron"]["status"] == "found"


def test_resolve_titles_flags_a_disambiguation_page(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "pages": {
                    "20605753": {
                        "title": "John Smith",
                        "fullurl": "https://en.wikipedia.org/wiki/John_Smith",
                        "pageprops": {"wikibase_item": "Q245903", "disambiguation": ""},
                    }
                }
            }
        },
    )

    assert wikipedia.resolve_titles(["John Smith"])["John Smith"]["status"] == "disambiguation"


def test_resolve_titles_flags_a_missing_page(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={"query": {"pages": {"-1": {"title": "Nobody At All", "missing": ""}}}},
    )

    result = wikipedia.resolve_titles(["Nobody At All"])

    assert result["Nobody At All"]["status"] == "missing"
    assert result["Nobody At All"]["url"] is None


def test_resolve_titles_reports_a_name_the_api_never_mentioned(monkeypatch):
    _fake_api(monkeypatch, api_payload={"query": {"pages": {}}})

    assert wikipedia.resolve_titles(["Ghost"])["Ghost"]["status"] == "missing"


def test_resolve_titles_batches_by_fifty(monkeypatch):
    calls = []

    def fake_get_json(url, params):
        calls.append(params["titles"].split("|"))
        return {"query": {"pages": {}}}

    monkeypatch.setattr(wikipedia, "_get_json", fake_get_json)

    names = [f"Person {i}" for i in range(120)]
    result = wikipedia.resolve_titles(names)

    assert [len(batch) for batch in calls] == [50, 50, 20]
    assert len(result) == 120


def test_fetch_birth_year_reads_the_rest_statement_shape(monkeypatch):
    _fake_api(
        monkeypatch,
        statements_payload={
            "P569": [
                {
                    "rank": "normal",
                    "property": {"id": "P569", "data_type": "time"},
                    "value": {"type": "value", "content": {"time": "+1879-03-14T00:00:00Z", "precision": 11}},
                }
            ]
        },
    )

    assert wikipedia.fetch_birth_year("Q937") == 1879


def test_fetch_birth_year_accepts_year_precision(monkeypatch):
    # Only the year is compared, so a year-precision claim is still usable here
    # (unlike wikidata.parse_birth_claim, which needs a day to compute an age).
    _fake_api(
        monkeypatch,
        statements_payload={
            "P569": [{"value": {"type": "value", "content": {"time": "+1452-01-01T00:00:00Z", "precision": 9}}}]
        },
    )

    assert wikipedia.fetch_birth_year("Q762") == 1452


def test_fetch_birth_year_returns_none_for_a_bc_date(monkeypatch):
    _fake_api(
        monkeypatch,
        statements_payload={
            "P569": [{"value": {"type": "value", "content": {"time": "-0106-01-03T00:00:00Z", "precision": 11}}}]
        },
    )

    assert wikipedia.fetch_birth_year("Q1541") is None


def test_fetch_birth_year_returns_none_when_there_is_no_claim(monkeypatch):
    _fake_api(monkeypatch, statements_payload={"P569": []})

    assert wikipedia.fetch_birth_year("Q1") is None


def test_fetch_birth_year_returns_none_for_a_novalue_statement(monkeypatch):
    _fake_api(monkeypatch, statements_payload={"P569": [{"value": {"type": "novalue"}}]})

    assert wikipedia.fetch_birth_year("Q1") is None
