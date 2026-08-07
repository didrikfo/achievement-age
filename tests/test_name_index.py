from ingest.name_index import build_name_index, find_names_in_text


def test_finds_a_multi_token_name():
    automaton = build_name_index(["George Washington"])
    assert find_names_in_text(automaton, "In 1776 George Washington crossed.") == ["george washington"]


def test_does_not_match_inside_a_longer_word():
    # A plain substring check would wrongly match "Art Ross" inside "Parts Rossiter".
    automaton = build_name_index(["Art Ross"])
    assert find_names_in_text(automaton, "The parts rossiter was replaced.") == []


def test_matches_across_diacritics_and_punctuation():
    automaton = build_name_index(["José O'Brien"])
    assert find_names_in_text(automaton, "A speech by Jose O Brien followed.") == ["jose o brien"]


def test_returns_every_matching_name_sorted():
    automaton = build_name_index(["Albert Einstein", "Marie Curie"])
    found = find_names_in_text(automaton, "Marie Curie wrote to Albert Einstein.")
    assert found == ["albert einstein", "marie curie"]


def test_deduplicates_repeated_names():
    automaton = build_name_index(["Marie Curie"])
    assert find_names_in_text(automaton, "Marie Curie met Marie Curie again.") == ["marie curie"]


def test_empty_index_finds_nothing():
    automaton = build_name_index([])
    assert find_names_in_text(automaton, "Anything at all.") == []


def test_blank_names_are_skipped():
    automaton = build_name_index(["", "   ", "Marie Curie"])
    assert find_names_in_text(automaton, "Marie Curie arrived.") == ["marie curie"]
