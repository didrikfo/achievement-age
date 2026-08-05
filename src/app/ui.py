"""Streamlit entry point for the Achievement Age application."""

from __future__ import annotations

import calendar
from datetime import date
from typing import Dict, List

import streamlit as st

from core.age import age_breakdown
from core.config import DATA_DIR
from core.io import load_events_from_json, load_persons_from_json
from core.matching import events_by_age_days
from core.models.event import Event
from core.models.person import Person

# Load your preprocessed events file once at startup
PERSONS: Dict[str, Person] = load_persons_from_json(DATA_DIR / "top_1000_births.json")
EVENTS: List[Event] = load_events_from_json(DATA_DIR / "displayable_events.json", PERSONS)
EVENTS_BY_AGE: Dict[int, List[Event]] = events_by_age_days(EVENTS)

st.title("Achievement Age Calendar")

st.write(
    "Enter your birthday, then browse the calendar for days when you were "
    "(or will be) the same age as someone famous at a notable moment."
)

# Input
birthdate = st.date_input("Your birthday", value=date(2000, 1, 1), min_value=date(1900, 1, 1))

# Show age
years, months, days = age_breakdown(birthdate, date.today())
st.markdown(f"You are **{years} years, {months} months, and {days} days** old today.")

st.caption("⭐ marks a day that matches a historical event - click it for details. 🔵 marks today.")


@st.dialog("Matching event")
def show_event_dialog(day_date: date, matches: List[Event]) -> None:
    match_years, match_months, match_days = age_breakdown(birthdate, day_date)
    st.markdown(
        f"On **{day_date.strftime('%B %d, %Y')}** you are/were "
        f"**{match_years} years, {match_months} months, {match_days} days** old:"
    )
    for event in matches:
        st.markdown(f"- {event.display_text}")


today = date.today()
if "view_year" not in st.session_state:
    st.session_state.view_year = today.year
if "view_month" not in st.session_state:
    st.session_state.view_month = today.month

nav_prev, nav_month, nav_year, nav_next = st.columns([1, 2, 2, 1])

with nav_prev:
    if st.button("◀", use_container_width=True):
        st.session_state.view_month -= 1
        if st.session_state.view_month < 1:
            st.session_state.view_month = 12
            st.session_state.view_year -= 1

with nav_next:
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

weekday_cols = st.columns(7)
for col, weekday_name in zip(weekday_cols, calendar.day_abbr):
    col.markdown(f"**{weekday_name}**")

for week in calendar.monthcalendar(view_year, view_month):
    week_cols = st.columns(7)
    for col, day in zip(week_cols, week):
        if day == 0:
            col.write("")
            continue

        day_date = date(view_year, view_month, day)
        age_days = (day_date - birthdate).days
        day_matches = EVENTS_BY_AGE.get(age_days, [])
        is_today = day_date == today

        if day_matches:
            label = f"{day} ⭐" + (" \U0001f535" if is_today else "")
            if col.button(
                label,
                key=f"day_{view_year}_{view_month}_{day}",
                type="primary",
                use_container_width=True,
            ):
                show_event_dialog(day_date, day_matches)
        elif is_today:
            col.markdown(
                "<div style='text-align:center; border-radius:50%; "
                "background-color:#1c83e1; color:white; padding:4px 0;'>"
                f"{day}</div>",
                unsafe_allow_html=True,
            )
        else:
            col.markdown(f"<div style='text-align:center;'>{day}</div>", unsafe_allow_html=True)
