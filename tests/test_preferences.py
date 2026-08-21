from core.config import CATEGORY_NAMES, SEQUENCE_TAXONOMY, TAG_TAXONOMY
from core.preferences import (
    NotifyOverride,
    default_preferences,
    preferences_from_subscription,
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
            "notify_included_sequences": [],
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
            "notify_included_sequences": ["Powers of 2"],
        }
    )

    assert "Sport" not in preferences.notify.categories
    assert "War & Conflict" not in preferences.notify.categories
    assert "Science & Technology" in preferences.notify.categories
    assert preferences.notify.sequences == ("Powers of 2",)


def test_notify_is_intersected_with_calendar_on_read():
    # "Sport" is off the calendar entirely, but the override still names it as
    # notifying. The stale entry must not resurrect it.
    preferences = preferences_from_subscription(
        {
            "excluded_categories": ["Sport"],
            "included_sequences": [],
            "notify_mirrors_calendar": False,
            "notify_excluded_categories": [],
            "notify_included_sequences": ["Primes"],
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
                "notify_included_sequences": [],
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
            "notify_included_sequences": None,
        }
    )

    assert preferences.calendar.categories == tuple(CATEGORY_NAMES)
    assert preferences.notify_mirrors_calendar is True


def test_unknown_stored_names_are_ignored():
    preferences = preferences_from_subscription(
        {"included_sequences": ["Powers of 2", "Perfect fifths"]}
    )

    assert preferences.calendar.sequences == ("Powers of 2",)


def test_a_new_category_notifies_by_default_but_a_new_sequence_does_not():
    # Exclusion semantics for categories mean anything unnamed is kept; inclusion
    # semantics for sequences mean anything unnamed is off. This is the whole
    # reason the two axes are stored differently.
    preferences = preferences_from_subscription(
        {
            "excluded_categories": ["Sport"],
            "notify_mirrors_calendar": False,
            "notify_excluded_categories": ["Sport"],
            "notify_included_sequences": [],
        }
    )

    assert set(preferences.notify.categories) == set(CATEGORY_NAMES) - {"Sport"}
    assert preferences.notify.sequences == ()
    assert set(SEQUENCE_TAXONOMY) - set(preferences.calendar.sequences) == set(SEQUENCE_TAXONOMY)


def test_notify_override_is_not_a_channel_selection():
    # NotifyOverride deliberately has no `tags` field - a notification-only tag
    # selection is a state the model forbids.
    assert not hasattr(NotifyOverride(categories=(), sequences=()), "tags")
