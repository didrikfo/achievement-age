"""Streamlit entry point for the Achievement Age application."""

from __future__ import annotations

import calendar
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

# Streamlit Community Cloud only runs `pip install -r requirements.txt`, not
# `pip install -e .` - so unlike local dev, src/ never lands on sys.path on
# its own. Add it here so `core`/`ingest` imports work regardless of how the
# app was installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from core.age import age_breakdown
from core.config import CATEGORY_NAMES, DEFAULT_SEQUENCES, SEQUENCE_TAXONOMY, TAG_TAXONOMY
from core.db import (
    create_subscription,
    fetch_events,
    get_subscription,
    update_subscription_filters,
)
from core.matching import (
    events_by_age_days,
    excluded_from_included,
    filter_events,
    full_sentence,
    included_categories_for_subscription,
    included_tags_for_subscription,
)
from core.sequences import (
    anniversary_matches,
    anniversary_sentence,
    included_sequences_for_subscription,
)

from app.links import further_reading_links, subscription_link
from app.styles import MASTHEAD_HTML, PAGE_CSS


@st.cache_data(ttl=3600)
def load_events() -> List[Dict]:
    return fetch_events()


EVENTS_BY_AGE: Dict[int, List[Dict]] = events_by_age_days(load_events())

st.markdown(PAGE_CSS, unsafe_allow_html=True)
st.markdown(MASTHEAD_HTML, unsafe_allow_html=True)

st.write(
    "Enter your birthday, then browse the calendar for days when you were "
    "(or will be) the same age as someone famous at a notable moment."
)

# Resolve identity: a magic-link token in the URL means a returning subscriber.
token = st.query_params.get("u")
subscription = get_subscription(token) if token else None

# The filter is a session value for anonymous visitors and a saved preference
# for subscribers. Seed it once per session so a rerun doesn't clobber an
# in-progress selection.
if "included_tags" not in st.session_state:
    if subscription:
        st.session_state.included_tags = included_tags_for_subscription(subscription)
    else:
        st.session_state.included_tags = list(TAG_TAXONOMY)

if "included_categories" not in st.session_state:
    if subscription:
        st.session_state.included_categories = included_categories_for_subscription(subscription)
    else:
        st.session_state.included_categories = list(CATEGORY_NAMES)

# Mathematical anniversaries are opt-in, so the checkbox starts off for everyone
# who hasn't already chosen sequences - including existing subscribers, whose
# stored column is empty. The multiselect behind it is pre-loaded with the
# recommended four regardless, so enabling the feature is one click rather than
# five.
if "included_sequences" not in st.session_state:
    stored_sequences = included_sequences_for_subscription(subscription) if subscription else []
    st.session_state.included_sequences = stored_sequences or list(DEFAULT_SEQUENCES)
    st.session_state.anniversaries_on = bool(stored_sequences)

# Read here for the "Get notified" button below, which runs earlier in the
# script than the expander that renders the sequence widgets. Correct for that
# use: clicking the button is its own rerun, so widget state is current at the
# top of it. The expander RE-READS this into the same name after the widgets
# have run, because the multiselect assigns mid-script - see Task 4 Step 4.
active_sequences = (
    st.session_state.included_sequences if st.session_state.anniversaries_on else []
)

if subscription:
    birthdate = date.fromisoformat(subscription["birthday"])
    st.caption("Welcome back — this link remembers your birthday.")
else:
    birthdate = st.date_input("Your birthday", value=date(2000, 1, 1), min_value=date(1900, 1, 1))

    with st.expander("Get notified when your age matches an event"):
        st.write(
            "Get a push notification (via the free [ntfy](https://ntfy.sh) app) on days "
            "when your age matches a historical event, without having to check the calendar yourself. "
            "If you've turned on mathematical anniversaries above, those are pushed too."
        )
        if st.button("Get notified"):
            try:
                new_subscription = create_subscription(
                    birthdate,
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                    active_sequences,
                )
            except Exception:
                st.error("Couldn't save your preferences — try again in a moment.")
            else:
                link = subscription_link(new_subscription["token"])
                st.success("Subscription created! Save this link, then subscribe to your notification topic:")
                st.code(link, language=None)
                st.markdown(
                    f"1. **Save this link.** Bookmark it or add it to your home screen — it "
                    f"remembers your birthday and your filters, and it's the only way back to "
                    f"them, so don't lose it.\n"
                    f"2. Install the [ntfy app](https://ntfy.sh) and subscribe to the topic "
                    f"`{new_subscription['ntfy_topic']}`.\n"
                    f"3. You'll get a notification whenever your age matches an event."
                )

# Show age
years, months, days = age_breakdown(birthdate, date.today())
st.markdown(
    f"<p class='aa-age'>You are <b>{years} years, {months} months, and {days} days</b> old today.</p>",
    unsafe_allow_html=True,
)

with st.expander("Filter what shows up on the calendar"):
    st.multiselect("Show events about:", options=CATEGORY_NAMES, key="included_categories")
    st.caption(
        "Every event belongs to exactly one of these. Events that haven't been "
        "tagged yet always show up, whatever you pick."
    )
    # A popover, not a nested expander - Streamlit rejects expander-in-expander.
    with st.popover("Advanced: filter by detailed tag"):
        st.multiselect("Show events tagged:", options=TAG_TAXONOMY, key="included_tags")
        st.caption(
            "These narrow things down within the categories you kept above. "
            "Unchecking a tag can never bring back an event from a category you hid."
        )

    st.checkbox("Also mark mathematical anniversaries", key="anniversaries_on")
    st.caption(
        "Days when your age in days is itself an interesting number. "
        "Marked with a triangle instead of a circle."
    )
    if st.session_state.anniversaries_on:
        # A stable widget key, not an unkeyed multiselect fed from session
        # state via `default`: Streamlit hashes `default` into an unkeyed
        # widget's element id, so feeding last run's selection back in as
        # `default` changes the id on the very next run and silently drops
        # whatever the user just picked (confirmed against Streamlit's own
        # element-id hashing and reproduced with AppTest - every second edit
        # was reverted). The key still gets garbage-collected whenever the
        # checkbox above is unticked, which is why included_sequences below
        # stays the durable value the rest of the script reads, reseeded from
        # this widget's key rather than replaced by it.
        if "sequence_picker" not in st.session_state:
            st.session_state.sequence_picker = list(st.session_state.included_sequences)
        st.multiselect(
            "Track these sequences:", options=SEQUENCE_TAXONOMY, key="sequence_picker"
        )
        st.session_state.included_sequences = list(st.session_state.sequence_picker)
        st.caption(
            "Primes and squares are off to begin with because they'd land far more "
            "often — a prime day comes round roughly once every nine days."
        )

    # Refresh the value now that the widgets above have run. The copy made at
    # the top of the script is already stale by this point: the multiselect
    # assigns included_sequences here, mid-script, so everything BELOW the
    # expander - the save button and the calendar - must re-read it or it will
    # render one interaction behind.
    active_sequences = (
        st.session_state.included_sequences if st.session_state.anniversaries_on else []
    )

    if subscription:
        if st.button("Update preferences"):
            try:
                update_subscription_filters(
                    subscription["token"],
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                    active_sequences,
                )
            except Exception:
                st.error("Couldn't save your preferences — try again in a moment.")
            else:
                st.success("Saved — your notifications will follow these filters from now on.")

st.caption("A red circle marks a day that matches a historical event, a triangle marks a mathematical anniversary — click either for details. A filled black date marks today. Use the filter above to narrow what counts.")


@st.dialog("This day")
def show_day_dialog(day_date: date, events: List[Dict], anniversaries: List[Dict]) -> None:
    match_years, match_months, match_days = age_breakdown(birthdate, day_date)
    st.markdown(
        f"On **{day_date.strftime('%B %d, %Y')}** you are/were "
        f"**{match_years} years, {match_months} months, {match_days} days** old:"
    )
    # A scrolling box only past a few entries - day 1 alone carries seven
    # anniversaries, but a one-match dialog shouldn't sit in a mostly-empty box.
    body = st.container(height=340) if len(events) + len(anniversaries) > 3 else st.container()
    with body:
        # Kept in separate sections, never interleaved: a sentence about Ada
        # Lovelace and a sentence about the number 2,048 have nothing to do with
        # each other beyond landing on the same date.
        if events:
            st.markdown("**Historical matches**")
            for event in events:
                event_date = date(int(event["year"]), int(event["month"]), int(event["day"]))
                st.markdown(f"- {full_sentence(event)} *({event_date.strftime('%B %d, %Y')})*")
                description = event.get("detailed_description") or event.get("text")
                if description:
                    st.caption(description)
                links = further_reading_links(event)
                if links:
                    joined = " · ".join(f"[{label}]({url})" for label, url in links)
                    st.caption(f"Further reading on Wikipedia: {joined}")
        if anniversaries:
            st.markdown("**Mathematical anniversaries**")
            for anniversary in anniversaries:
                st.markdown(f"- {anniversary_sentence(anniversary)}")


today = date.today()
if "view_year" not in st.session_state:
    st.session_state.view_year = today.year
if "view_month" not in st.session_state:
    st.session_state.view_month = today.month

nav_prev, nav_month, nav_year, nav_next = st.columns([1, 2, 2, 1])

with nav_prev:
    with st.container(key="nav-prev"):
        if st.button("‹", use_container_width=True):
            st.session_state.view_month -= 1
            if st.session_state.view_month < 1:
                st.session_state.view_month = 12
                st.session_state.view_year -= 1

with nav_next:
    with st.container(key="nav-next"):
        if st.button("›", use_container_width=True):
            st.session_state.view_month += 1
            if st.session_state.view_month > 12:
                st.session_state.view_month = 1
                st.session_state.view_year += 1

with nav_month:
    st.session_state.view_month = st.selectbox(
        "Month",
        options=list(range(1, 13)),
        index=st.session_state.view_month - 1,
        format_func=lambda m: calendar.month_name[m],
        label_visibility="collapsed",
    )

with nav_year:
    year_min = min(1900, st.session_state.view_year - 5)
    year_max = max(today.year + 50, st.session_state.view_year + 5)
    year_options = list(range(year_min, year_max + 1))
    st.session_state.view_year = st.selectbox(
        "Year",
        options=year_options,
        index=year_options.index(st.session_state.view_year),
        label_visibility="collapsed",
    )

view_year = st.session_state.view_year
view_month = st.session_state.view_month

st.markdown(
    f"<p class='aa-cal-heading'>{calendar.month_name[view_month].upper()} {view_year}</p>",
    unsafe_allow_html=True,
)

with st.container(key="calendar-grid"):
    weekday_cols = st.columns(7)
    for col, weekday_name in zip(weekday_cols, calendar.day_abbr):
        col.markdown(f"<div class='aa-cal-dow'>{weekday_name}</div>", unsafe_allow_html=True)

    for week in calendar.monthcalendar(view_year, view_month):
        week_cols = st.columns(7)
        for col, day in zip(week_cols, week):
            if day == 0:
                col.markdown("<div class='aa-cal-cell aa-blank'></div>", unsafe_allow_html=True)
                continue

            day_date = date(view_year, view_month, day)
            age_days = (day_date - birthdate).days
            day_matches = filter_events(
                EVENTS_BY_AGE.get(age_days, []),
                st.session_state.included_categories,
                st.session_state.included_tags,
            )
            day_anniversaries = anniversary_matches(age_days, active_sequences)
            is_today = day_date == today

            if not day_matches and not day_anniversaries:
                cell_class = "aa-cal-cell aa-today" if is_today else "aa-cal-cell"
                col.markdown(f"<div class='{cell_class}'>{day}</div>", unsafe_allow_html=True)
                continue

            # Three independent marks (circle, triangle, today's black fill) but
            # a container carries exactly one key, so they nest one per flag
            # rather than encoding all eight combinations in a single key. The
            # CSS matches each class as a descendant, so the depth is irrelevant.
            suffix = f"{view_year}-{view_month}-{day}"
            with col.container(key=f"mark-event-{int(bool(day_matches))}-{suffix}"):
                with st.container(key=f"mark-anniv-{int(bool(day_anniversaries))}-{suffix}"):
                    with st.container(key=f"mark-today-{int(is_today)}-{suffix}"):
                        if st.button(
                            str(day),
                            key=f"day_{suffix}",
                            type="primary",
                            use_container_width=True,
                        ):
                            show_day_dialog(day_date, day_matches, day_anniversaries)
