# Two-Channel Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single event/anniversary filter into two channels — what is marked on the calendar, and what also pushes a notification — with the notification channel constrained to a subset of the calendar channel.

**Architecture:** A new pure module `src/core/preferences.py` owns the whole preference model: reading a subscription row into an immutable `Preferences`, the click rules, and writing back the six database columns. A new `src/app/filters.py` renders the Streamlit panel and holds no rules of its own. Existing matchers (`filter_events`, `anniversary_matches`) are unchanged and simply get called once per channel.

**Tech Stack:** Python 3.11+, Streamlit ≥1.45, Supabase (PostgREST via the `supabase` package), pytest. Tests use `unittest.mock` for the database and `streamlit.testing.v1.AppTest` for the panel.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-21-two-channel-filter-design.md`. Read it before starting.
- **Python ≥3.11**, `from __future__ import annotations` at the top of every module (matches every existing file).
- **No new dependencies.** `requirements.txt` and `pyproject.toml` are not modified by this plan.
- **`core/` must never import from `ingest/` or `app/`.** `core/preferences.py` must not import `streamlit`.
- **Docstring style:** a one-line summary, then a blank line, then prose explaining *why* — matching `core/matching.py` and `core/sequences.py`. Comments explain rationale, not mechanics.
- **The six subscription columns**, exact names: `excluded_categories`, `excluded_tags`, `included_sequences` (calendar channel, pre-existing); `notify_mirrors_calendar`, `notify_excluded_categories`, `notify_included_sequences` (notification channel, new).
- **Semantics are asymmetric on purpose.** Categories and tags are stored as **exclusions**; sequences are stored as **inclusions**. Do not "fix" this.
- **Every read of a subscription column must be defensive** — a missing key and a `None` value both mean the pre-feature default, because the cron job may run against a database that has not had the SQL applied.
- **Run tests with:** `pytest` from the repo root (`pyproject.toml` puts `src` on the path).
- **Taxonomy constants** live in `core/config.py`: `CATEGORY_NAMES` (8), `TAG_TAXONOMY` (20), `TAG_CATEGORIES` (dict), `SEQUENCE_TAXONOMY` (8), `DEFAULT_SEQUENCES` (first 4).

---

### Task 1: The `Preferences` model and reading it

**Files:**
- Create: `src/core/preferences.py`
- Test: `tests/test_preferences.py`

**Interfaces:**
- Consumes: `core.config.CATEGORY_NAMES`, `TAG_TAXONOMY`, `SEQUENCE_TAXONOMY`; `core.matching.included_from_excluded`
- Produces: `ChannelSelection(categories, tags, sequences)`, `NotifyOverride(categories, sequences)`, `Preferences(calendar, notify_mirrors_calendar, notify_override)` with a `.notify` property; `default_preferences()`; `preferences_from_subscription(subscription)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preferences.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_preferences.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.preferences'`

- [ ] **Step 3: Write the implementation**

Create `src/core/preferences.py`:

```python
"""The subscriber preference model: what is marked, and what also notifies.

Two channels over one set of rows. The calendar channel says which categories
and sequences are marked; the notification channel says which of those also push
a notification, and is always a subset of the calendar - you cannot be notified
about something you have hidden.

The notification channel is not stored directly. It is a mirror flag plus an
override, and the override is intersected with the calendar every time it is
read. That intersection is what enforces the subset rule without any data
cleanup: drop a category from the calendar months after muting a different one,
and the stale notify entry for it dies on read rather than leaking a
notification for a row the subscriber can no longer see.

Deliberately pure and Streamlit-free: the Streamlit app and the daily cron job
both build a Preferences from the same row, so they cannot disagree about what a
subscription means. The click rules live here too (toggle_calendar and friends),
so the panel in app/filters.py renders and delegates but decides nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Optional, Sequence, Tuple

from core.config import CATEGORY_NAMES, SEQUENCE_TAXONOMY, TAG_TAXONOMY
from core.matching import included_from_excluded

CATEGORY = "category"
SEQUENCE = "sequence"


@dataclass(frozen=True)
class ChannelSelection:
    """What one channel counts as a match."""

    categories: Tuple[str, ...]
    tags: Tuple[str, ...]
    sequences: Tuple[str, ...]


@dataclass(frozen=True)
class NotifyOverride:
    """Only the two axes the notification channel is allowed to differ on.

    Not a ChannelSelection: that type has a `tags` field, and a
    notification-only tag selection is a state the model forbids. Making it
    unrepresentable here is cheaper than validating against it everywhere
    downstream.
    """

    categories: Tuple[str, ...]
    sequences: Tuple[str, ...]


@dataclass(frozen=True)
class Preferences:
    """One subscriber's complete filter state, immutable.

    Every mutation helper below returns a new instance, so the Streamlit session
    holds one value that is replaced wholesale rather than a mutable object that
    several widgets could edit out from under each other.
    """

    calendar: ChannelSelection
    notify_mirrors_calendar: bool
    notify_override: NotifyOverride

    @property
    def notify(self) -> ChannelSelection:
        """The effective notification channel, with the subset invariant applied.

        While mirroring this is `calendar` exactly. Otherwise categories and
        sequences come from the override intersected with the calendar, and tags
        are always the calendar's - tags are not a per-channel axis.
        """
        if self.notify_mirrors_calendar:
            return self.calendar
        return ChannelSelection(
            categories=_ordered(self.notify_override.categories, CATEGORY_NAMES, self.calendar.categories),
            tags=self.calendar.tags,
            sequences=_ordered(self.notify_override.sequences, SEQUENCE_TAXONOMY, self.calendar.sequences),
        )


def _ordered(
    chosen: Sequence[str], taxonomy: Sequence[str], limit_to: Sequence[str]
) -> Tuple[str, ...]:
    """Entries of `taxonomy` that are in both `chosen` and `limit_to`, in taxonomy order.

    Ordering by the taxonomy rather than by either input means the result never
    depends on how a list happened to be stored, which keeps equality checks
    (and the unsaved-changes indicator in the panel) meaningful.
    """
    chosen_set = set(chosen or ())
    allowed = set(limit_to or ())
    return tuple(entry for entry in taxonomy if entry in chosen_set and entry in allowed)


def default_preferences() -> Preferences:
    """A visitor with no subscription: every category and tag, no sequences, mirroring.

    Matches what an anonymous visitor sees today - the whole corpus, and
    mathematical anniversaries off until they ask for them.
    """
    calendar = ChannelSelection(
        categories=tuple(CATEGORY_NAMES), tags=tuple(TAG_TAXONOMY), sequences=()
    )
    return Preferences(
        calendar=calendar,
        notify_mirrors_calendar=True,
        notify_override=NotifyOverride(categories=tuple(CATEGORY_NAMES), sequences=()),
    )


def preferences_from_subscription(subscription: Optional[Dict]) -> Preferences:
    """Build a Preferences from a subscription row, or the defaults from None.

    Every column is read defensively. A missing key and a null value both mean
    the pre-feature default, because the daily cron job may run against a
    database that has not had the new columns applied yet - and raising there
    would kill the whole run over one subscriber rather than degrade to the
    behaviour they already had.
    """
    if not subscription:
        return default_preferences()

    calendar = ChannelSelection(
        categories=tuple(
            included_from_excluded(subscription.get("excluded_categories") or [], CATEGORY_NAMES)
        ),
        tags=tuple(included_from_excluded(subscription.get("excluded_tags") or [], TAG_TAXONOMY)),
        sequences=_ordered(
            subscription.get("included_sequences") or [], SEQUENCE_TAXONOMY, SEQUENCE_TAXONOMY
        ),
    )

    mirrors = subscription.get("notify_mirrors_calendar")
    override = NotifyOverride(
        categories=tuple(
            included_from_excluded(
                subscription.get("notify_excluded_categories") or [], CATEGORY_NAMES
            )
        ),
        sequences=_ordered(
            subscription.get("notify_included_sequences") or [], SEQUENCE_TAXONOMY, SEQUENCE_TAXONOMY
        ),
    )

    return Preferences(
        calendar=calendar,
        # None (column absent, or null) means the column default, which is True.
        notify_mirrors_calendar=True if mirrors is None else bool(mirrors),
        notify_override=override,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_preferences.py -v`
Expected: PASS — 11 tests

- [ ] **Step 5: Run the whole suite to confirm nothing regressed**

Run: `pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/core/preferences.py tests/test_preferences.py
git commit -m "feat: add a two-channel subscriber preference model"
```

---

### Task 2: Rows and the click rules

**Files:**
- Modify: `src/core/preferences.py` (append)
- Test: `tests/test_preferences.py` (append)

**Interfaces:**
- Consumes: everything from Task 1
- Produces: `Row(kind, name)`; `all_rows()`; `is_on_calendar(preferences, row)`; `is_notifying(preferences, row)`; `toggle_calendar(preferences, row)`; `toggle_notify(preferences, row)`; `toggle_tag(preferences, tag)`; `set_mirror(preferences, mirroring)`; `tags_for_category(category)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preferences.py`:

```python
from core.preferences import (
    CATEGORY,
    SEQUENCE,
    Row,
    all_rows,
    is_notifying,
    is_on_calendar,
    set_mirror,
    tags_for_category,
    toggle_calendar,
    toggle_notify,
    toggle_tag,
)

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


def test_breaking_the_mirror_seeds_the_override_from_the_calendar():
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_preferences.py -v`
Expected: FAIL — `ImportError: cannot import name 'Row' from 'core.preferences'`

- [ ] **Step 3: Write the implementation**

Append to `src/core/preferences.py` (and add `TAG_CATEGORIES` to the `core.config` import):

```python
@dataclass(frozen=True)
class Row:
    """One filterable thing: a coarse category or an integer sequence.

    A single type for both so the panel renders all sixteen in one loop, rather
    than duplicating the row markup for each taxonomy. `kind` is CATEGORY or
    SEQUENCE.
    """

    kind: str
    name: str


def all_rows() -> Tuple[Row, ...]:
    """Every row, categories first, each taxonomy in its published order."""
    return tuple(Row(CATEGORY, name) for name in CATEGORY_NAMES) + tuple(
        Row(SEQUENCE, name) for name in SEQUENCE_TAXONOMY
    )


def tags_for_category(category: str) -> Tuple[str, ...]:
    """The fine tags inside one category, in taxonomy order.

    The panel shows a narrowing popover only where this returns more than one
    tag - for Sport, Disasters and War & Conflict it returns a single tag and
    the control would be a no-op.
    """
    return tuple(TAG_CATEGORIES.get(category, ()))


def _taxonomy_for(row: Row) -> Sequence[str]:
    return CATEGORY_NAMES if row.kind == CATEGORY else SEQUENCE_TAXONOMY


def _selected_for(selection: ChannelSelection, row: Row) -> Tuple[str, ...]:
    return selection.categories if row.kind == CATEGORY else selection.sequences


def is_on_calendar(preferences: Preferences, row: Row) -> bool:
    """Is this row marked on the calendar?"""
    return row.name in _selected_for(preferences.calendar, row)


def is_notifying(preferences: Preferences, row: Row) -> bool:
    """Does this row also push a notification? False whenever it is off the calendar."""
    return row.name in _selected_for(preferences.notify, row)


def _with_row(values: Tuple[str, ...], row: Row, present: bool) -> Tuple[str, ...]:
    """Add or remove row.name from `values`, re-sorted into taxonomy order."""
    names = set(values)
    if present:
        names.add(row.name)
    else:
        names.discard(row.name)
    return tuple(entry for entry in _taxonomy_for(row) if entry in names)


def _replace_channel(selection: ChannelSelection, row: Row, values: Tuple[str, ...]) -> ChannelSelection:
    if row.kind == CATEGORY:
        return replace(selection, categories=values)
    return replace(selection, sequences=values)


def _replace_override(override: NotifyOverride, row: Row, values: Tuple[str, ...]) -> NotifyOverride:
    if row.kind == CATEGORY:
        return replace(override, categories=values)
    return replace(override, sequences=values)


def toggle_calendar(preferences: Preferences, row: Row) -> Preferences:
    """Mark or unmark a row on the calendar.

    Its notification state is left alone. While the row is off, the intersection
    in Preferences.notify hides that state anyway, so turning the row back on
    restores exactly what it had rather than silently re-enabling a notification
    the subscriber had muted.
    """
    turning_on = not is_on_calendar(preferences, row)
    values = _with_row(_selected_for(preferences.calendar, row), row, turning_on)
    return replace(preferences, calendar=_replace_channel(preferences.calendar, row, values))


def _seeded_override(preferences: Preferences) -> NotifyOverride:
    """The override that reproduces the current effective notify channel.

    Called when the mirror is about to break, so that the instant it drops
    nothing has changed except the row the user clicked - the panel never
    silently rearranges itself around them.
    """
    current = preferences.notify
    return NotifyOverride(categories=current.categories, sequences=current.sequences)


def toggle_notify(preferences: Preferences, row: Row) -> Preferences:
    """Toggle notifications for one row, from any of its three visual states.

    A dim bell (the row is off the calendar) turns both channels on and leaves
    the mirror alone: "on for both" is what mirroring already means, so there is
    nothing to override. Muting a live row is the only case that breaks the
    mirror, and it seeds the override from the calendar first.
    """
    if not is_on_calendar(preferences, row):
        lit = toggle_calendar(preferences, row)
        if lit.notify_mirrors_calendar:
            return lit
        values = _with_row(_selected_for(lit.notify, row), row, True)
        return replace(lit, notify_override=_replace_override(lit.notify_override, row, values))

    broken = replace(
        preferences,
        notify_mirrors_calendar=False,
        notify_override=(
            _seeded_override(preferences)
            if preferences.notify_mirrors_calendar
            else preferences.notify_override
        ),
    )
    values = _with_row(
        _selected_for(broken.notify, row), row, not is_notifying(broken, row)
    )
    return replace(broken, notify_override=_replace_override(broken.notify_override, row, values))


def set_mirror(preferences: Preferences, mirroring: bool) -> Preferences:
    """Turn mirroring on or off.

    Turning it on does not clear the override, so toggling twice within a
    session is not destructive.
    """
    if mirroring:
        return replace(preferences, notify_mirrors_calendar=True)
    return replace(
        preferences,
        notify_mirrors_calendar=False,
        notify_override=(
            _seeded_override(preferences)
            if preferences.notify_mirrors_calendar
            else preferences.notify_override
        ),
    )


def toggle_tag(preferences: Preferences, tag: str) -> Preferences:
    """Include or exclude one fine tag. Narrows both channels - tags are not per-channel."""
    tags = set(preferences.calendar.tags)
    tags.discard(tag) if tag in tags else tags.add(tag)
    ordered = tuple(entry for entry in TAG_TAXONOMY if entry in tags)
    return replace(preferences, calendar=replace(preferences.calendar, tags=ordered))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_preferences.py -v`
Expected: PASS — 23 tests

- [ ] **Step 5: Commit**

```bash
git add src/core/preferences.py tests/test_preferences.py
git commit -m "feat: add rows and click rules to the preference model"
```

---

### Task 3: Writing preferences back to the database

**Files:**
- Modify: `src/core/preferences.py` (append `preferences_to_columns`)
- Modify: `src/core/db.py:100-150` (`create_subscription`, `update_subscription_filters`)
- Test: `tests/test_preferences.py` (append), `tests/test_db.py` (rewrite three tests)

**Interfaces:**
- Consumes: `Preferences` from Tasks 1–2
- Produces: `preferences_to_columns(preferences) -> Dict[str, object]`; `create_subscription(birthday, preferences=None)`; `update_subscription_filters(token, preferences)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preferences.py`:

```python
from core.preferences import preferences_to_columns


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
    assert columns["notify_included_sequences"] == []


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
        "notify_included_sequences",
    }
```

Replace the last three tests in `tests/test_db.py` (from `test_create_subscription_defaults_to_no_filters_and_no_sequences` to the end of the file) with:

```python
def test_create_subscription_without_preferences_uses_the_defaults():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1))

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == []
    assert inserted["excluded_categories"] == []
    # Inclusions, not exclusions: empty means mathematical anniversaries are off.
    assert inserted["included_sequences"] == []
    # Mirroring is the default, so a new subscriber's notifications follow their calendar.
    assert inserted["notify_mirrors_calendar"] is True


def test_create_subscription_stores_all_six_preference_columns():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    preferences = toggle_notify(
        toggle_tag(toggle_calendar(default_preferences(), Row(CATEGORY, "Sport")), "military"),
        Row(CATEGORY, "Science & Technology"),
    )

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1), preferences)

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_categories"] == ["Sport"]
    assert inserted["excluded_tags"] == ["military"]
    assert inserted["notify_mirrors_calendar"] is False
    assert inserted["notify_excluded_categories"] == ["Sport", "Science & Technology"]


def test_update_subscription_filters_writes_all_six_columns_for_the_right_token():
    mock_client = MagicMock()
    preferences = toggle_calendar(default_preferences(), Row(SEQUENCE, "Primes"))

    with patch("core.db.get_client", return_value=mock_client):
        update_subscription_filters("tok123", preferences)

    mock_client.table.assert_called_with("subscriptions")
    mock_client.table.return_value.update.assert_called_with(preferences_to_columns(preferences))
    mock_client.table.return_value.update.return_value.eq.assert_called_with("token", "tok123")
    mock_client.table.return_value.update.return_value.eq.return_value.execute.assert_called_once()
```

Update the imports at the top of `tests/test_db.py`:

```python
from datetime import date
from unittest.mock import MagicMock, patch

from core.db import create_subscription, fetch_events, update_subscription_filters
from core.preferences import (
    CATEGORY,
    SEQUENCE,
    Row,
    default_preferences,
    preferences_to_columns,
    toggle_calendar,
    toggle_notify,
    toggle_tag,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_preferences.py tests/test_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'preferences_to_columns'`

- [ ] **Step 3: Add `preferences_to_columns`**

Append to `src/core/preferences.py`:

```python
def preferences_to_columns(preferences: Preferences) -> Dict[str, object]:
    """The six subscription columns for a Preferences, ready to insert or update.

    Note the asymmetry, which is deliberate and mirrors how each axis is read:
    categories and tags are stored as EXCLUSIONS, so an entry added to the
    taxonomy later is visible by default rather than silently hidden; sequences
    are stored as INCLUSIONS, so a sequence added later stays off rather than
    pushing itself at everyone.

    The override is written even while mirroring. It costs nothing, and it means
    a subscriber who turns mirroring off and on again does not lose the
    selection they had.
    """
    return {
        "excluded_categories": excluded_from_included(preferences.calendar.categories, CATEGORY_NAMES),
        "excluded_tags": excluded_from_included(preferences.calendar.tags, TAG_TAXONOMY),
        "included_sequences": list(preferences.calendar.sequences),
        "notify_mirrors_calendar": preferences.notify_mirrors_calendar,
        "notify_excluded_categories": excluded_from_included(
            preferences.notify_override.categories, CATEGORY_NAMES
        ),
        "notify_included_sequences": list(preferences.notify_override.sequences),
    }
```

Extend the `core.matching` import at the top of the file to
`from core.matching import excluded_from_included, included_from_excluded`.

- [ ] **Step 4: Rewrite the two `db.py` functions**

In `src/core/db.py`, replace `create_subscription` and `update_subscription_filters` with:

```python
def create_subscription(birthday: date, preferences: Optional[Preferences] = None) -> Dict:
    """Create a new subscription (magic-link token + ntfy topic) for a birthday.

    The visitor's current filter panel state is carried straight into the new
    row, so filter-then-subscribe is one action - bells included, which is why
    an anonymous visitor can set up "notify me about science only" in one visit.
    """
    client = get_client()
    row = {
        "token": secrets.token_urlsafe(12),
        "ntfy_topic": f"achage-{secrets.token_urlsafe(9)}",
        "birthday": birthday.isoformat(),
        **preferences_to_columns(preferences or default_preferences()),
    }
    response = client.table("subscriptions").insert(row).execute()
    return response.data[0]


def update_subscription_filters(token: str, preferences: Preferences) -> None:
    """Overwrite a subscription's stored calendar and notification preferences in one write."""
    client = get_client()
    client.table("subscriptions").update(preferences_to_columns(preferences)).eq(
        "token", token
    ).execute()
```

Add to `db.py`'s imports: `from core.preferences import Preferences, default_preferences, preferences_to_columns`.

> **Import-cycle check:** `core/preferences.py` imports from `core/config.py` and `core/matching.py` only. `core/matching.py` imports from `core/config.py` only. So `db.py → preferences → matching → config` is acyclic. Do not add a `db` import to `preferences.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_preferences.py tests/test_db.py -v`
Expected: PASS — 28 preference tests, 7 db tests

- [ ] **Step 6: Commit**

```bash
git add src/core/preferences.py src/core/db.py tests/test_preferences.py tests/test_db.py
git commit -m "feat: persist the two-channel preference model"
```

---

### Task 4: Notifications use the notify channel

**Files:**
- Modify: `scripts/send_daily_notifications.py:1-40` (docstring, imports), `:88-110` (`main`)
- Test: `tests/test_send_daily_notifications.py` (create)

**Interfaces:**
- Consumes: `preferences_from_subscription` (Task 1), `Preferences.notify` (Task 1)
- Produces: no new public API; `main()` keeps its signature

- [ ] **Step 1: Write the failing test**

Create `tests/test_send_daily_notifications.py`:

```python
"""The cron job's filtering, exercised without touching the network.

Only the selection logic is under test - _send_ntfy_notification and
_send_anniversary_notification are patched out, and what matters is which
matches reach them.
"""

from datetime import date, timedelta
from unittest.mock import patch

import scripts.send_daily_notifications as notify


def _event(name, tags, age_days):
    return {"id": 1, "name": name, "tags": tags, "age_days": age_days, "event_phrase": "x"}


def _run(subscription, events, age_days):
    birthday = (date.today() - timedelta(days=age_days)).isoformat()
    subscription = {"ntfy_topic": "t", "token": "tok", "birthday": birthday, **subscription}

    with patch.object(notify, "fetch_all_subscriptions", return_value=[subscription]), \
         patch.object(notify, "MATCHERS", [lambda age: list(events)]), \
         patch.object(notify, "_send_ntfy_notification") as send_event, \
         patch.object(notify, "_send_anniversary_notification") as send_anniversary:
        notify.main()

    sent_events = [call.args[1]["name"] for call in send_event.call_args_list]
    sent_sequences = [call.args[1]["sequence"] for call in send_anniversary.call_args_list]
    return sent_events, sent_sequences


def test_a_mirroring_subscriber_is_notified_about_everything_they_marked():
    # 2048 is a power of two, so the anniversary matcher has something to find.
    events = [_event("Ada", ["science"], 2048), _event("Wellington", ["military"], 2048)]
    subscription = {
        "excluded_categories": ["War & Conflict"],
        "included_sequences": ["Powers of 2"],
        "notify_mirrors_calendar": True,
    }

    sent_events, sent_sequences = _run(subscription, events, 2048)

    assert sent_events == ["Ada"]
    assert sent_sequences == ["Powers of 2"]


def test_a_subscriber_can_mark_a_sequence_without_being_notified_about_it():
    subscription = {
        "included_sequences": ["Powers of 2"],
        "notify_mirrors_calendar": False,
        "notify_excluded_categories": [],
        "notify_included_sequences": [],
    }

    _, sent_sequences = _run(subscription, [], 2048)

    assert sent_sequences == []


def test_a_subscriber_can_be_notified_about_one_category_only():
    events = [_event("Ada", ["science"], 2048), _event("Wellington", ["military"], 2048)]
    subscription = {
        "excluded_categories": [],
        "notify_mirrors_calendar": False,
        "notify_excluded_categories": [
            name for name in ["Sport", "Disasters", "Exploration & Space", "Arts & Culture",
                              "Society & Belief", "War & Conflict", "Politics & Power"]
        ],
        "notify_included_sequences": [],
    }

    sent_events, _ = _run(subscription, events, 2048)

    assert sent_events == ["Ada"]


def test_an_unmigrated_row_falls_back_to_the_calendar_channel():
    # No notify_* columns at all: the pre-feature behaviour, not silence.
    events = [_event("Ada", ["science"], 2048)]

    sent_events, _ = _run({"excluded_categories": []}, events, 2048)

    assert sent_events == ["Ada"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_send_daily_notifications.py -v`
Expected: FAIL — `test_a_subscriber_can_mark_a_sequence_without_being_notified_about_it` and `test_a_subscriber_can_be_notified_about_one_category_only` fail, because the script still filters on the calendar columns.

> If the import itself fails with `ModuleNotFoundError: No module named 'scripts'`, add an empty `scripts/__init__.py` and include it in the commit.

- [ ] **Step 3: Rewrite the filtering in `main()`**

In `scripts/send_daily_notifications.py`, replace the imports:

```python
from core.db import fetch_all_subscriptions, fetch_events
from core.matching import events_by_age_days, filter_events, full_sentence
from core.preferences import preferences_from_subscription
from core.sequences import anniversary_matches, anniversary_sentence
```

and replace the body of the per-subscription loop in `main()`:

```python
    for subscription in subscriptions:
        birthday = date.fromisoformat(subscription["birthday"])
        age_days = (today - birthday).days
        notify_channel = preferences_from_subscription(subscription).notify

        matches: List[Dict] = []
        for matcher in MATCHERS:
            matches.extend(matcher(age_days))
        matches = filter_events(matches, notify_channel.categories, notify_channel.tags)

        for event in matches:
            _send_ntfy_notification(subscription["ntfy_topic"], event, subscription["token"])
            notified += 1

        for anniversary in anniversary_matches(age_days, notify_channel.sequences):
            _send_anniversary_notification(
                subscription["ntfy_topic"], anniversary, subscription["token"]
            )
            notified += 1
```

Replace the second and fourth paragraphs of the module docstring with:

```
Each subscriber's matches are filtered through the NOTIFICATION channel of
core.preferences - which is their calendar selection unless they have turned
mirroring off, in which case it is their narrower notification selection
intersected with it. A subscriber can therefore mark prime days on the calendar
without being pushed one every nine days, or browse every category while being
notified about science alone.

Mathematical anniversaries are computed rather than looked up, so they are
gathered separately from MATCHERS - they share no fields with an event - but
they are filtered through the same notification channel.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_send_daily_notifications.py -v`
Expected: PASS — 4 tests

- [ ] **Step 5: Run the whole suite**

Run: `pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/send_daily_notifications.py tests/test_send_daily_notifications.py
git commit -m "feat: filter daily notifications through the notification channel"
```

---

### Task 5: The filter panel

**Files:**
- Create: `src/app/filters.py`
- Create: `tests/apps/filter_panel_app.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: everything from `core.preferences`
- Produces: `render_filter_panel(subscription: Optional[Dict]) -> Preferences`, and the session-state key `"filter_preferences"`

- [ ] **Step 1: Write the harness app**

Create `tests/apps/filter_panel_app.py`. AppTest cannot point at `src/app/ui.py`, which opens a
Supabase connection at import time; this renders only the panel.

```python
"""Minimal Streamlit app that renders nothing but the filter panel, for AppTest.

Kept out of src/ because it is a test fixture, not shipped code.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from app.filters import render_filter_panel

preferences = render_filter_panel(None)

# Surfaced so assertions can read the resolved state without reaching into
# session_state internals.
st.text(f"calendar_categories={','.join(preferences.calendar.categories)}")
st.text(f"notify_categories={','.join(preferences.notify.categories)}")
st.text(f"calendar_sequences={','.join(preferences.calendar.sequences)}")
st.text(f"notify_sequences={','.join(preferences.notify.sequences)}")
st.text(f"mirroring={preferences.notify_mirrors_calendar}")
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_filters.py`:

```python
"""Wiring tests for the panel: a click reaches the right rule.

The rules themselves are unit-tested in test_preferences.py without a Streamlit
runtime; these only prove the buttons are connected to them.
"""

from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent / "apps" / "filter_panel_app.py")


def _value(app, prefix):
    for element in app.text:
        if element.value.startswith(prefix):
            return element.value[len(prefix):]
    raise AssertionError(f"no text element starting with {prefix!r}")


def _button(app, key):
    # Looked up by iterating rather than by an accessor: AppTest's element lists
    # index positionally, and a key lookup is the stable thing to assert on.
    return next(button for button in app.button if button.key == key)


def _toggle(app, key):
    return next(toggle for toggle in app.toggle if toggle.key == key)


def _run():
    app = AppTest.from_file(APP)
    app.run()
    return app


def test_the_panel_starts_with_everything_marked_and_mirroring():
    app = _run()

    assert "Sport" in _value(app, "calendar_categories=")
    assert _value(app, "mirroring=") == "True"


def test_clicking_a_row_drops_it_from_the_calendar():
    app = _run()

    _button(app, "row-category-Sport").click().run()

    assert "Sport" not in _value(app, "calendar_categories=")
    assert "Disasters" in _value(app, "calendar_categories=")


def test_clicking_a_bell_mutes_the_row_and_breaks_the_mirror():
    app = _run()

    _button(app, "bell-category-Sport").click().run()

    assert _value(app, "mirroring=") == "False"
    assert "Sport" in _value(app, "calendar_categories=")
    assert "Sport" not in _value(app, "notify_categories=")


def test_clicking_a_dim_bell_turns_both_channels_on():
    app = _run()

    _button(app, "bell-sequence-Primes").click().run()

    assert "Primes" in _value(app, "calendar_sequences=")
    assert "Primes" in _value(app, "notify_sequences=")
    assert _value(app, "mirroring=") == "True"


def test_the_mirror_toggle_restores_mirroring():
    app = _run()
    _button(app, "bell-category-Sport").click().run()
    assert _value(app, "mirroring=") == "False"

    _toggle(app, "notify_mirror").set_value(True).run()

    assert _value(app, "mirroring=") == "True"
    assert "Sport" in _value(app, "notify_categories=")


def test_single_tag_categories_get_no_narrowing_popover():
    app = _run()
    keys = [button.key for button in app.button]

    # War & Conflict maps to exactly one tag, so a narrowing control is a no-op.
    assert not any(key == "tag-military" for key in keys)
    assert any(key == "tag-science" for key in keys)
```

> **If this last test errors rather than fails:** AppTest renders `st.popover` children into the
> element tree eagerly, so the tag buttons should appear in `app.button` without the popover being
> "opened". If they turn out not to, assert on `app.popover` instead — that `Science & Technology`
> produces one and `War & Conflict` does not. The behaviour under test is the same either way.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_filters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.filters'`

- [ ] **Step 4: Write the panel**

Create `src/app/filters.py`:

```python
"""The filter panel: sixteen rows, two channels, one Preferences.

Holds no rules. Every click is handed to a function in core.preferences, which
returns a new Preferences that replaces the one in session state. That is why
every control here is a BUTTON rather than a stateful widget: buttons carry no
widget state of their own, so there is exactly one source of truth and no
mirroring between session values and widget keys.

That last point is not stylistic. The version of this panel that used
st.multiselect needed a shadow key and a mid-script re-read to work around
Streamlit hashing a widget's `default` into its element id - feeding last run's
selection back in changed the id and silently dropped every second edit.
"""

from __future__ import annotations

from typing import Dict, Optional

import streamlit as st

from core.config import TAG_CATEGORIES
from core.preferences import (
    CATEGORY,
    SEQUENCE,
    Preferences,
    Row,
    all_rows,
    is_notifying,
    is_on_calendar,
    preferences_from_subscription,
    set_mirror,
    tags_for_category,
    toggle_calendar,
    toggle_notify,
    toggle_tag,
)

STATE_KEY = "filter_preferences"
SAVED_KEY = "filter_preferences_saved"
MIRROR_KEY = "notify_mirror"

GROUP_LABELS = {CATEGORY: "Historical events", SEQUENCE: "Mathematical anniversaries"}

BELL = "🔔"


def _seed(subscription: Optional[Dict]) -> None:
    """Load the subscription into session state once per session.

    Seeded once rather than every run so a rerun triggered by a click does not
    clobber the selection that click just made.
    """
    if STATE_KEY not in st.session_state:
        preferences = preferences_from_subscription(subscription)
        st.session_state[STATE_KEY] = preferences
        st.session_state[SAVED_KEY] = preferences
        st.session_state[MIRROR_KEY] = preferences.notify_mirrors_calendar


def _state_digits(preferences: Preferences, row: Row) -> str:
    """This row's two visual states as two digits: on-calendar, then notifying.

    "11" is marked and notifying, "10" marked but muted, "00" off. ("01" cannot
    occur - that is the subset invariant.)
    """
    return f"{int(is_on_calendar(preferences, row))}{int(is_notifying(preferences, row))}"


def _row_key(preferences: Preferences, row: Row) -> str:
    """Container key for the row's label, for the CSS to match on."""
    return f"filter-row-{_state_digits(preferences, row)}-{row.kind}-{row.name}"


def _bell_key(preferences: Preferences, row: Row) -> str:
    """Container key for the row's bell.

    A separate container from the label rather than one wrapping both: the label
    and the bell need different rules, and they sit in different st.columns, so
    a CSS selector scoped to the row alone cannot tell them apart.
    """
    return f"filter-bell-{_state_digits(preferences, row)}-{row.kind}-{row.name}"


def _apply(preferences: Preferences) -> None:
    """Store a new Preferences and rerun.

    The mirror toggle's widget key is written too. Streamlit prefers a keyed
    widget's stored state over the `value=` argument, so a bell click that
    breaks the mirror would otherwise leave the toggle drawn in its old
    position - the state and the control would disagree until the next manual
    click.
    """
    st.session_state[STATE_KEY] = preferences
    st.session_state[MIRROR_KEY] = preferences.notify_mirrors_calendar
    st.rerun()


def _render_tag_popover(preferences: Preferences, category: str) -> None:
    """The narrowing control for one category, shown only where it can do something.

    Sport, Disasters and War & Conflict map to a single tag each, so a popover
    there would offer one checkbox that duplicates the row itself.
    """
    tags = tags_for_category(category)
    if len(tags) < 2:
        return
    with st.popover(f"{len(tags)} tags", use_container_width=False):
        for tag in tags:
            mark = "☑" if tag in preferences.calendar.tags else "☐"
            if st.button(f"{mark} {tag}", key=f"tag-{tag}", use_container_width=True):
                _apply(toggle_tag(preferences, tag))


def _render_row(preferences: Preferences, row: Row) -> None:
    # The columns are created INSIDE the keyed container, not outside it. Built
    # outside, they would be siblings of the container rather than descendants,
    # and every CSS rule in styles.py matches the button as a descendant of the
    # key - so the row would render completely unstyled.
    with st.container(key=_row_key(preferences, row)):
        label_col, tag_col, bell_col = st.columns([6, 2, 1], vertical_alignment="center")
        with label_col:
            if st.button(
                row.name,
                key=f"row-{row.kind}-{row.name}",
                use_container_width=True,
                help="Click to show or hide this on the calendar.",
            ):
                _apply(toggle_calendar(preferences, row))
        with tag_col:
            if row.kind == CATEGORY:
                _render_tag_popover(preferences, row.name)
        with bell_col:
            with st.container(key=_bell_key(preferences, row)):
                if st.button(
                    BELL,
                    key=f"bell-{row.kind}-{row.name}",
                    help="Click to turn notifications for this on or off.",
                ):
                    _apply(toggle_notify(preferences, row))


def render_filter_panel(subscription: Optional[Dict]) -> Preferences:
    """Render the panel and return the resolved Preferences.

    Callers use `.calendar` for what to mark; the cron job uses `.notify`.
    """
    _seed(subscription)
    preferences = st.session_state[STATE_KEY]

    mirroring = st.toggle("Notify me about everything I've marked", key=MIRROR_KEY)
    if mirroring != preferences.notify_mirrors_calendar:
        _apply(set_mirror(preferences, mirroring))

    if subscription is None:
        st.caption(
            "Bells only do anything once you're subscribed — set them now and "
            "they'll carry over."
        )

    last_kind = None
    for row in all_rows():
        if row.kind != last_kind:
            st.markdown(f"**{GROUP_LABELS[row.kind]}**")
            last_kind = row.kind
        _render_row(preferences, row)

    return st.session_state[STATE_KEY]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_filters.py -v`
Expected: PASS — 6 tests

- [ ] **Step 6: Run the whole suite**

Run: `pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/app/filters.py tests/apps/filter_panel_app.py tests/test_filters.py
git commit -m "feat: add the two-channel filter panel"
```

---

### Task 6: Panel styling

**Files:**
- Modify: `src/app/styles.py` (append to `PAGE_CSS`, before the closing `</style>`)

**Interfaces:**
- Consumes: the container keys `filter-row-{on}{notifying}-{kind}-{name}` from Task 5
- Produces: no Python API

- [ ] **Step 1: Append the CSS**

The container key encodes both states as two digits, so `10` is marked-but-muted and `00` is off.
Matching is by `[class*=…]` on a descendant, exactly as the calendar's marks already do
(`styles.py:80`, `:92`, `:101`).

Insert before the final `</style>` in `PAGE_CSS`:

```css
/* ---- Filter panel ---------------------------------------------------- */

/* Rows are flat buttons, not chrome: the panel should read as a list. */
[class*="st-key-filter-row-"] button[data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    text-align: left !important;
    justify-content: flex-start !important;
    padding: 2px 4px !important;
    min-height: 0 !important;
}

/* A row that is off: dimmed and struck through, so it reads as "you turned this
   off" rather than "this is unavailable". Both digits zero. */
[class*="st-key-filter-row-00-"] button[data-testid="stBaseButton-secondary"] {
    opacity: .34;
}
[class*="st-key-filter-row-00-"] button[data-testid="stBaseButton-secondary"] p {
    text-decoration: line-through;
    text-decoration-thickness: 1px;
}

/* Marked rows carry their name in full weight. */
[class*="st-key-filter-row-11-"] button[data-testid="stBaseButton-secondary"] p,
[class*="st-key-filter-row-10-"] button[data-testid="stBaseButton-secondary"] p {
    font-weight: 600;
}

/* Bells live in their own keyed container, so they can be targeted without a
   positional selector - the label and the bell sit in different st.columns, and
   each is the only button in its own column. */
[class*="st-key-filter-bell-"] button[data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 2px !important;
    min-height: 0 !important;
}

/* The bell for a row that is off the calendar: dim, but still clickable - it
   turns the row on. */
[class*="st-key-filter-bell-00-"] button[data-testid="stBaseButton-secondary"] {
    opacity: .25;
}

/* Marked but muted: a red slash across the bell, in the same ink as the
   calendar's circle. Drawn rather than swapped to the 🔕 glyph, which renders
   grey on every platform and so reads as "disabled" instead of "deliberately
   muted". The pale outline keeps it legible over the emoji beneath.
   FALLBACK: if the overlay proves fragile across platforms, delete this rule
   and render the label as 🔕 in filters.py instead - it is a local change. */
[class*="st-key-filter-bell-10-"] button[data-testid="stBaseButton-secondary"] {
    position: relative;
}
[class*="st-key-filter-bell-10-"] button[data-testid="stBaseButton-secondary"]::after {
    content: '';
    position: absolute; top: 50%; left: 50%;
    width: 22px; height: 2px; margin: -1px 0 0 -11px;
    background: var(--aa-accent);
    box-shadow: 0 0 0 1px rgba(242, 239, 230, .75);
    border-radius: 2px;
    transform: rotate(-45deg);
    pointer-events: none;
}

/* Tag popover trigger: a quiet hint, not a button. */
[class*="st-key-filter-row-"] [data-testid="stPopover"] button {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    opacity: .5;
    font-size: 11px;
}
```

- [ ] **Step 2: Verify the app still renders**

Run: `streamlit run src/app/ui.py`
Expected: the app loads without a Streamlit error. The panel is unstyled at this point — Task 7
wires it in — so confirm only that nothing broke.

- [ ] **Step 3: Run the suite**

Run: `pytest`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/app/styles.py
git commit -m "style: dim, strike and slash marks for the filter panel"
```

---

### Task 7: Wire the panel into the app

**Files:**
- Modify: `src/app/ui.py:19-42` (imports), `:64-96` (delete the three seeding blocks), `:104-131` (subscribe button), `:140-195` (replace the expander body), `:245-250` (calendar filtering)

**Interfaces:**
- Consumes: `render_filter_panel` (Task 5), `create_subscription`/`update_subscription_filters` (Task 3)
- Produces: no new API

- [ ] **Step 1: Replace the imports**

In `src/app/ui.py`, replace the `core.config` / `core.matching` / `core.sequences` import block with:

```python
from core.age import age_breakdown
from core.db import (
    create_subscription,
    fetch_events,
    get_subscription,
    update_subscription_filters,
)
from core.matching import events_by_age_days, filter_events, full_sentence
from core.sequences import anniversary_matches, anniversary_sentence

from app.filters import SAVED_KEY, STATE_KEY, render_filter_panel
from app.links import further_reading_links, subscription_link
from app.styles import MASTHEAD_HTML, PAGE_CSS
```

- [ ] **Step 2: Delete the three session-seeding blocks**

Delete everything from the comment `# The filter is a session value for anonymous visitors…` down
to and including the `active_sequences = (...)` assignment (`ui.py:64-96`). `render_filter_panel`
owns all of that now, and the `sequence_picker` workaround and the double read of
`active_sequences` disappear with it.

- [ ] **Step 3: Update the subscribe button**

In the "Get notified" expander, replace the `create_subscription(...)` call with:

```python
                new_subscription = create_subscription(
                    birthdate, st.session_state.get(STATE_KEY)
                )
```

`st.session_state.get(STATE_KEY)` rather than a bare index: this button renders above the panel in
script order, so on the very first run the key does not exist yet. `create_subscription` treats
`None` as `default_preferences()`, which is the right value for a visitor who never opened the
panel.

- [ ] **Step 4: Replace the expander body**

Replace the whole `with st.expander("Filter what shows up on the calendar"):` block
(`ui.py:140-195`) with:

```python
with st.expander("What counts as a match"):
    preferences = render_filter_panel(subscription)

    if subscription:
        if preferences != st.session_state[SAVED_KEY]:
            st.caption("You have unsaved changes.")
        if st.button("Save preferences"):
            try:
                update_subscription_filters(subscription["token"], preferences)
            except Exception:
                st.error("Couldn't save your preferences — try again in a moment.")
            else:
                st.session_state[SAVED_KEY] = preferences
                st.success("Saved — your notifications will follow these from now on.")
```

`SAVED_KEY` comes from the import block added in Step 1. The comparison is what drives the
unsaved-changes caption: `Preferences` is a frozen dataclass of tuples ordered by taxonomy, so `!=`
is a meaningful value comparison and not an identity check.

- [ ] **Step 5: Update the calendar's filtering**

In the calendar grid loop, replace the two match lookups with:

```python
            day_matches = filter_events(
                EVENTS_BY_AGE.get(age_days, []),
                preferences.calendar.categories,
                preferences.calendar.tags,
            )
            day_anniversaries = anniversary_matches(age_days, preferences.calendar.sequences)
```

- [ ] **Step 6: Update the caption under the panel**

Replace the caption below the expander with:

```python
st.caption(
    "A red circle marks a day that matches a historical event, a triangle marks a "
    "mathematical anniversary — click either for details. A filled black date marks "
    "today. Use the panel above to choose what counts, and which of it notifies you."
)
```

- [ ] **Step 7: Verify by hand**

Run: `streamlit run src/app/ui.py`

Confirm, in order:
1. The panel lists 8 categories then 8 sequences, every category marked and no sequence marked.
2. Clicking a category row dims and strikes it, and its ⭐ days disappear from the calendar.
3. Clicking a bell on a marked row puts a red slash through it and unticks the mirror toggle.
4. Clicking a dim bell on a sequence marks the row *and* leaves the mirror toggle ticked.
5. `Science & Technology` shows a "4 tags" popover; `War & Conflict` shows none.

- [ ] **Step 8: Run the suite**

Run: `pytest`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/app/ui.py
git commit -m "feat: use the two-channel filter panel in the app"
```

---

### Task 8: Remove the superseded preference readers

**Files:**
- Modify: `src/core/matching.py` (delete `included_tags_for_subscription`, `included_categories_for_subscription`, `events_for_subscription`)
- Modify: `src/core/sequences.py` (delete `included_sequences_for_subscription`)
- Modify: `tests/test_matching.py:180-232`, `tests/test_sequences.py:190-215`

**Interfaces:**
- Consumes: nothing new
- Produces: nothing new — this is deletion only. All four functions were replaced by `core.preferences` in Tasks 1–4; grep confirms no caller remains.

- [ ] **Step 1: Confirm nothing still calls them**

Run:

```bash
grep -rn "events_for_subscription\|included_tags_for_subscription\|included_categories_for_subscription\|included_sequences_for_subscription" src/ scripts/ tests/
```

Expected: matches only in `src/core/matching.py`, `src/core/sequences.py`, and the test blocks
listed above. If anything else appears, stop and fix that caller first.

- [ ] **Step 2: Delete the four functions and their tests**

- `src/core/matching.py`: delete `included_tags_for_subscription`, `included_categories_for_subscription`, `events_for_subscription`. Keep `included_from_excluded` and `excluded_from_included` — `core/preferences.py` imports both. Drop `CATEGORY_NAMES` from the `core.config` import if it is now unused.
- `src/core/sequences.py`: delete `included_sequences_for_subscription`. Drop the now-unused `Optional`/`Dict` imports only if nothing else in the file uses them.
- `tests/test_matching.py`: delete the four `test_events_for_subscription_*` tests and the four `test_included_(tags|categories)_for_subscription_*` tests, and prune the imports.
- `tests/test_sequences.py`: delete the four `test_included_sequences_for_subscription_*` tests and prune the import.

- [ ] **Step 3: Run the suite**

Run: `pytest`
Expected: PASS. The deleted coverage is replaced by `tests/test_preferences.py`, which tests the
same defensive-read behaviour against the new model.

- [ ] **Step 4: Commit**

```bash
git add src/core/matching.py src/core/sequences.py tests/test_matching.py tests/test_sequences.py
git commit -m "refactor: drop the superseded per-module preference readers"
```

---

### Task 9: Documentation

**Files:**
- Modify: `SUPABASE_SETUP.md` (append section 14)
- Modify: `README.md` (the "Tag filtering" bullet)
- Modify: `IDEAS.md` (no new entry; nothing here is deferred)

**Interfaces:**
- Consumes: the column names from Task 3
- Produces: no code

- [ ] **Step 1: Add SUPABASE_SETUP.md section 14**

Append:

````markdown
## 14. Two-channel filter preferences

Run this in the SQL editor **before** deploying the two-channel filter code — the app's "Save
preferences" button writes these columns, and the write fails if they don't exist.

```sql
alter table subscriptions add column if not exists notify_mirrors_calendar    boolean not null default true;
alter table subscriptions add column if not exists notify_excluded_categories text[]  not null default '{}';
alter table subscriptions add column if not exists notify_included_sequences  text[]  not null default '{}';
```

No backfill is needed, and that is the point of the mirror flag. `default true` puts every existing
subscriber on "notifications follow my calendar", which is exactly the behaviour they had before
the split — so nobody who muted a category starts hearing about it again.

The three pre-existing columns (`excluded_categories`, `excluded_tags`, `included_sequences`) keep
their meaning unchanged and now describe the **calendar** channel. The two new list columns describe
the **notification** channel, and are read only while `notify_mirrors_calendar` is false. They are
always intersected with the calendar selection on read (`core.preferences.Preferences.notify`), so a
stale entry for a category the subscriber later hid can never resurrect a notification.

Semantics follow the axis they mirror rather than each other:
`notify_excluded_categories` stores **exclusions**, so a category added to `TAG_CATEGORIES` later
notifies by default; `notify_included_sequences` stores **inclusions**, so a sequence added to
`SEQUENCE_TAXONOMY` later stays silent. There is no `notify_excluded_tags` — fine tags narrow both
channels equally.

**The rename hazard from sections 11 and 13 now applies twice over.** A `TAG_CATEGORIES` key is
persisted verbatim into both `excluded_categories` and `notify_excluded_categories`; a
`SEQUENCE_TAXONOMY` name into both `included_sequences` and `notify_included_sequences`. Renaming
either orphans subscriber state in two columns instead of one, silently. Keep an alias or write a
migration that rewrites **both**.

`core.preferences.preferences_from_subscription` reads every column defensively, so if the daily
cron job runs before this SQL is applied it falls back to the calendar channel rather than failing
the run.
````

- [ ] **Step 2: Update the README bullet**

Replace the "**Tag filtering**" bullet with:

```markdown
- **Two-channel filtering** — every event is tagged (science, military, arts, …) and grouped into
  eight categories; mathematical anniversaries add eight more rows. For each row you choose two
  things: whether it's marked on the calendar, and whether it also pushes a notification. So you can
  mark prime days without being pinged every nine days, or browse everything while only being
  notified about science. Notifications follow your calendar by default; click a bell to break that
  and pick per row. If you're subscribed, the whole panel is saved to your private link.
```

- [ ] **Step 3: Verify the docs match the code**

Run:

```bash
grep -n "notify_mirrors_calendar\|notify_excluded_categories\|notify_included_sequences" SUPABASE_SETUP.md src/core/preferences.py
```

Expected: the three column names appear in both files, spelled identically.

- [ ] **Step 4: Commit**

```bash
git add SUPABASE_SETUP.md README.md
git commit -m "docs: document the two-channel filter schema and behaviour"
```

---

## Deployment note

Task 9's SQL must be applied to Supabase **before** the code from Tasks 3–7 is deployed. Until it
is, `preferences_from_subscription` degrades to the calendar channel (harmless), but the "Save
preferences" button will error, because PostgREST rejects an update naming columns that do not
exist.
