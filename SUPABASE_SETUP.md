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

## 3. Configure secrets

**Streamlit Community Cloud** (App settings -> Secrets), as TOML:

```toml
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "..."
APP_BASE_URL = "https://your-app-name.streamlit.app"
```

(`APP_BASE_URL` can only be filled in *after* the first deploy, once you know the app's URL — redeploy/update the secret once you have it.)

**GitHub repo secrets** (Settings -> Secrets and variables -> Actions), same three: `SUPABASE_URL`, `SUPABASE_KEY`, `APP_BASE_URL`.

For local development, create `.streamlit/secrets.toml` (already gitignored by Streamlit's default `.gitignore` template — double check it's not tracked) with the same three keys.

## 4. Install ntfy

Install the [ntfy app](https://ntfy.sh) (iOS/Android) or use the ntfy web app. No account needed — after clicking "Get notified" in the web app, subscribe to the topic name it shows you.

## 5. Test end-to-end

- Run `streamlit run src/app/ui.py` locally, click "Get notified", subscribe to the shown topic in the ntfy app.
- Manually trigger `.github/workflows/daily_notify.yml` from the GitHub Actions tab (`Run workflow`) — with a birthday chosen so today is a match, you should get a push notification within a few seconds.
