# achievement-age

Streamlit app (`src/app/ui.py`) over a Supabase Postgres DB (`src/core/db.py`); ingest pipelines in `src/ingest/`. Run tests with `./venv/Scripts/python -m pytest -q` locally, `python -m pytest -q` in cloud sessions.

## Supabase

Live data: `SUPABASE_SETUP.md` is the runbook for every schema change and ingest run. Read its section before touching the DB.

- **Check access first**: `python scripts/check_supabase_access.py` reports whether this environment is READ-ONLY (publishable key under row-level security) or PRIVILEGED (secret key). Read-only sessions can read events/persons/tags but not `subscriptions`, and cannot write.
- **Writes go through the ingest scripts** (`python -m ingest.<module>`), which dedupe and are safe to rerun. Before an ad-hoc write, show the user the exact rows and get a yes.
- **Subscriptions are subscribers' private data** (magic-link tokens, ntfy topics). Report counts or aggregates, never the rows themselves.
- **Schema changes are SQL the user runs** in the Supabase SQL editor: add it as a new numbered section of `SUPABASE_SETUP.md`, and give every new table `enable row level security` plus its policies (section 16).
