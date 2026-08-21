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
