from core.config import CATEGORY_NAMES, SEQUENCE_TAXONOMY, TAG_TAXONOMY
from core.preferences import (
    CATEGORY,
    SEQUENCE,
    NotifyOverride,
    Row,
    all_rows,
    default_preferences,
    is_notifying,
    is_on_calendar,
    preferences_from_subscription,
    preferences_to_columns,
    set_mirror,
    tags_for_category,
    toggle_calendar,
    toggle_notify,
    toggle_tag,
)


def test_default_preferences_show_everything_and_mirror():
    preferences = default_preferences()

    assert preferences.calendar.categories == tuple(CATEGORY_NAMES)
    assert preferences.calendar.tags == tuple(TAG_TAXONOMY)
    # Anniversaries are opt-in, so a fresh visitor tracks none.
    assert preferences.calendar.sequences == ()
    assert preferences.notify_mirrors_calendar is True


def test_no_subscription_gives_the_defaults():
    assert preferences_from_subscription(None) == default_preferences()


def test_calendar_channel_reads_the_three_stored_columns():
    preferences = preferences_from_subscription(
        {
            "excluded_categories": ["Sport"],
            "excluded_tags": ["military"],
            "included_sequences": ["Primes", "Powers of 2"],
        }
    )

    assert "Sport" not in preferences.calendar.categories
    assert "Politics & Power" in preferences.calendar.categories
    assert "military" not in preferences.calendar.tags
    # Ordered by the taxonomy, not by how they were stored.
    assert preferences.calendar.sequences == ("Powers of 2", "Primes")


def test_mirroring_makes_notify_identical_to_calendar():
    preferences = preferences_from_subscription(
        {
            "excluded_categories": ["Sport"],
            "included_sequences": ["Primes"],
            "notify_mirrors_calendar": True,
            # Deliberately contradictory: while mirroring these are never read.
            "notify_excluded_categories": CATEGORY_NAMES,
            "notify_excluded_sequences": [],
        }
    )

    assert preferences.notify == preferences.calendar


def test_override_applies_when_not_mirroring():
    preferences = preferences_from_subscription(
        {
            "excluded_categories": [],
            "included_sequences": ["Primes", "Powers of 2"],
            "notify_mirrors_calendar": False,
            "notify_excluded_categories": ["Sport", "War & Conflict"],
            "notify_excluded_sequences": ["Powers of 2"],
        }
    )

    assert "Sport" not in preferences.notify.categories
    assert "War & Conflict" not in preferences.notify.categories
    assert "Science & Technology" in preferences.notify.categories
    # Both override columns are exclusions, so naming "Powers of 2" MUTES it and
    # leaves the other marked sequence notifying.
    assert preferences.notify.sequences == ("Primes",)


def test_notify_is_intersected_with_calendar_on_read():
    # "Sport" is off the calendar entirely, but the override still names it as
    # notifying. The stale entry must not resurrect it.
    preferences = preferences_from_subscription(
        {
            "excluded_categories": ["Sport"],
            "included_sequences": [],
            "notify_mirrors_calendar": False,
            "notify_excluded_categories": [],
            "notify_excluded_sequences": ["Primes"],
        }
    )

    assert "Sport" not in preferences.notify.categories
    assert preferences.notify.sequences == ()


def test_notify_tags_always_equal_calendar_tags():
    for mirroring in (True, False):
        preferences = preferences_from_subscription(
            {
                "excluded_tags": ["military", "sports"],
                "notify_mirrors_calendar": mirroring,
                "notify_excluded_categories": [],
                "notify_excluded_sequences": [],
            }
        )
        assert preferences.notify.tags == preferences.calendar.tags


def test_missing_columns_fall_back_to_pre_feature_behaviour():
    # The un-migrated-database case: the cron job must degrade, not explode.
    preferences = preferences_from_subscription({})

    assert preferences.calendar.categories == tuple(CATEGORY_NAMES)
    assert preferences.calendar.tags == tuple(TAG_TAXONOMY)
    assert preferences.calendar.sequences == ()
    assert preferences.notify_mirrors_calendar is True
    assert preferences.notify == preferences.calendar


def test_null_columns_are_treated_as_missing():
    preferences = preferences_from_subscription(
        {
            "excluded_categories": None,
            "excluded_tags": None,
            "included_sequences": None,
            "notify_mirrors_calendar": None,
            "notify_excluded_categories": None,
            "notify_excluded_sequences": None,
        }
    )

    assert preferences.calendar.categories == tuple(CATEGORY_NAMES)
    assert preferences.notify_mirrors_calendar is True


def test_unknown_stored_names_are_ignored():
    preferences = preferences_from_subscription(
        {"included_sequences": ["Powers of 2", "Perfect fifths"]}
    )

    assert preferences.calendar.sequences == ("Powers of 2",)


def test_an_unmarked_sequence_never_notifies_even_though_the_override_allows_it():
    # Both override columns are exclusions, so an empty notify_excluded_sequences
    # means "every sequence may notify". Opt-in is enforced one level up, by the
    # CALENDAR axis: a sequence nobody marked is not in calendar.sequences, and
    # the intersection makes the override's permission unreachable. This is why
    # the override does not need inclusion semantics of its own.
    preferences = preferences_from_subscription(
        {
            "excluded_categories": ["Sport"],
            "notify_mirrors_calendar": False,
            "notify_excluded_categories": ["Sport"],
            "notify_excluded_sequences": [],
        }
    )

    assert set(preferences.notify.categories) == set(CATEGORY_NAMES) - {"Sport"}
    assert preferences.notify.sequences == ()
    assert set(SEQUENCE_TAXONOMY) - set(preferences.calendar.sequences) == set(SEQUENCE_TAXONOMY)


def test_notify_override_is_not_a_channel_selection():
    # NotifyOverride deliberately has no `tags` field - a notification-only tag
    # selection is a state the model forbids.
    assert not hasattr(NotifyOverride(categories=(), sequences=()), "tags")


SPORT = Row(CATEGORY, "Sport")
SCIENCE = Row(CATEGORY, "Science & Technology")
PRIMES = Row(SEQUENCE, "Primes")


def test_all_rows_is_the_eight_categories_then_the_eight_sequences():
    rows = all_rows()

    assert len(rows) == 16
    assert rows[:8] == tuple(Row(CATEGORY, name) for name in CATEGORY_NAMES)
    assert rows[8:] == tuple(Row(SEQUENCE, name) for name in SEQUENCE_TAXONOMY)


def test_toggle_calendar_removes_and_restores_a_category():
    preferences = default_preferences()
    assert is_on_calendar(preferences, SPORT)

    without = toggle_calendar(preferences, SPORT)
    assert not is_on_calendar(without, SPORT)
    assert is_on_calendar(without, SCIENCE)

    restored = toggle_calendar(without, SPORT)
    assert is_on_calendar(restored, SPORT)


def test_toggle_calendar_on_a_sequence_opts_it_in():
    preferences = default_preferences()
    assert not is_on_calendar(preferences, PRIMES)

    with_primes = toggle_calendar(preferences, PRIMES)
    assert is_on_calendar(with_primes, PRIMES)
    assert with_primes.calendar.sequences == ("Primes",)


def test_while_mirroring_every_calendar_row_is_notifying():
    preferences = default_preferences()

    assert is_notifying(preferences, SPORT)
    assert not is_notifying(preferences, PRIMES)  # not on the calendar at all


def test_toggle_notify_mutes_one_row_and_breaks_the_mirror():
    preferences = default_preferences()

    muted = toggle_notify(preferences, SPORT)

    assert muted.notify_mirrors_calendar is False
    assert not is_notifying(muted, SPORT)
    # Seeded from the calendar, so nothing else moved.
    assert is_notifying(muted, SCIENCE)
    assert is_on_calendar(muted, SPORT)


def test_muting_one_row_leaves_every_other_marked_row_notifying():
    # Two categories already hidden, one sequence tracked. After muting Science,
    # the notify channel must equal the calendar minus Science - not the whole
    # taxonomy, and not an empty set.
    preferences = default_preferences()
    preferences = toggle_calendar(preferences, SPORT)
    preferences = toggle_calendar(preferences, Row(CATEGORY, "Disasters"))
    preferences = toggle_calendar(preferences, PRIMES)

    muted = toggle_notify(preferences, SCIENCE)

    assert set(muted.notify.categories) == set(muted.calendar.categories) - {"Science & Technology"}
    assert muted.notify.sequences == ("Primes",)


def test_toggle_notify_twice_restores_the_row_but_leaves_the_mirror_off():
    preferences = toggle_notify(default_preferences(), SPORT)

    unmuted = toggle_notify(preferences, SPORT)

    assert is_notifying(unmuted, SPORT)
    assert unmuted.notify_mirrors_calendar is False


def test_clicking_a_dim_bell_turns_both_channels_on_without_breaking_the_mirror():
    preferences = default_preferences()
    assert not is_on_calendar(preferences, PRIMES)

    lit = toggle_notify(preferences, PRIMES)

    assert is_on_calendar(lit, PRIMES)
    assert is_notifying(lit, PRIMES)
    # "On for both channels" is what mirroring already means - nothing to override.
    assert lit.notify_mirrors_calendar is True


def test_a_row_turned_off_keeps_its_notify_state_for_when_it_comes_back():
    preferences = toggle_notify(default_preferences(), SPORT)   # mute Sport
    hidden = toggle_calendar(preferences, SPORT)                # then hide it

    assert not is_notifying(hidden, SPORT)

    back = toggle_calendar(hidden, SPORT)
    assert is_on_calendar(back, SPORT)
    assert not is_notifying(back, SPORT)


def test_unticking_the_mirror_never_silently_mutes_a_marked_sequence():
    # Regression. With the override stored as inclusions, nothing maintained it
    # while mirroring, so every marked sequence was absent from it and unticking
    # the toggle silenced them all at once with no warning.
    preferences = toggle_calendar(default_preferences(), PRIMES)
    assert is_notifying(preferences, PRIMES)

    unmirrored = set_mirror(preferences, False)

    assert is_on_calendar(unmirrored, PRIMES)
    assert is_notifying(unmirrored, PRIMES)


def test_unticking_the_mirror_leaves_every_marked_row_notifying():
    # The same property for categories, and for the panel as a whole: breaking
    # the mirror changes nothing until a bell is actually clicked.
    preferences = toggle_calendar(default_preferences(), PRIMES)

    unmirrored = set_mirror(preferences, False)

    assert unmirrored.notify == unmirrored.calendar


def test_set_mirror_back_on_does_not_clear_the_override():
    muted = toggle_notify(default_preferences(), SPORT)

    mirrored = set_mirror(muted, True)
    assert mirrored.notify_mirrors_calendar is True
    assert is_notifying(mirrored, SPORT)

    # Toggling twice within a session is not destructive.
    again = set_mirror(mirrored, False)
    assert not is_notifying(again, SPORT)


def test_toggle_tag_narrows_both_channels():
    preferences = toggle_tag(default_preferences(), "military")

    assert "military" not in preferences.calendar.tags
    assert "military" not in preferences.notify.tags


def test_tags_for_category_returns_the_configured_tags():
    assert tags_for_category("War & Conflict") == ("military",)
    assert tags_for_category("Science & Technology") == (
        "science",
        "technology",
        "engineering",
        "health",
    )


def test_preferences_to_columns_stores_exclusions_for_categories_and_tags():
    preferences = toggle_calendar(default_preferences(), SPORT)
    preferences = toggle_tag(preferences, "military")

    columns = preferences_to_columns(preferences)

    assert columns["excluded_categories"] == ["Sport"]
    assert columns["excluded_tags"] == ["military"]


def test_preferences_to_columns_stores_inclusions_for_sequences():
    preferences = toggle_calendar(default_preferences(), PRIMES)

    columns = preferences_to_columns(preferences)

    assert columns["included_sequences"] == ["Primes"]


def test_preferences_to_columns_writes_the_mirror_flag_and_override():
    preferences = toggle_notify(default_preferences(), SPORT)

    columns = preferences_to_columns(preferences)

    assert columns["notify_mirrors_calendar"] is False
    assert columns["notify_excluded_categories"] == ["Sport"]
    assert columns["notify_excluded_sequences"] == []


def test_a_mute_survives_a_save_reload_while_mirroring():
    # The property the design ruled on twice, exercised across the DATABASE and
    # with the mirror ON at write time - the branch the plain round-trip test
    # below never reaches, because toggle_notify always leaves the mirror off.
    muted = toggle_notify(default_preferences(), SPORT)
    mirrored = set_mirror(muted, True)

    reloaded = preferences_from_subscription(preferences_to_columns(mirrored))

    # While mirroring, Sport notifies - that is what mirroring means.
    assert is_notifying(reloaded, SPORT)
    # But the mute was preserved underneath, and unticking reveals it again.
    assert not is_notifying(set_mirror(reloaded, False), SPORT)


def test_the_override_is_written_unchanged_whether_or_not_mirroring():
    # The override column must not depend on the mirror flag. A mirror-dependent
    # write is how the save/reload above loses the mute.
    muted = toggle_notify(default_preferences(), SPORT)

    off = preferences_to_columns(muted)["notify_excluded_categories"]
    on = preferences_to_columns(set_mirror(muted, True))["notify_excluded_categories"]

    assert off == on == ["Sport"]


def test_preferences_to_columns_round_trips():
    preferences = default_preferences()
    preferences = toggle_calendar(preferences, Row(CATEGORY, "Disasters"))
    preferences = toggle_calendar(preferences, PRIMES)
    preferences = toggle_tag(preferences, "military")
    preferences = toggle_notify(preferences, SCIENCE)

    assert preferences_from_subscription(preferences_to_columns(preferences)) == preferences


def test_preferences_to_columns_writes_all_six_columns():
    columns = preferences_to_columns(default_preferences())

    assert set(columns) == {
        "excluded_categories",
        "excluded_tags",
        "included_sequences",
        "notify_mirrors_calendar",
        "notify_excluded_categories",
        "notify_excluded_sequences",
    }
