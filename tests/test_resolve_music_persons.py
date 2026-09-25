from ingest.resolve_music_persons import (
    settle_ambiguous,
    canonicalize_names,
    is_posthumous,
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


def test_musician_candidate_filter_needs_a_musician_description():
    # The exact-name half of the check is wikidata.lookup_birth_date(require_exact_name=True).
    assert musician_candidate_filter({"label": "Prince", "description": "American singer-songwriter (1958-2016)"})
    assert musician_candidate_filter({"label": "Sisqó", "description": "American R&B artist"})
    assert not musician_candidate_filter({"label": "Prince", "description": "son of a monarch"})
    assert not musician_candidate_filter({"label": "Brandy", "description": "spirit produced by distilling wine"})
    assert not musician_candidate_filter({"label": "Nobody", "description": None})


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
        ("not_found", "via wikidata"), ("not_found", "via births_file"),
    ]


def test_resolve_people_asks_wikidata_before_the_births_file():
    # Only Wikidata knows death dates, so it goes first even when the births file has the name.
    births = {"ray charles": [{"year": 1930, "month": 9, "day": 23}]}
    wikidata = _wikidata({"Ray Charles": {"status": "resolved", "year": 1930, "month": 9, "day": 23, "qid": "Q544387",
                                          "death": {"year": 2004, "month": 6, "day": 10, "precision": 11}}})

    resolved, review = resolve_people([_person(expected=30.0)], births, lookup_wikidata=wikidata)

    assert resolved[0]["birth_source"] == "wikidata"
    assert review == []


def test_resolve_people_flags_a_births_file_fallback_for_a_posthumous_check():
    births = {"ray charles": [{"year": 1930, "month": 9, "day": 23}]}

    resolved, review = resolve_people([_person(expected=30.0)], births, lookup_wikidata=_wikidata({}))

    assert resolved[0]["birth_source"] == "births_file"
    assert [entry["issue_type"] for entry in review] == ["not_found", "death_date_unknown"]


def test_resolve_people_settles_an_ambiguous_wikidata_result_by_the_age_check():
    # Two musicians named Chris Brown; only the one born 1989 was 18 at "Kiss Kiss".
    record = _person(name="Chris Brown", title="Kiss Kiss", year=2007, month=12, day=15, expected=18.0)
    wikidata = _wikidata({"Chris Brown": {"status": "ambiguous", "name": "Chris Brown", "candidates": [
        {"qid": "Q155700", "label": "Chris Brown", "year": 1989, "month": 5, "day": 5, "death": None},
        {"qid": "Q115390006", "label": "Chris Brown", "year": 1960, "month": 1, "day": 1, "death": None},
    ]}})

    resolved, _ = resolve_people([record], {}, lookup_wikidata=wikidata)

    assert [(r["wikidata_qid"], r["birth_year"]) for r in resolved] == [("Q155700", 1989)]


def test_resolve_people_leaves_ambiguous_a_result_nothing_settles():
    # "Tossin' and Turnin'" carries no usable age here, and neither Bobby Lewis is clearly the famous one.
    single = _person(name="Bobby Lewis", title="Tossin' and Turnin'", year=1961, month=7, day=10)
    wikidata = _wikidata({"Bobby Lewis": {"status": "ambiguous", "name": "Bobby Lewis", "candidates": [
        {"qid": "Q888609", "label": "Bobby Lewis", "year": 1925, "month": 2, "day": 9, "death": None, "sitelinks": 9},
        {"qid": "Q4935287", "label": "Bobby Lewis", "year": 1942, "month": 5, "day": 9, "death": None, "sitelinks": 3},
    ]}})

    resolved, review = resolve_people([single], {}, lookup_wikidata=wikidata)

    assert resolved == []
    assert review[0]["issue_type"] == "ambiguous"


def _mayer(qid, year, sitelinks, death=None):
    return {"qid": qid, "label": "John Mayer", "year": year, "month": 10, "day": 16, "death": death,
            "sitelinks": sitelinks}


def test_settle_ambiguous_takes_a_clear_fame_winner():
    # An album carries no front_person_age, so both John Mayers pass the age check.
    album = _person(name="John Mayer", title="Continuum", year=2006, month=10, day=7, kind="billboard200_number_one")

    chosen = settle_ambiguous([album], [_mayer("Q1785163", 1930, 7), _mayer("Q215215", 1977, 61)], "wikidata")

    assert chosen["qid"] == "Q215215"


def test_settle_ambiguous_needs_a_clear_margin_and_real_fame():
    album = _person(name="John Mayer", title="Continuum", year=2006, month=10, day=7, kind="billboard200_number_one")

    assert settle_ambiguous([album], [_mayer("Q1", 1930, 12), _mayer("Q2", 1977, 40)], "wikidata") is None
    assert settle_ambiguous([album], [_mayer("Q1", 1930, 1), _mayer("Q2", 1977, 8)], "wikidata") is None


def test_settle_ambiguous_drops_a_candidate_dead_before_every_record():
    album = _person(name="Ken Carson", title="More Chaos", year=2025, month=5, day=3, kind="billboard200_number_one")
    old = {"qid": "Q19667992", "label": "Ken Carson", "year": 1914, "month": 11, "day": 14, "sitelinks": 40,
           "death": {"year": 1994, "month": 1, "day": 1, "precision": 11}}
    rapper = {"qid": "Q114050700", "label": "Ken Carson", "year": 2000, "month": 4, "day": 11, "sitelinks": 24,
              "death": None}

    assert settle_ambiguous([album], [old, rapper], "wikidata")["qid"] == "Q114050700"


def test_resolve_people_drops_records_after_the_death_but_keeps_the_rest():
    # "(Just Like) Starting Over" reached number one 19 days after John Lennon was killed.
    imagine = _person(name="John Lennon", title="Imagine", year=1971, month=10, day=30, kind="billboard200_number_one")
    starting_over = _person(name="John Lennon", title="(Just Like) Starting Over", year=1980, month=12, day=27,
                            expected=40.0)
    wikidata = _wikidata({"John Lennon": {"status": "resolved", "year": 1940, "month": 10, "day": 9, "qid": "Q1203",
                                          "death": {"year": 1980, "month": 12, "day": 8, "precision": 11}}})

    resolved, review = resolve_people([imagine, starting_over], {}, lookup_wikidata=wikidata)

    assert [r["title"] for r in resolved] == ["Imagine"]
    assert review[0]["issue_type"] == "posthumous_record"
    assert "after the death on 1980-12-08" in review[0]["detail"]


def test_is_posthumous_handles_coarse_death_dates():
    event = {"year": 1970, "month": 11, "day": 1}
    assert is_posthumous(event, {"year": 1970, "month": 10, "day": 4})
    assert not is_posthumous(event, {"year": 1970, "month": 11, "day": 1})  # the day of death itself
    assert is_posthumous(event, {"year": 1970, "month": 11, "day": None})
    assert not is_posthumous(event, {"year": 1970, "month": 12, "day": None})
    assert is_posthumous(event, {"year": 1970, "month": None, "day": None})
    assert not is_posthumous(event, None)


def test_canonicalize_names_merges_spellings_of_one_person_under_the_wikidata_label():
    records = [
        {"name": "Beyonce", "names": ["Beyonce"], "wikidata_qid": "Q36153", "wikidata_label": "Beyoncé"},
        {"name": "Beyonce", "names": ["Beyonce"], "wikidata_qid": "Q36153", "wikidata_label": "Beyoncé"},
        {"name": "Beyoncé", "names": ["Beyoncé", "Jay-Z"], "wikidata_qid": "Q36153", "wikidata_label": "Beyoncé"},
        {"name": "Adele", "names": ["Adele"], "wikidata_qid": "Q23215", "wikidata_label": "Adele"},
        {"name": "Ray Charles", "names": ["Ray Charles"], "wikidata_qid": None},
    ]

    renamed = canonicalize_names(records)

    assert [r["name"] for r in renamed] == ["Beyoncé"] * 3 + ["Adele", "Ray Charles"]
    assert renamed[0]["names"] == ["Beyoncé"]
    assert renamed[2]["names"] == ["Beyoncé", "Jay-Z"]


def test_resolve_people_does_not_fall_back_to_the_births_file_after_a_contradicted_wikidata_date():
    # Wikidata has Ernie K-Doe born 1936, the births file 1933: disagreeing sources, so no guess.
    single = _person(name="Ernie K-Doe", title="Mother-in-Law", year=1961, month=5, day=22, expected=28.0)
    births = {"ernie k-doe": [{"year": 1933, "month": 2, "day": 22}]}
    wikidata = _wikidata({"Ernie K-Doe": {"status": "resolved", "year": 1936, "month": 2, "day": 22, "qid": "Q1"}})

    resolved, review = resolve_people([single], births, lookup_wikidata=wikidata)

    assert resolved == []
    assert [entry["issue_type"] for entry in review] == ["rejected_birth_date"]
