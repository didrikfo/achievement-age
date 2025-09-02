import streamlit as st
from datetime import date, datetime
import os
from utils import load_events_from_json, load_persons_from_json
from models.event import Event
from models.person import Person
from display_event import search_for_event_matching_age
from config import DATA_DIR

# Load your preprocessed events file
PERSONS = load_persons_from_json(os.path.join(DATA_DIR,"top_1000_births.json"))
EVENTS = load_events_from_json(os.path.join(DATA_DIR,"displayable_events.json"), PERSONS)

st.title("Achievement Age Calendar")

st.write("Enter your birthday to find an event that matches your current age.")

# Input
birthdate = st.date_input("Your birthday", value=date(2000, 1, 1), min_value='1900-01-01')

# Calculate user's current age in days
user_age_days = (date.today() - birthdate).days

# Find matching events
matches = search_for_event_matching_age(EVENTS, user_age_days)

# Show age
years = user_age_days // 365
days = user_age_days % 365
st.markdown(f"You are **{years} years and {days} days** old today.")

# Show event
if matches:
    st.success()
    for match in matches:
        st.markdown(f"{match.display_text}")
else:
    st.warning("No historical events found for your exact age.")
