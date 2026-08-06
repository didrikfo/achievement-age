"""Supabase access layer.

Used both by the Streamlit app (reads SUPABASE_URL/SUPABASE_KEY from
st.secrets) and by standalone scripts like the ingest migration and the
daily-notification cron job (reads the same two values from the
environment, since those run outside of Streamlit).
"""

from __future__ import annotations

import os
import secrets
from datetime import date
from typing import Dict, List, Optional

from supabase import Client, create_client


def _get_config_value(key: str) -> str:
    try:
        import streamlit as st

        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass

    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"{key} is not set (checked st.secrets and the environment).")
    return value


def get_client() -> Client:
    url = _get_config_value("SUPABASE_URL")
    key = _get_config_value("SUPABASE_KEY")
    return create_client(url, key)


def fetch_events() -> List[Dict]:
    """Return every row from the events table."""
    client = get_client()
    response = client.table("events").select("*").execute()
    return response.data


def fetch_tags() -> List[Dict]:
    """Return every row from the tags table."""
    client = get_client()
    response = client.table("tags").select("*").execute()
    return response.data


def create_subscription(birthday: date) -> Dict:
    """Create a new subscription (magic-link token + ntfy topic) for a birthday."""
    client = get_client()
    row = {
        "token": secrets.token_urlsafe(12),
        "ntfy_topic": f"achage-{secrets.token_urlsafe(9)}",
        "birthday": birthday.isoformat(),
    }
    response = client.table("subscriptions").insert(row).execute()
    return response.data[0]


def get_subscription(token: str) -> Optional[Dict]:
    """Look up a subscription by its magic-link token, or None if it doesn't exist."""
    client = get_client()
    response = client.table("subscriptions").select("*").eq("token", token).execute()
    return response.data[0] if response.data else None


def fetch_all_subscriptions() -> List[Dict]:
    """Return every subscription row. Used only by the notification cron job."""
    client = get_client()
    response = client.table("subscriptions").select("*").execute()
    return response.data
