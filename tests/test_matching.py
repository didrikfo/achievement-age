from datetime import date

from core.config import TAG_TAXONOMY
from core.matching import events_by_age_days, events_for_subscription, excluded_from_included, filter_events_by_tags, full_sentence, included_from_excluded, name_matches_text, normalize_name


def test_true_positive_with_punctuation():
    assert name_matches_text("O'Brien", "Kevin O'Brien announced the results.")


def test_false_positive_avoided_short_name_inside_longer_word():
    # A plain substring check would wrongly match "Art" inside "parts".
    assert not name_matches_text("Art", "He organized many parts of the exhibit.")


def test_diacritic_normalization():
    assert name_matches_text("José", "The news mentioned Jose Rizal today.")


def test_normalize_name_strips_diacritics_and_punctuation():
    assert normalize_name("José O'Brien") == "jose o brien"


def _event(name, birth_date, event_date, event_phrase=""):
    return {
        "name": name,
        "age_days": (event_date - birth_date).days,
        "event_phrase": event_phrase,
    }


def test_events_by_age_days_groups_by_age():
    washington = _event("George Washington", date(1732, 2, 22), date(1776, 1, 1))
    einstein = _event("Albert Einstein", date(1879, 3, 14), date(1905, 11, 21))

    index = events_by_age_days([washington, einstein])

    assert index[washington["age_days"]] == [washington]
    assert index[einstein["age_days"]] == [einstein]


def test_events_by_age_days_same_age_shares_bucket():
    same_age_date = date(2000, 1, 1)
    person_a = _event("Person A", date(1990, 1, 1), same_age_date)
    person_b = _event("Person B", date(1985, 1, 1), date(1995, 1, 1))

    assert person_a["age_days"] == person_b["age_days"]

    index = events_by_age_days([person_a, person_b])

    assert index[person_a["age_days"]] == [person_a, person_b]


def test_full_sentence_prefixes_a_legacy_suffix_only_phrase():
    event = {"name": "George Washington", "event_phrase": "he hoisted the flag"}
    assert full_sentence(event) == "The same age that George Washington was when he hoisted the flag"


def test_full_sentence_passes_through_a_stored_full_sentence():
    phrase = "The same age that Sir Richard Owen was when a dinner party was held inside an iguanodon."
    event = {"name": "Richard Owen", "event_phrase": phrase}
    assert full_sentence(event) == phrase


def test_full_sentence_passes_through_regardless_of_case_or_leading_space():
    # A subagent that lowercased the opening, or left the phrase indented, still
    # produced a full sentence - prefixing it again would duplicate the opening.
    event = {"name": "Ada Lovelace", "event_phrase": "  the same age that Ada Lovelace was when she published her notes."}
    assert full_sentence(event) == "  the same age that Ada Lovelace was when she published her notes."


def test_no_exclusions_includes_the_whole_taxonomy():
    assert included_from_excluded([]) == TAG_TAXONOMY


def test_included_from_excluded_removes_the_excluded_names():
    result = included_from_excluded(["military", "sports"])
    assert "military" not in result
    assert "sports" not in result
    assert "science" in result
    assert len(result) == len(TAG_TAXONOMY) - 2


def test_excluded_from_included_is_the_complement():
    assert excluded_from_included(TAG_TAXONOMY) == []
    assert excluded_from_included([]) == TAG_TAXONOMY


def test_inversion_round_trips_a_ui_selection():
    selection = ["science", "space", "technology"]
    stored = excluded_from_included(selection)
    assert sorted(included_from_excluded(stored)) == sorted(selection)


def test_helpers_order_output_by_taxonomy_not_input_order():
    # "space" comes after "science" in TAG_TAXONOMY; input order must not leak through.
    assert included_from_excluded(
        [tag for tag in TAG_TAXONOMY if tag not in {"space", "science"}]
    ) == ["science", "space"]


def test_unknown_names_are_ignored():
    assert included_from_excluded(["not-a-real-tag"]) == TAG_TAXONOMY


def _tagged(tags):
    return {"name": "Someone", "age_days": 1, "event_phrase": "", "tags": tags}


def test_untagged_event_survives_every_filter():
    untagged = _tagged([])
    assert filter_events_by_tags([untagged], ["science"]) == [untagged]
    assert filter_events_by_tags([untagged], []) == [untagged]


def test_event_with_all_tags_excluded_is_dropped():
    assert filter_events_by_tags([_tagged(["military"])], ["science"]) == []


def test_event_survives_on_any_single_surviving_tag():
    # Only "politics" is included; the event keeps it despite "military" being out.
    event = _tagged(["military", "politics"])
    assert filter_events_by_tags([event], ["politics"]) == [event]


def test_empty_inclusion_drops_tagged_but_keeps_untagged():
    untagged = _tagged([])
    tagged = _tagged(["science"])
    assert filter_events_by_tags([tagged, untagged], []) == [untagged]


def test_missing_tags_key_is_treated_as_untagged():
    # An event dict from a code path that never set "tags" must not raise.
    event = {"name": "Someone", "age_days": 1, "event_phrase": ""}
    assert filter_events_by_tags([event], ["science"]) == [event]


def test_events_for_subscription_applies_stored_exclusions():
    science = _tagged(["science"])
    military = _tagged(["military"])
    subscription = {"excluded_tags": ["military"]}
    assert events_for_subscription([science, military], subscription) == [science]


def test_events_for_subscription_survives_a_missing_column():
    # Before the alter-table lands, subscription rows have no excluded_tags key
    # at all. That must mean "no filtering", not a KeyError that kills the cron.
    science = _tagged(["science"])
    military = _tagged(["military"])
    assert events_for_subscription([science, military], {}) == [science, military]


def test_events_for_subscription_treats_null_column_as_no_filtering():
    events = [_tagged(["science"])]
    assert events_for_subscription(events, {"excluded_tags": None}) == events
