import csv

import pytest

from ingest.sources.billboard import (
    BILLBOARD_200_PATH,
    HOT_100_CHART_PATH,
    HOT_100_PATH,
    known_credit_structures,
    load_billboard200_number_ones,
    load_chart_number_ones,
    load_hot100_number_ones,
    verify_hot100_dates,
)

HOT100_FIELDS = ["song", "artist", "date", "weeks_at_number_one", "artist_structure", "front_person_age"]
CHART_FIELDS = ["chart_week", "current_week", "title", "performer", "last_week", "peak_pos", "wks_on_chart"]


def _write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    return path


def _hot100_file(tmp_path, rows):
    return _write_csv(tmp_path / "hot100.csv", HOT100_FIELDS, rows)


def _chart_file(tmp_path, rows, name="chart.csv"):
    return _write_csv(tmp_path / name, CHART_FIELDS, [{"current_week": "1", **row} for row in rows])


def test_load_hot100_number_ones_parses_dates_structure_and_expected_age(tmp_path):
    path = _hot100_file(
        tmp_path,
        [
            {"song": "Poor Little Fool", "artist": "Ricky Nelson", "date": "1958-08-04T00:00:00Z",
             "weeks_at_number_one": "2", "artist_structure": "1", "front_person_age": "18"},
            {"song": "Umbrella", "artist": "Rihanna ft. Jay-Z", "date": "2007-06-09T00:00:00Z",
             "weeks_at_number_one": "7", "artist_structure": "1.5", "front_person_age": "28"},
            {"song": "Little Star", "artist": "The Elegants", "date": "1958-08-25T00:00:00Z",
             "weeks_at_number_one": "1", "artist_structure": "0", "front_person_age": "NA"},
        ],
    )

    nelson, rihanna, elegants = load_hot100_number_ones(path)

    assert (nelson["year"], nelson["month"], nelson["day"]) == (1958, 8, 4)
    assert nelson["names"] == ["Ricky Nelson"]
    assert nelson["expected_age_years"] == 18.0
    # front_person_age averages a featured guest in, so it pins nobody down.
    assert rihanna["names"] == ["Rihanna"]
    assert rihanna["expected_age_years"] is None
    assert elegants["names"] == []


def test_load_chart_number_ones_keeps_the_first_week_and_counts_every_week_at_the_top(tmp_path):
    path = _write_csv(
        tmp_path / "chart.csv",
        CHART_FIELDS,
        [
            {"chart_week": "1983-03-05", "current_week": "1", "title": "Thriller", "performer": "Michael Jackson"},
            {"chart_week": "1983-02-26", "current_week": "1", "title": "Thriller", "performer": "Michael Jackson"},
            {"chart_week": "1983-03-12", "current_week": "2", "title": "Thriller", "performer": "Michael Jackson"},
            {"chart_week": "1983-04-30", "current_week": "1", "title": "Thriller", "performer": "Michael Jackson"},
        ],
    )

    assert load_chart_number_ones(path) == {("Thriller", "Michael Jackson"): ("1983-02-26", 3)}


def _hot100_record(title, year, month, day):
    return {"record_key": f"hot100:{title}", "title": title, "credit": "Someone", "year": year, "month": month, "day": day}


def test_verify_hot100_dates_keeps_a_date_the_archive_confirms_even_under_another_title():
    chart = {("Come Together/Something", "The Beatles"): ("1969-11-29", 1)}
    record = _hot100_record("Come Together", 1969, 11, 29)

    verified, review = verify_hot100_dates([record], chart)

    assert verified == [record]
    assert review == []


def test_verify_hot100_dates_corrects_the_real_whitney_houston_typo():
    # TidyTuesday has 1987-07-27 - a Monday; the chart was dated Saturday 1987-06-27.
    chart = {("I Wanna Dance With Somebody (Who Loves Me)", "Whitney Houston"): ("1987-06-27", 2)}
    record = _hot100_record("I Wanna Dance with Somebody (Who Loves Me)", 1987, 7, 27)

    verified, review = verify_hot100_dates([record], chart)

    assert (verified[0]["year"], verified[0]["month"], verified[0]["day"]) == (1987, 6, 27)
    assert review[0]["issue_type"] == "corrected_date"


def test_verify_hot100_dates_drops_a_date_it_cannot_confirm():
    chart = {("Something Else", "Someone"): ("1990-01-06", 1)}
    record = _hot100_record("Unknown Song", 1987, 7, 27)

    verified, review = verify_hot100_dates([record], chart)

    assert verified == []
    assert review[0]["issue_type"] == "unverified_date"


def test_verify_hot100_dates_ignores_a_same_title_number_one_years_away():
    chart = {("Hello", "Adele"): ("2015-11-14", 10)}
    record = _hot100_record("Hello", 1984, 5, 19)  # Lionel Richie's, a different song

    verified, review = verify_hot100_dates([record], chart)

    assert verified == []


def test_billboard200_skips_soundtracks_and_uses_known_structures(tmp_path):
    path = _chart_file(
        tmp_path,
        [
            {"chart_week": "2021-01-02", "title": "Evermore", "performer": "Taylor Swift"},
            {"chart_week": "2020-04-04", "title": "After Hours", "performer": "The Weeknd"},
            {"chart_week": "1977-04-02", "title": "Rumours", "performer": "Fleetwood Mac"},
            {"chart_week": "1978-01-21", "title": "Saturday Night Fever", "performer": "Soundtrack"},
        ],
    )
    structures = known_credit_structures(
        [{"credit": "The Weeknd", "artist_structure": "1"}, {"credit": "Fleetwood Mac", "artist_structure": "0"}]
    )

    records = load_billboard200_number_ones(path, structures)

    assert [record["title"] for record in records] == ["Rumours", "After Hours", "Evermore"]
    names = {record["title"]: record["names"] for record in records}
    assert names == {"Rumours": [], "After Hours": ["The Weeknd"], "Evermore": ["Taylor Swift"]}
    assert all(record["kind"] == "billboard200_number_one" for record in records)


# --- against the real downloaded files (python -c "from ingest.sources.music import fetch_sources; fetch_sources()") ---

_real_files = pytest.mark.skipif(
    not (HOT_100_PATH.exists() and HOT_100_CHART_PATH.exists() and BILLBOARD_200_PATH.exists()),
    reason="music source files not downloaded",
)


@_real_files
def test_real_hot100_dates_all_verify_and_the_whitney_typo_is_corrected():
    verified, review = verify_hot100_dates(load_hot100_number_ones(), load_chart_number_ones(HOT_100_CHART_PATH))

    assert not [entry for entry in review if entry["issue_type"] == "unverified_date"]
    whitney = next(record for record in verified if record["title"].startswith("I Wanna Dance with Somebody"))
    assert (whitney["year"], whitney["month"], whitney["day"]) == (1987, 6, 27)


@_real_files
def test_real_billboard200_has_thriller_first_at_number_one_on_1983_02_26():
    records = load_billboard200_number_ones(credit_structures=known_credit_structures(load_hot100_number_ones()))

    thriller = next(record for record in records if record["title"] == "Thriller")
    assert (thriller["year"], thriller["month"], thriller["day"]) == (1983, 2, 26)
    assert thriller["names"] == ["Michael Jackson"]
    assert len({record["record_key"] for record in records}) == len(records)


@_real_files
def test_real_front_person_age_errata_are_skipped():
    records = {record["record_key"]: record for record in load_hot100_number_ones()}

    assert records["hot100:Got My Mind Set On You:George Harrison"]["expected_age_years"] is None
    assert records["hot100:Call Me Maybe:Carly Rae Jepsen"]["expected_age_years"] is None
    assert records["hot100:Billie Jean:Michael Jackson"]["expected_age_years"] is not None
