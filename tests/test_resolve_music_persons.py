from ingest.resolve_music_persons import (
    build_music_births_lookup,
    check_resolved_record,
    expand_to_people,
    musician_candidate_filter,
    resolve_from_births,
    resolve_people,
)


def _person(name="Ray Charles", title="Georgia on My Mind", year=1960, month=11, day=14, expected=None, **extra):
    return {
        "kind": "hot100_number_one", "record_key": f"hot100:{title}:{name}", "title": title, "credit": name,
        "names": [name], "name": name, "year": year, "month": month, "day": day,
        "expected_age_years": expected, **extra,
    }


# --- births file ---


def test_births_lookup_keeps_only_musicians():
    # The births file's only "Michael Jackson" isn't the singer.
    births = [
        {"name": "Michael Jackson", "year": "1967", "month": 1, "day": 1, "text": "English footballer"},
        {"name": "Elton John", "year": "1947", "month": 3, "day": 25, "text": "English singer-songwriter and pianist"},
    ]

    lookup = build_music_births_lookup(births)

    assert resolve_from_births("Michael Jackson", lookup)["status"] == "not_found"
    assert resolve_from_births("Elton John", lookup) == {
        "status": "resolved", "name": "Elton John", "year": 1947, "month": 3, "day": 25,
    }


def test_births_lookup_reports_two_different_musicians_of_one_name_as_ambiguous():
    births = [
        {"name": "John Smith", "year": "1950", "month": 1, "day": 1, "text": "American singer"},
        {"name": "John Smith", "year": "1970", "month": 2, "day": 2, "text": "British guitarist"},
    ]

    assert resolve_from_births("John Smith", build_music_births_lookup(births))["status"] == "ambiguous"


# --- Wikidata candidate filter ---


def test_musician_candidate_filter_needs_an_exact_name_match_and_a_musician_description():
    accept = musician_candidate_filter("Prince")

    assert accept({"label": "Prince", "description": "American singer-songwriter (1958-2016)",
                   "match": {"type": "label", "text": "Prince"}})
    assert not accept({"label": "Prince", "description": "son of a monarch", "match": {"text": "Prince"}})
    # wbsearchentities matches prefixes: "Prince Buster" is not "Prince".
    assert not accept({"label": "Prince Buster", "description": "Jamaican singer", "match": {"text": "Prince Buster"}})


def test_musician_candidate_filter_accepts_an_alias_match():
    accept = musician_candidate_filter("2Pac")
    assert accept({"label": "Tupac Shakur", "description": "American rapper", "match": {"type": "alias", "text": "2Pac"}})


# --- per-record checks ---


def test_check_resolved_record_rejects_an_implausibly_young_age():
    record = _person(birth_year=1958, birth_month=1, birth_day=1, year=1960)
    age_days, reason = check_resolved_record(record)
    assert age_days is None
    assert "implausible age" in reason


def test_check_resolved_record_tolerates_a_two_year_disagreement_but_not_more():
    record = _person(name="Jan Hammer", birth_year=1948, birth_month=4, birth_day=17,
                     year=1985, month=11, day=9, expected=35.0)
    assert check_resolved_record(record)[1] is None  # 37 vs. the dataset's 35
    assert "disagrees" in check_resolved_record({**record, "expected_age_years": 34.0})[1]


# --- expand_to_people ---


def test_expand_to_people_gives_each_credited_person_a_record_and_sets_groups_aside():
    duo = {"record_key": "a", "names": ["Bruno Mars", "Lady Gaga"]}
    group = {"record_key": "b", "names": []}

    people, groups = expand_to_people([duo, group])

    assert [person["name"] for person in people] == ["Bruno Mars", "Lady Gaga"]
    assert all(person["names"] == ["Bruno Mars", "Lady Gaga"] for person in people)
    assert groups == [group]


# --- resolve_people ---

# The births file's "Ray Charles" (born 1918) is not the singer (born 1930).
WRONG_RAY_CHARLES = {"ray charles": [{"year": 1918, "month": 9, "day": 13}]}


def _wikidata(results):
    calls = []

    def lookup(name, event_year):
        calls.append((name, event_year))
        return results.get(name, {"status": "not_found", "name": name})

    lookup.calls = calls
    return lookup


def test_resolve_people_rejects_a_contradicted_births_file_date_for_every_record_of_that_name():
    single = _person(expected=30.0)
    album = _person(title="The Genius Hits the Road", year=1960, month=12, day=5,
                    kind="billboard200_number_one")  # no front_person_age to check

    resolved, review = resolve_people([single, album], WRONG_RAY_CHARLES, lookup_wikidata=None)

    assert resolved == []
    assert review[0]["issue_type"] == "rejected_birth_date"
    assert "all 2 record(s)" in review[0]["detail"]


def test_resolve_people_falls_through_to_wikidata_when_the_births_file_is_contradicted():
    single = _person(expected=30.0)
    wikidata = _wikidata({"Ray Charles": {"status": "resolved", "year": 1930, "month": 9, "day": 23, "qid": "Q544387"}})

    resolved, _ = resolve_people([single], WRONG_RAY_CHARLES, lookup_wikidata=wikidata)

    assert len(resolved) == 1
    assert resolved[0]["birth_source"] == "wikidata"
    assert resolved[0]["wikidata_qid"] == "Q544387"
    assert resolved[0]["age_days"] == 11010


def test_resolve_people_asks_wikidata_once_per_name_with_the_earliest_event_year():
    records = [_person(name="Adele", title="Hello", year=2015), _person(name="Adele", title="21", year=2011)]
    wikidata = _wikidata({"Adele": {"status": "resolved", "year": 1988, "month": 5, "day": 5}})

    resolved, _ = resolve_people(records, {}, lookup_wikidata=wikidata)

    assert wikidata.calls == [("Adele", 2011)]
    assert len(resolved) == 2


def test_resolve_people_reports_a_name_nobody_can_resolve():
    resolved, review = resolve_people([_person(name="Nobody Known")], {}, lookup_wikidata=_wikidata({}))

    assert resolved == []
    assert [(entry["issue_type"], entry["detail"]) for entry in review] == [
        ("not_found", "via births_file"), ("not_found", "via wikidata"),
    ]
