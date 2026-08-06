"""Supabase access layer.

Used both by the Streamlit app (reads SUPABASE_URL/SUPABASE_KEY from
st.secrets) and by standalone scripts like the ingest migration and the
daily-notification cron job (reads the same two values from the
environment, since those run outside of Streamlit). A local .env file
(see .env at the repo root) is loaded into the environment automatically,
so a single .env covers both local `streamlit run` and standalone scripts
without needing .streamlit/secrets.toml too.
"""

from __future__ import annotations

import os
import secrets
from datetime import date
from typing import Dict, List, Optional

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


def get_config_value(key: str, default: str | None = None) -> str:
    """Look up a config value from st.secrets, then the environment.

    Safe to call whether or not .streamlit/secrets.toml exists at all —
    accessing st.secrets when there's no secrets file raises, not just a
    missing-key lookup, so that's caught here too. If the key is missing
    from both sources: returns `default` if one was given (for optional
    values like APP_BASE_URL), otherwise raises.
    """
    try:
        import streamlit as st

        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass

    value = os.environ.get(key)
    if not value:
        if default is not None:
            return default
        raise RuntimeError(f"{key} is not set (checked st.secrets and the environment).")
    return value


def get_client() -> Client:
    url = get_config_value("SUPABASE_URL")
    key = get_config_value("SUPABASE_KEY")
    return create_client(url, key)


EVENTS_PAGE_SIZE = 1000


def fetch_events() -> List[Dict]:
    """Return every row from the events table, joined with each event's person data.

    Paginates because Supabase/PostgREST caps a single response at ~1000 rows.
    """
    client = get_client()
    events: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("*, persons(wikipedia_url)")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        events.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return events


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
