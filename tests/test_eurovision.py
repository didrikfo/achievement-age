import json

import pytest

from ingest.sources.eurovision import EUROVISION_PATH, load_eurovision_winners, repair_credit


def test_repair_credit_rejoins_a_name_split_at_its_hyphen():
    # The real 1961 winner, as the dataset stores it.
    assert repair_credit("Jean", "Claude Pascal", "Nous les amoureux") == ("Jean-Claude Pascal", "Nous les amoureux")


def test_repair_credit_restores_a_song_title_cut_at_its_hyphen():
    assert repair_credit("Lulu", "Boom Bang", "Boom Bang-a-Bang") == ("Lulu", "Boom Bang-a-Bang")


def test_repair_credit_leaves_an_intact_pair_alone():
    assert repair_credit("ABBA", "Waterloo", "Waterloo") == ("ABBA", "Waterloo")
    assert repair_credit("ABBA", "Waterloo", None) == ("ABBA", "Waterloo")


def _contestant(contestant_id, artist, song, country, lyrics_title=None):
    return {
        "id": contestant_id, "artist": artist, "song": song, "country": country,
        "lyrics": [{"title": lyrics_title or song}],
    }


def _write(tmp_path, contests):
    path = tmp_path / "eurovision.json"
    path.write_text(json.dumps(contests), encoding="utf-8")
    countries = tmp_path / "countries.json"
    countries.write_text(json.dumps({"SE": "Sweden", "GB": "United Kingdom", "UA": "Ukraine"}), encoding="utf-8")
    return path, countries


def test_load_eurovision_winners_takes_place_one_of_the_final_and_its_total_points(tmp_path):
    contests = [
        {
            "year": 2016, "city": "Stockholm",
            "contestants": [_contestant(0, "Jamala", "1944", "UA"), _contestant(1, "Someone", "Song", "SE")],
            "rounds": [
                {"name": "semifinal1", "date": "2016-05-10", "performances": [{"contestantId": 1, "place": 1}]},
                {
                    "name": "final", "date": "2016-05-14",
                    "performances": [
                        # Since 2016 the jury and televote tallies sit beside the total.
                        {"contestantId": 0, "place": 1, "scores": [
                            {"name": "total", "points": 534}, {"name": "public", "points": 323},
                            {"name": "jury", "points": 211},
                        ]},
                        {"contestantId": 1, "place": 2, "scores": [{"name": "total", "points": 511}]},
                    ],
                },
            ],
        }
    ]
    path, countries = _write(tmp_path, contests)

    [winner] = load_eurovision_winners(path, countries)

    assert winner["credit"] == "Jamala"
    assert winner["names"] == ["Jamala"]
    assert winner["points"] == 534
    assert winner["country_name"] == "Ukraine"
    assert winner["host_city"] == "Stockholm"
    assert (winner["year"], winner["month"], winner["day"]) == (2016, 5, 14)


def test_load_eurovision_winners_keeps_every_winner_of_a_tie_and_skips_a_contest_with_no_final(tmp_path):
    contests = [
        {
            "year": 1969, "city": "Madrid",
            "contestants": [_contestant(0, "Lulu", "Boom Bang", "GB", "Boom Bang-a-Bang"), _contestant(1, "X Y", "Z", "SE")],
            "rounds": [{"name": "final", "date": "1969-03-29", "performances": [
                {"contestantId": 0, "place": 1, "scores": []}, {"contestantId": 1, "place": 1, "scores": []},
            ]}],
        },
        {"year": 2020, "city": "Rotterdam", "contestants": [], "rounds": []},
    ]
    path, countries = _write(tmp_path, contests)

    winners = load_eurovision_winners(path, countries)

    assert [winner["credit"] for winner in winners] == ["Lulu", "X Y"]
    assert winners[0]["title"] == "Boom Bang-a-Bang"
    assert winners[0]["points"] is None



@pytest.mark.skipif(not EUROVISION_PATH.exists(), reason="music source files not downloaded")
def test_real_eurovision_winners_are_repaired_and_dated():
    winners = {(winner["year"], winner["credit"]): winner for winner in load_eurovision_winners()}

    assert ("Nous les amoureux", "Luxembourg") == (
        winners[(1961, "Jean-Claude Pascal")]["title"], winners[(1961, "Jean-Claude Pascal")]["country_name"]
    )
    abba = winners[(1974, "ABBA")]
    assert (abba["month"], abba["day"], abba["points"]) == (4, 6, 24)
    assert winners[(2016, "Jamala")]["points"] == 534
    assert len([winner for winner in winners if winner[0] == 1969]) == 4
