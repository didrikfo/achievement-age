from ingest.match_events import classify_event, load_widened_births_lookup
from ingest.name_index import build_name_index

CURIE = {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7}
EINSTEIN = {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}


def _lookup(*people):
    return {
        __import__("core.matching", fromlist=["normalize_name"]).normalize_name(person["name"]): person
        for person in people
    }


def _event(text, year=1905, month=11, day=21):
    return {"year": year, "month": month, "day": day, "text": text}


def test_single_known_name_matches_and_computes_age():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Albert Einstein published a paper."), automaton, lookup)

    assert status == "matched"
    assert payload["name"] == "Albert Einstein"
    # 1879-03-14 to 1905-11-21, verified: (date(1905,11,21) - date(1879,3,14)).days
    assert payload["age"] == 9748
    assert payload["text"] == "Albert Einstein published a paper."


def test_two_known_names_are_ambiguous_not_guessed():
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Marie Curie wrote to Albert Einstein."), automaton, lookup)

    assert status == "ambiguous"
    assert payload == ["albert einstein", "marie curie"]


def test_no_known_name_is_unmatched():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(_event("A treaty was signed in Vienna."), automaton, lookup)

    assert status == "unmatched"


def test_event_before_the_persons_birth_is_implausible():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Albert Einstein appears.", year=1800), automaton, lookup)

    assert status == "implausible"
    assert payload == "Albert Einstein"


def test_non_numeric_year_is_unusable():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(
        {"year": "c. 1300", "month": 1, "day": 1, "text": "Albert Einstein appears."}, automaton, lookup
    )

    assert status == "unusable"


def test_widened_lookup_drops_single_token_names(tmp_path):
    births = tmp_path / "births.json"
    births.write_text(
        '[{"name": "Cicero", "year": -106, "month": 1, "day": 3},'
        ' {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7}]',
        encoding="utf-8",
    )

    lookup = load_widened_births_lookup(births)

    assert "marie curie" in lookup
    assert "cicero" not in lookup
