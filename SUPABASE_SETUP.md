# Supabase + ntfy setup (one-time)

## 1. Create the Supabase project

1. Create a free project at [supabase.com](https://supabase.com).
2. In the project's SQL editor, run:

```sql
create table events (
    id bigint generated always as identity primary key,
    name text not null,
    text text not null,
    display_text text not null,
    year int not null,
    month int not null,
    day int not null,
    age_days int not null,
    event_type text not null default 'achievement',
    source text,
    created_at timestamptz not null default now()
);
create index events_age_days_idx on events (age_days);

create table tags (
    id bigint generated always as identity primary key,
    name text not null unique,
    color text not null
);

create table event_tags (
    event_id bigint not null references events (id) on delete cascade,
    tag_id bigint not null references tags (id) on delete cascade,
    primary key (event_id, tag_id)
);

create table subscriptions (
    token text primary key,
    ntfy_topic text not null,
    birthday date not null,
    created_at timestamptz not null default now()
);
```

3. In Project Settings -> API, note the **Project URL** and the **anon public key** (or a service-role key if you'd rather bypass row-level security entirely for this low-stakes hobby project — anon key is fine as long as you don't add RLS policies that block reads/writes).

## 2. Run the one-off data migration

Locally, with `SUPABASE_URL`/`SUPABASE_KEY` set as environment variables (or in a `.env` you source):

```bash
pip install -e .
pip install -r requirements.txt
python -m ingest.migrate_to_supabase
```

Confirm in the Supabase table editor that `events` now has 1232 rows.

## 3. Persons and event detail fields

Run this in the SQL editor to add the `persons` table and the new optional event fields:

```sql
create table persons (
    id bigint generated always as identity primary key,
    name text not null unique,
    wikipedia_url text,
    created_at timestamptz not null default now()
);

alter table events add column person_id bigint references persons (id);
alter table events add column detailed_description text;
alter table events rename column display_text to event_phrase;
```

Then run the one-off backfill (same environment/credentials as the original migration):

```bash
python -m ingest.backfill_persons_and_phrases
```

This creates one `persons` row per distinct event name, links every event to its person, and
splits each `event_phrase` value down to just the suffix after "The same age that {name} was
when " (the prefix is now built at display time). Any row whose text didn't match that exact
prefix is printed at the end instead of being silently mangled — check the output for stragglers
and fix them by hand in the Supabase table editor if any show up.

`wikipedia_url` (on `persons`) and `detailed_description` (on `events`) are left empty — fill
them in later, e.g. via the Supabase table editor, as you get to it.

## 4. Configure secrets

**Streamlit Community Cloud** (App settings -> Secrets), as TOML:

```toml
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "..."
APP_BASE_URL = "https://your-app-name.streamlit.app"
```

(`APP_BASE_URL` can only be filled in *after* the first deploy, once you know the app's URL — redeploy/update the secret once you have it.)

**GitHub repo secrets** (Settings -> Secrets and variables -> Actions), same three: `SUPABASE_URL`, `SUPABASE_KEY`, `APP_BASE_URL`.

For local development, create `.streamlit/secrets.toml` (already gitignored by Streamlit's default `.gitignore` template — double check it's not tracked) with the same three keys.

## 5. Install ntfy

Install the [ntfy app](https://ntfy.sh) (iOS/Android) or use the ntfy web app. No account needed — after clicking "Get notified" in the web app, subscribe to the topic name it shows you.

## 6. Test end-to-end

- Run `streamlit run src/app/ui.py` locally, click "Get notified", subscribe to the shown topic in the ntfy app.
- Manually trigger `.github/workflows/daily_notify.yml` from the GitHub Actions tab (`Run workflow`) — with a birthday chosen so today is a match, you should get a push notification within a few seconds.
