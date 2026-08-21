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

#: Set by _apply() alongside STATE_KEY, cleared at the top of the next run.
#: Not a widget key, so writing it is never restricted the way MIRROR_KEY is -
#: see _apply()'s docstring for why that restriction exists at all.
_MIRROR_SYNC_KEY = "_notify_mirror_needs_sync"

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
    """Store a new Preferences, flag the mirror toggle for a resync, and rerun.

    MIRROR_KEY itself is not written here. It backs the toggle widget
    instantiated near the top of render_filter_panel, and Streamlit forbids
    writing to a widget-backed session_state key once that widget has been
    instantiated during the current run - every row and bell live below the
    toggle in the script, so writing MIRROR_KEY from this function would
    always raise.

    _MIRROR_SYNC_KEY carries the intent across the rerun instead. It is
    plain, not widget-backed, so setting it here is always legal; the top of
    render_filter_panel clears it and force-writes MIRROR_KEY from the new
    preferences before the toggle is instantiated for that run, which is the
    one point where that write is legal. A plain equality check between
    MIRROR_KEY and the model can't stand in for this flag: Streamlit resolves
    a genuine toggle click into session_state before the script body even
    starts, so by the time this function's caller runs, a fresh click and a
    stale value left over from a bell click are indistinguishable by value
    alone - only the call path (through here, or not) tells them apart.
    """
    st.session_state[STATE_KEY] = preferences
    st.session_state[_MIRROR_SYNC_KEY] = True
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

    # Force the toggle's stored value to match the model, but only when
    # _apply() flagged this run for it - i.e. only when the previous run's
    # mutation came from a row or bell, not from the toggle itself. This is
    # the only point in the run where writing MIRROR_KEY is legal (before the
    # toggle below has been instantiated), and skipping it would leave the
    # toggle drawn in its old position after a bell click breaks the mirror,
    # since Streamlit prefers a keyed widget's stored state over `value=`.
    if st.session_state.pop(_MIRROR_SYNC_KEY, False):
        st.session_state[MIRROR_KEY] = preferences.notify_mirrors_calendar

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
