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
st.text(f"calendar_tags={','.join(preferences.calendar.tags)}")
st.text(f"mirroring={preferences.notify_mirrors_calendar}")
