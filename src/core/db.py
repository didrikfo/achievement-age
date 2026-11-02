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

import httpx
from dotenv import load_dotenv
from postgrest import ReturnMethod
from supabase import Client, ClientOptions, create_client

from core.preferences import Preferences, default_preferences, preferences_to_columns

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


#: SUPABASE_SECRET_KEY value meaning "a network proxy attaches the real key",
#: as in the Claude cloud write environment (SUPABASE_SETUP.md section 17).
SECRET_KEY_FROM_PROXY = "injected-by-proxy"


def _strip_auth_headers(request: httpx.Request) -> None:
    """Remove the headers supabase-py always sends, leaving the proxy's apikey alone.

    Supabase rejects a secret apikey sent beside an Authorization header holding
    any other key, and the proxy can set only the one header.
    """
    request.headers.pop("apikey", None)
    request.headers.pop("Authorization", None)


def get_client() -> Client:
    """A Supabase client, privileged only where a secret key has been configured.

    Row-level security (SUPABASE_SETUP.md section 16) lets the publishable key
    in SUPABASE_KEY read the public tables and create a subscription, nothing
    more. Writes to events/persons/tags and reading every subscription need the
    secret key, which bypasses RLS. Only privileged contexts set
    SUPABASE_SECRET_KEY: the cron job, a local .env used for ingest, and the
    Claude cloud write environment. The deployed Streamlit app never does, so
    it always runs as the publishable key.
    """
    url = get_config_value("SUPABASE_URL")
    secret_key = get_config_value("SUPABASE_SECRET_KEY", default="")
    if secret_key == SECRET_KEY_FROM_PROXY:
        http_client = httpx.Client(timeout=120, event_hooks={"request": [_strip_auth_headers]})
        return create_client(url, secret_key, options=ClientOptions(httpx_client=http_client))
    return create_client(url, secret_key or get_config_value("SUPABASE_KEY"))


EVENTS_PAGE_SIZE = 1000


def _flatten_tags(event: Dict) -> Dict:
    """Collapse the nested event_tags(tags(name)) join into a flat list of names.

    PostgREST returns [{"tags": {"name": "science"}}, ...]; callers just want
    ["science", ...]. Always produces a list - never None - so no downstream
    code needs a None check.
    """
    tag_rows = event.pop("event_tags", None) or []
    event["tags"] = [row["tags"]["name"] for row in tag_rows if row.get("tags")]
    return event


def fetch_events() -> List[Dict]:
    """Return every row from the events table, joined with person data and tag names.

    Paginates because Supabase/PostgREST caps a single response at ~1000 rows.
    """
    client = get_client()
    events: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("*, persons(wikipedia_url), event_tags(tags(name))")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        events.extend(_flatten_tags(event) for event in page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return events


def fetch_tags() -> List[Dict]:
    """Return every row from the tags table."""
    client = get_client()
    response = client.table("tags").select("*").execute()
    return response.data


def create_subscription(birthday: date, preferences: Optional[Preferences] = None) -> Dict:
    """Create a new subscription (magic-link token + ntfy topic) for a birthday.

    The visitor's current filter panel state is carried straight into the new
    row, so filter-then-subscribe is one action - bells included, which is why
    an anonymous visitor can set up "notify me about science only" in one visit.
    """
    client = get_client()
    row = {
        "token": secrets.token_urlsafe(12),
        "ntfy_topic": f"achage-{secrets.token_urlsafe(9)}",
        "birthday": birthday.isoformat(),
        **preferences_to_columns(preferences or default_preferences()),
    }
    # Minimal return: the publishable key may insert a subscription but not read
    # one back, so asking PostgREST for the inserted row would fail under RLS.
    client.table("subscriptions").insert(row, returning=ReturnMethod.minimal).execute()
    return row


def get_subscription(token: str) -> Optional[Dict]:
    """Look up a subscription by its magic-link token, or None if it doesn't exist.

    Goes through the get_subscription SQL function rather than a table select:
    RLS gives the publishable key no read access to subscriptions at all, so
    nobody can list every token. The function returns only the row whose token
    the caller already knows.
    """
    client = get_client()
    response = client.rpc("get_subscription", {"p_token": token}).execute()
    return response.data[0] if response.data else None


def update_subscription_filters(token: str, preferences: Preferences) -> None:
    """Overwrite a subscription's stored calendar and notification preferences in one write.

    Goes through the update_subscription_preferences SQL function, for the same
    reason as get_subscription. That function names each preference column
    explicitly, so a column added to preferences_to_columns must be added to it
    too (SUPABASE_SETUP.md section 16), or the new column is silently never saved.
    """
    client = get_client()
    client.rpc(
        "update_subscription_preferences",
        {"p_token": token, "p_preferences": preferences_to_columns(preferences)},
    ).execute()


def fetch_all_subscriptions() -> List[Dict]:
    """Return every subscription row. Used only by the notification cron job."""
    client = get_client()
    response = client.table("subscriptions").select("*").execute()
    return response.data
