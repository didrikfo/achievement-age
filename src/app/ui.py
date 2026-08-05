"""Streamlit entry point for the Achievement Age application."""

from __future__ import annotations

from datetime import date
from typing import Dict, List

import streamlit as st

from core.age import age_breakdown
from core.config import DATA_DIR
from core.io import load_events_from_json, load_persons_from_json
from core.models.event import Event
from core.models.person import Person

from app.display_event import search_for_event_matching_age

# Load your preprocessed events file once at startup
PERSONS: Dict[str, Person] = load_persons_from_json(DATA_DIR / "top_1000_births.json")
EVENTS: List[Event] = load_events_from_json(DATA_DIR / "displayable_events.json", PERSONS)

st.title("Achievement Age Calendar")

st.write("Enter your birthday to find an event that matches your current age.")

# Input
birthdate = st.date_input("Your birthday", value=date(2000, 1, 1), min_value=date(1900, 1, 1))

# Calculate user's current age in days
user_age_days = (date.today() - birthdate).days

# Find matching events
matches = search_for_event_matching_age(EVENTS, user_age_days)

# Show age
years, months, days = age_breakdown(birthdate, date.today())
st.markdown(f"You are **{years} years, {months} months, and {days} days** old today.")

# Show event
if matches:
    st.success("Found matching events.")
    for match in matches:
        st.markdown(f"{match.display_text}")
else:
    st.warning("No historical events found for your exact age.")
