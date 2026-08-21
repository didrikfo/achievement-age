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

from core.config import CATEGORY_NAMES, SEQUENCE_TAXONOMY, TAG_CATEGORIES, TAG_TAXONOMY
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
        # Both override axes are stored as exclusions, so "everything" is the
        # empty-exclusion state: every name present, nothing muted. The calendar
        # axis is what keeps sequences opt-in, not this one.
        notify_override=NotifyOverride(
            categories=tuple(CATEGORY_NAMES), sequences=tuple(SEQUENCE_TAXONOMY)
        ),
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
        sequences=tuple(
            included_from_excluded(
                subscription.get("notify_excluded_sequences") or [], SEQUENCE_TAXONOMY
            )
        ),
    )

    return Preferences(
        calendar=calendar,
        # None (column absent, or null) means the column default, which is True.
        notify_mirrors_calendar=True if mirrors is None else bool(mirrors),
        notify_override=override,
    )


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


def _selected_for_override(override: NotifyOverride, row: Row) -> Tuple[str, ...]:
    """The override's tuple for this row's axis.

    A sibling of _selected_for rather than one function over both types: an
    override is edited directly (it is what gets stored), whereas a
    ChannelSelection read off `.notify` has already been intersected with the
    calendar. Editing the intersected value would silently drop every muted row
    the moment any other row was clicked.
    """
    return override.categories if row.kind == CATEGORY else override.sequences


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


def toggle_notify(preferences: Preferences, row: Row) -> Preferences:
    """Toggle notifications for one row, from any of its three visual states.

    A dim bell (the row is off the calendar) turns both channels on: the row
    joins the calendar, and it is un-muted in the override so that it notifies
    whether or not the mirror is currently on.

    Muting a live row is the only case that breaks the mirror. No seeding step
    is needed when it does, because the override stores exclusions on both axes
    - an untouched override already means "everything notifies", so breaking the
    mirror changes nothing except the row that was clicked.
    """
    if not is_on_calendar(preferences, row):
        lit = toggle_calendar(preferences, row)
        values = _with_row(_selected_for_override(lit.notify_override, row), row, True)
        return replace(lit, notify_override=_replace_override(lit.notify_override, row, values))

    broken = replace(preferences, notify_mirrors_calendar=False)
    values = _with_row(
        _selected_for_override(broken.notify_override, row),
        row,
        not is_notifying(broken, row),
    )
    return replace(broken, notify_override=_replace_override(broken.notify_override, row, values))


def set_mirror(preferences: Preferences, mirroring: bool) -> Preferences:
    """Turn mirroring on or off. A flag flip in both directions.

    Neither direction touches the override, so re-ticking the toggle and
    unticking it again returns the subscriber to the selection they had rather
    than quietly resetting it.
    """
    return replace(preferences, notify_mirrors_calendar=mirroring)


def toggle_tag(preferences: Preferences, tag: str) -> Preferences:
    """Include or exclude one fine tag. Narrows both channels - tags are not per-channel."""
    tags = set(preferences.calendar.tags)
    tags.discard(tag) if tag in tags else tags.add(tag)
    ordered = tuple(entry for entry in TAG_TAXONOMY if entry in tags)
    return replace(preferences, calendar=replace(preferences.calendar, tags=ordered))
