"""Report which Supabase access this environment has, without printing any key.

Run it first in a new Claude Code cloud session, or after changing keys:

    python scripts/check_supabase_access.py

Reads events (anyone with the publishable key can) and subscriptions (only the
secret key can, because of row-level security), so the two lines together say
whether this environment is read-only or privileged. Writes nothing.
"""

from __future__ import annotations

import sys

from core.db import get_client, get_config_value


def key_kind() -> str:
    secret = get_config_value("SUPABASE_SECRET_KEY", default="")
    if secret:
        return "secret key configured (SUPABASE_SECRET_KEY)"
    return "publishable key only (SUPABASE_KEY)"


def main() -> int:
    print(f"Supabase project: {get_config_value('SUPABASE_URL')}")
    print(f"Key: {key_kind()}")
    client = get_client()

    try:
        events = client.table("events").select("id", count="exact").limit(1).execute()
    except Exception as error:  # noqa: BLE001 - report, don't crash, so the next line still prints
        print(f"events: NOT readable ({error})")
        return 1
    print(f"events: readable, {events.count} rows")

    try:
        subscriptions = client.table("subscriptions").select("token", count="exact").limit(1).execute()
    except Exception as error:  # noqa: BLE001
        print(f"subscriptions: blocked ({error})")
        subscriptions = None
    if subscriptions is not None and subscriptions.count:
        if key_kind().startswith("secret"):
            print(f"subscriptions: readable, {subscriptions.count} rows -> PRIVILEGED (secret key bypasses RLS)")
        else:
            print(
                f"subscriptions: readable, {subscriptions.count} rows with the publishable key -> "
                "RLS IS NOT ENABLED (run SUPABASE_SETUP.md section 16)"
            )
    else:
        print("subscriptions: no rows visible -> READ-ONLY (publishable key under RLS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
