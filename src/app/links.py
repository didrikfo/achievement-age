"""URL helpers for the Streamlit app.

The pure URL manipulation lives apart from the Streamlit lookups so it can be
tested without a Streamlit runtime.
"""

from __future__ import annotations

from typing import Dict, List, Tuple
from urllib.parse import urlsplit, urlunsplit

import streamlit as st

from core.db import get_config_value


def base_url_from(url: str) -> str:
    """Strip query string, fragment and trailing slash from a page URL."""
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")


def app_base_url() -> str:
    """The app's own base URL: the live browser context first, the secret as fallback.

    st.context.url reports the address the visitor actually loaded, so this is
    correct in local development, on Streamlit Community Cloud, and on any
    future domain without a secret to keep in sync. APP_BASE_URL remains the
    fallback for any context where st.context is unavailable, and stays the only
    source for scripts/send_daily_notifications.py, which has no browser at all.
    """
    try:
        context_url = st.context.url
    except Exception:
        context_url = None
    return base_url_from(context_url or "") or base_url_from(
        get_config_value("APP_BASE_URL", default="")
    )


def subscription_link(token: str) -> str:
    """The full bookmarkable URL that logs a subscriber back in."""
    return f"{app_base_url()}?u={token}"


def further_reading_links(event: Dict) -> List[Tuple[str, str]]:
    """(label, url) pairs for an event's further-reading line.

    A list rather than a single value because event-level Wikipedia links are
    planned next: when they arrive this grows one entry and the display does not
    change shape. Labels name their target so a reader knows what they'd open.
    """
    person = event.get("persons") or {}
    url = person.get("wikipedia_url")
    return [(event["name"], url)] if url else []
