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
from core.db import create_subscription, fetch_events, get_config_value, get_subscription
from core.matching import events_by_age_days, full_sentence

from app.styles import MASTHEAD_HTML, PAGE_CSS

APP_BASE_URL = get_config_value("APP_BASE_URL", default="")


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

if subscription:
    birthdate = date.fromisoformat(subscription["birthday"])
    st.caption("Welcome back — this link remembers your birthday.")
else:
    birthdate = st.date_input("Your birthday", value=date(2000, 1, 1), min_value=date(1900, 1, 1))

    with st.expander("Get notified when your age matches an event"):
        st.write(
            "Get a push notification (via the free [ntfy](https://ntfy.sh) app) on days "
            "when your age matches a historical event, without having to check the calendar yourself."
        )
        if st.button("Get notified"):
            new_subscription = create_subscription(birthdate)
            link = f"{APP_BASE_URL}?u={new_subscription['token']}"
            st.success("Subscription created! Save this link and subscribe to your notification topic:")
            st.code(link, language=None)
            st.markdown(
                f"1. **Bookmark or add this page to your home screen** — it remembers your birthday.\n"
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

st.caption("A red circle marks a day that matches a historical event — click it for details. A filled black date marks today.")


@st.dialog("Matching event")
def show_event_dialog(day_date: date, matches: List[Dict]) -> None:
    match_years, match_months, match_days = age_breakdown(birthdate, day_date)
    st.markdown(
        f"On **{day_date.strftime('%B %d, %Y')}** you are/were "
        f"**{match_years} years, {match_months} months, {match_days} days** old:"
    )
    for event in matches:
        st.markdown(f"- {full_sentence(event)}")
        description = event.get("detailed_description") or event.get("text")
        if description:
            st.caption(description)
        person = event.get("persons") or {}
        wikipedia_url = person.get("wikipedia_url")
        if wikipedia_url:
            st.markdown(f"[Read more on Wikipedia]({wikipedia_url})")


today = date.today()
if "view_year" not in st.session_state:
    st.session_state.view_year = today.year
if "view_month" not in st.session_state:
    st.session_state.view_month = today.month

nav_prev, nav_month, nav_year, nav_next = st.columns([1, 2, 2, 1])

with nav_prev:
    with st.container(key="nav-prev"):
        if st.button("◀", use_container_width=True):
            st.session_state.view_month -= 1
            if st.session_state.view_month < 1:
                st.session_state.view_month = 12
                st.session_state.view_year -= 1

with nav_next:
    with st.container(key="nav-next"):
        if st.button("▶", use_container_width=True):
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

st.subheader(f"{calendar.month_name[view_month]} {view_year}")

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
            day_matches = EVENTS_BY_AGE.get(age_days, [])
            is_today = day_date == today

            if day_matches and is_today:
                with col.container(key=f"today-match-{view_year}-{view_month}-{day}"):
                    if st.button(
                        str(day),
                        key=f"day_{view_year}_{view_month}_{day}",
                        type="primary",
                        use_container_width=True,
                    ):
                        show_event_dialog(day_date, day_matches)
            elif day_matches:
                if col.button(
                    str(day),
                    key=f"day_{view_year}_{view_month}_{day}",
                    type="primary",
                    use_container_width=True,
                ):
                    show_event_dialog(day_date, day_matches)
            elif is_today:
                col.markdown(f"<div class='aa-cal-cell aa-today'>{day}</div>", unsafe_allow_html=True)
            else:
                col.markdown(f"<div class='aa-cal-cell'>{day}</div>", unsafe_allow_html=True)
