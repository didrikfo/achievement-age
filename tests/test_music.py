from unittest.mock import MagicMock

import pytest

from ingest.enrichment import check_phrase_format
from ingest.sources import music
from ingest.sources.music import (
    KIND_ORDER,
    KIND_SOURCES,
    MUSIC_SOURCES,
    build_event_phrase,
    event_clause,
    event_detail,
    lead_credit,
    split_credit,
)


# --- lead_credit / split_credit ---


@pytest.mark.parametrize(
    "credit, lead",
    [
        ("Mariah Carey ft. Jay-Z", "Mariah Carey"),
        ("Pitbull Featuring Ne-Yo, Afrojack & Nayer", "Pitbull"),
        ("Bruno Mars & Lady Gaga", "Bruno Mars & Lady Gaga"),
        ("¥$: Kanye West & Ty Dolla $ign", "Kanye West & Ty Dolla $ign"),
    ],
)
def test_lead_credit_drops_featured_guests_and_group_prefixes(credit, lead):
    assert lead_credit(credit) == lead


def test_split_credit_with_a_solo_structure_keeps_the_whole_lead_even_with_the():
    assert split_credit("The Weeknd", "1") == ["The Weeknd"]
    assert split_credit("Mariah Carey ft. Jay-Z", "1.5") == ["Mariah Carey"]


def test_split_credit_with_a_duo_structure_splits_the_lead():
    assert split_credit("Bruno Mars & Lady Gaga", "2") == ["Bruno Mars", "Lady Gaga"]
    assert split_credit("Luis Fonsi & Daddy Yankee ft. Justin Bieber", "2.5") == ["Luis Fonsi", "Daddy Yankee"]


def test_split_credit_drops_single_words_split_off_a_duo():
    # The births file's "Peaches" is a different musician from Peaches & Herb's.
    assert split_credit("Peaches & Herb", "2") == []
    assert split_credit("Sonny & Cher", "2") == []


@pytest.mark.parametrize("structure", ["0", "0.5"])
def test_split_credit_with_a_group_structure_names_nobody(structure):
    assert split_credit("Maroon 5 ft. Cardi B", structure) == []


@pytest.mark.parametrize(
    "credit, names",
    [
        ("Madonna", ["Madonna"]),
        ("Bruce Springsteen & The E Street Band", ["Bruce Springsteen"]),
        ("Paul Mauriat And His Orchestra", ["Paul Mauriat"]),
        ("Simon & Garfunkel", []),
        ("Earth, Wind & Fire", []),
        ("The Beatles", []),
        ("John Lennon & Yoko Ono", ["John Lennon", "Yoko Ono"]),
        ("Tanel Padar, Dave Benton & 2XL", ["Tanel Padar", "Dave Benton"]),
        ("Lil Nas X", ["Lil Nas X"]),
    ],
)
def test_split_credit_without_a_structure_uses_the_name_heuristics(credit, names):
    assert split_credit(credit) == names


# --- sentences ---


def _hot100(**overrides):
    base = {
        "kind": "hot100_number_one", "record_key": "hot100:Poor Little Fool:Ricky Nelson",
        "title": "Poor Little Fool", "credit": "Ricky Nelson", "names": ["Ricky Nelson"],
        "weeks_at_number_one": 2, "year": 1958, "month": 8, "day": 4,
    }
    return {**base, **overrides}


def test_hot100_sentences():
    record = _hot100()
    assert event_clause(record, "Ricky Nelson") == '"Poor Little Fool" reached number one on the Billboard Hot 100'
    assert event_detail(record) == (
        '"Poor Little Fool" by Ricky Nelson reached number one on the Billboard Hot 100 chart dated '
        "August 4, 1958, and spent 2 weeks at the top."
    )


def test_a_duo_clause_names_the_partner_but_not_the_subject():
    record = _hot100(title="Die with a Smile", credit="Bruno Mars & Lady Gaga", names=["Bruno Mars", "Lady Gaga"])
    assert event_clause(record, "Lady Gaga") == (
        '"Die with a Smile", recorded with Bruno Mars, reached number one on the Billboard Hot 100'
    )


def test_billboard200_detail_says_one_week_in_the_singular():
    record = _hot100(
        kind="billboard200_number_one", title="Thriller", credit="Michael Jackson", names=["Michael Jackson"],
        weeks_at_number_one=1,
    )
    assert event_clause(record, "Michael Jackson") == "the album Thriller reached number one on the Billboard 200"
    assert "spent 1 week at the top." in event_detail(record)


def test_eurovision_sentences_include_points_only_when_known():
    record = {
        "kind": "eurovision_win", "title": "Refrain", "credit": "Lys Assia", "names": ["Lys Assia"],
        "country_name": "Switzerland", "host_city": "Lugano", "points": None,
        "year": 1956, "month": 5, "day": 24,
    }
    assert event_clause(record, "Lys Assia") == '"Refrain" won the Eurovision Song Contest 1956 for Switzerland'
    assert event_detail(record) == (
        'Lys Assia won the Eurovision Song Contest 1956 for Switzerland with "Refrain" '
        "at the final in Lugano on May 24, 1956."
    )
    assert event_detail({**record, "points": 24}).endswith("on May 24, 1956, scoring 24 points.")


def test_event_phrase_joins_same_day_clauses_and_passes_the_format_check():
    phrase = build_event_phrase("Rod Stewart", ['"Maggie May" reached number one', "the album X did too"])
    assert phrase == 'Rod Stewart was when "Maggie May" reached number one and the album X did too.'
    assert check_phrase_format(phrase, "Rod Stewart") is None


def test_an_unknown_kind_is_an_error_not_a_silent_blank():
    with pytest.raises(ValueError):
        event_clause({"kind": "grammy", "names": []}, "Someone")


def test_every_kind_maps_to_one_of_the_music_sources():
    assert set(KIND_SOURCES.values()) == set(MUSIC_SOURCES)
    assert KIND_ORDER == list(KIND_SOURCES)


# --- fetch_sources ---


def test_fetch_sources_downloads_missing_files_and_skips_existing_ones(tmp_path, monkeypatch):
    existing = tmp_path / "eurovision.json"
    existing.write_text("already here")
    get = MagicMock(return_value=MagicMock(content=b"data"))
    monkeypatch.setattr(music.requests, "get", get)

    paths = music.fetch_sources(tmp_path)

    assert {path.name for path in paths} == set(music.SOURCE_FILES)
    assert existing.read_text() == "already here"
    assert get.call_count == len(music.SOURCE_FILES) - 1
    assert all("raw.githubusercontent.com" in call.args[0] for call in get.call_args_list)


def test_split_credit_treats_a_known_band_as_a_group():
    # Each of these resolves to one member through a Wikidata alias if it reaches the lookup.
    assert split_credit("Stone Temple Pilots") == []
    assert split_credit("Sade") == []
    assert split_credit("Madonna") == ["Madonna"]
