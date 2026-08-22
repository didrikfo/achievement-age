# Supabase + ntfy setup (one-time)

## 1. Create the Supabase project

1. Create a free project at [supabase.com](https://supabase.com).
2. In the project's SQL editor, run:

```sql
create table events (
    id bigint generated always as identity primary key,
    name text not null,
    text text not null,
    event_phrase text not null,
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

Locally, fill in `SUPABASE_URL`/`SUPABASE_KEY` in the `.env` file at the repo root (created for
you, gitignored — `core/db.py` loads it automatically via `python-dotenv`, so this one file
covers both standalone scripts and a local `streamlit run`, no `.streamlit/secrets.toml` needed
for local dev):

If the local `displayable_events.json` being migrated already has `tags` populated on its events
(i.e. this isn't the very first migration into a fresh project), run the tag-seeding SQL in
section 4 first — `migrate_to_supabase` looks up each tag name in the `tags` table and silently
skips any tag it can't find, so migrating tagged events against an empty `tags` table loses every
tag with no error.

```bash
pip install -e .
pip install -r requirements.txt
python -m ingest.migrate_to_supabase
```

Confirm in the Supabase table editor that `events` now has 1232 rows.

## 3. Persons and event detail fields

Run this in the SQL editor to add the `persons` table and the new optional event fields.
This SQL and the backfill script below must be run before deploying/merging the updated
application code: the app's `fetch_events()` queries a `persons` join that doesn't exist
until this SQL runs, and both the Streamlit UI and the daily notification script call
`fetch_events()` — running the new app code against a database missing this SQL will crash
the whole app.

```sql
create table persons (
    id bigint generated always as identity primary key,
    name text not null unique,
    wikipedia_url text,
    created_at timestamptz not null default now()
);

alter table events add column person_id bigint references persons (id);
alter table events add column detailed_description text;
```

Then run the one-off backfill (same environment/credentials as the original migration):

```bash
python -m ingest.backfill_persons_and_phrases
```

> **Do not re-run this after section 9 (full-sentence event phrases) has been applied.** Its
> `strip_prefix` step assumes `event_phrase` holds only the suffix, so it would strip the "The
> same age that {name} was when " opening from the full-sentence format too — silently discarding
> any title placed next to the name (e.g. "Sir Richard Owen"), since the rebuilt opening is plain
> name only.

This creates one `persons` row per distinct event name, links every event to its person, and
splits each `event_phrase` value down to just the suffix after "The same age that {name} was
when " (the prefix is now built at display time). Any row whose text didn't match that exact
prefix is printed at the end instead of being silently mangled — check the output for stragglers
and fix them by hand in the Supabase table editor if any show up.

`wikipedia_url` (on `persons`) and `detailed_description` (on `events`) are left empty — fill
them in later, e.g. via the Supabase table editor, as you get to it.

## 4. LLM event enrichment (tags and subject corrections)

Run this in the SQL editor to seed the fixed tag taxonomy into the `tags` table (already created
in step 1) — the backfill script below assigns tags by name, so the rows need to exist first.
`on conflict (name) do nothing` makes this safe to run more than once.

```sql
insert into tags (name, color) values
    ('military', '#6B4226'),
    ('politics', '#1E3A8A'),
    ('science', '#0F766E'),
    ('technology', '#2563EB'),
    ('exploration', '#B45309'),
    ('space', '#312E81'),
    ('arts', '#A21CAF'),
    ('music', '#7C3AED'),
    ('film', '#BE123C'),
    ('sports', '#EA580C'),
    ('religion', '#A16207'),
    ('royalty', '#86198F'),
    ('economics', '#15803D'),
    ('law', '#334155'),
    ('disaster', '#B91C1C'),
    ('health', '#0D9488'),
    ('social', '#C2410C'),
    ('education', '#1D4ED8'),
    ('philosophy', '#4338CA'),
    ('engineering', '#57534E')
on conflict (name) do nothing;
```

Then run the one-off backfill (same environment/credentials as the earlier migrations):

```bash
python -c "from ingest.backfill_event_enrichment import prepare_chunks; print(prepare_chunks())"
```

This writes chunk files under `data/tmp/enrichment_chunks/`. Dispatch a Claude Haiku subagent per
chunk file, using `ingest.enrichment.build_prompt()` for instructions, and save each subagent's
JSON response next to its chunk as `<chunk>_result.json`. Then merge each chunk:

```bash
python -c "from ingest.backfill_event_enrichment import merge_chunk; merge_chunk('data/tmp/enrichment_chunks/chunk_0000.json', 'data/tmp/enrichment_chunks/chunk_0000_result.json')"
```

This regenerates each event's `event_phrase` from its raw `text` (the existing `event_phrase` in
the database isn't fetched or reviewed — it's simply overwritten), assigns 1-3 tags from the list above, and
flags cases where the matched name looks like the wrong subject. Tag assignments are written to
`event_tags`; subject corrections are only applied when the suggested alternate is both mentioned
in the event text and a known person with a computable birth date — anything that doesn't clear
that bar is written to `data/tmp/enrichment_review.json` for a manual look instead of being
guessed. The script is resumable: `prepare_chunks()` only includes events that don't already have
`event_tags` rows, so re-running the whole process after fixing something picks up where it left
off.

## 5. Configure secrets

**Streamlit Community Cloud** (App settings -> Secrets), as TOML:

```toml
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "..."
APP_BASE_URL = "https://your-app-name.streamlit.app"
```

(`APP_BASE_URL` can only be filled in *after* the first deploy, once you know the app's URL — redeploy/update the secret once you have it.)

**GitHub repo secrets** (Settings -> Secrets and variables -> Actions), same three: `SUPABASE_URL`, `SUPABASE_KEY`, `APP_BASE_URL`.

Local development uses the `.env` file from step 2 (gitignored) for all three keys — no separate
`.streamlit/secrets.toml` needed. `APP_BASE_URL` can be left blank locally; it's only used to
build the "Click" link in a push notification and to build the magic-link signup URL, neither of
which matters for local testing.

## 6. Install ntfy

Install the [ntfy app](https://ntfy.sh) (iOS/Android) or use the ntfy web app. No account needed — after clicking "Get notified" in the web app, subscribe to the topic name it shows you.

## 7. Test end-to-end

- Run `streamlit run src/app/ui.py` locally, click "Get notified", subscribe to the shown topic in the ntfy app.
- Manually trigger `.github/workflows/daily_notify.yml` from the GitHub Actions tab (`Run workflow`) — with a birthday chosen so today is a match, you should get a push notification within a few seconds.

## 8. Matching expansion runbook

Grows the pool of age-matchable events beyond the original 1232. Requires the ingest-only
dependencies (not installed by Streamlit Community Cloud, which only installs
`requirements.txt` — see `requirements-ingest.txt`'s header comment):

```bash
venv\Scripts\python.exe -m pip install -r requirements-ingest.txt
```

**Stage 0/1 — widened local matching** (no network, no LLM; a few seconds):

```bash
venv\Scripts\python.exe -m ingest.match_events
```

Appends matches to `data/events_with_age.json`, queues the rest in
`data/tmp/subject_pending.json`, and logs implausible ages to
`data/tmp/matching_review.json`. Safe to rerun — matches are deduped by
`(name, text)`, so a second run appends nothing new.

**Stage 2 — LLM subject extraction** (run from inside a Claude Code session):

```bash
venv\Scripts\python.exe -c "from ingest.subject_extraction import prepare_subject_chunks; print(prepare_subject_chunks())"
```

This splits `data/tmp/subject_pending.json` into chunk files under
`data/tmp/subject_chunks/`. Dispatch one Haiku subagent per chunk file using
`ingest.subject_extraction.build_prompt()` as the instructions, write each subagent's
JSON array to `<chunk>_result.json` next to it, then merge each chunk:

```bash
venv\Scripts\python.exe -c "from ingest.subject_extraction import merge_subject_chunk; print(merge_subject_chunk('data/tmp/subject_chunks/chunk_0000.json', 'data/tmp/subject_chunks/chunk_0000_result.json'))"
```

Matched subjects are appended to `data/events_with_age.json`; subjects that are real people
but unknown locally are queued in `data/tmp/wikidata_pending.json` for Stage 3; everything
else lands in `data/tmp/matching_review.json`.

**Stage 3 — Wikidata resolution** (hits the network; rate-limited and cached):

```bash
venv\Scripts\python.exe -m ingest.resolve_wikidata
```

Resolves `data/tmp/wikidata_pending.json` against Wikidata and appends any match to
`data/events_with_age.json`. Safe to rerun — every lookup outcome is cached by name in
`data/wikidata_persons_cache.json`, so a second run makes no network requests for names
already attempted.

**Then reword for display and migrate.** New matched events in `data/events_with_age.json`
still need an `event_phrase` (and tags, and any final subject correction) before they're
displayable — the same reword step used to prepare the original 1232 events, not otherwise
documented as its own section in this file:

```bash
venv\Scripts\python.exe -c "from ingest.llm_utils import prepare_reword_chunks; print(prepare_reword_chunks())"
```

This splits whatever in `data/events_with_age.json` isn't already in
`data/displayable_events.json` into chunk files under `data/tmp/reword_chunks/`. Dispatch a
Haiku subagent per chunk using `ingest.enrichment.build_prompt()` for instructions, then
merge each chunk:

```bash
venv\Scripts\python.exe -c "from ingest.llm_utils import merge_reworded_chunk; print(merge_reworded_chunk('data/tmp/reword_chunks/chunk_0000.json', 'data/tmp/reword_chunks/chunk_0000_result.json', births_path='data/historical_births_cleaned.json'))"
```

Pass `births_path` explicitly here — it defaults to `data/top_1000_births.json`, and by
construction almost everyone newly matched by the stages above is *not* in the top 1000, so
every subject correction the reword subagent suggests for them would be rejected as "not in
known births list" and dumped into the review report. `data/historical_births_cleaned.json` is
the same widened pool `ingest.match_events` uses (`WIDENED_BIRTHS_PATH`).

This appends to `data/displayable_events.json`. Then run the migration from section 2,
which skips anything already in Supabase and so only inserts the newly added events:

```bash
venv\Scripts\python.exe -m ingest.migrate_to_supabase
```

**Review before trusting the output.** `data/tmp/matching_review.json` collects every
event that could not be auto-accepted — ambiguous subjects, birth dates that are only
year-precision, implausible ages. Nothing in it was guessed at or silently dropped.

## 9. Full-sentence event phrases

`event_phrase` now stores the **complete** display sentence ("The same age that Sir Richard Owen was
when …"), not just the fragment after "…was when ". The reword subagent writes the whole sentence so
it can place a title next to the name; `events.name` still holds the bare name.

`core.matching.full_sentence` prefixes anything that doesn't already open with "The same age that",
so rows in the older suffix-only format keep displaying correctly until the backfill below has run.

Run this in the SQL editor first — it tracks which rows have been written under which version of
`src/ingest/reword_prompt.md`, so the backfill is resumable and future prompt revisions are
re-runnable:

```sql
alter table events add column if not exists reword_prompt_version integer not null default 0;
```

Then migrate any events still sitting in `data/displayable_events.json`:

```bash
./venv/Scripts/python.exe -m ingest.migrate_to_supabase
```

This prints a preflight check before inserting anything. It must report no unmatched records — if it
aborts, a previously-migrated event's name has changed in Supabase and needs reconciling by hand,
because migrating would insert duplicates rather than skip them.

Finally, re-phrase every event that predates the current prompt:

```bash
./venv/Scripts/python.exe -c "from ingest.backfill_event_enrichment import prepare_chunks; print(len(prepare_chunks(mode='phrasing')))"
```

Dispatch a Claude Haiku subagent per chunk file under `data/tmp/enrichment_chunks/`, using
`ingest.enrichment.build_prompt()` for instructions, and save each response next to its chunk as
`<chunk>_result.json`. Then merge each one:

```bash
./venv/Scripts/python.exe -c "from ingest.backfill_event_enrichment import merge_chunk; merge_chunk('data/tmp/enrichment_chunks/chunk_0000.json', 'data/tmp/enrichment_chunks/chunk_0000_result.json', mode='phrasing')"
```

This pass writes **only** `event_phrase` and `reword_prompt_version`. It does not touch tags, and it
records suggested subject corrections in `data/tmp/enrichment_review.json` without applying them —
subject errors are a separate piece of work. It's resumable: `prepare_chunks(mode='phrasing')` only
picks up rows still below the current `REWORD_PROMPT_VERSION`.

Afterwards, read `data/tmp/enrichment_review.json`. Expect a large number of `facts` entries — the
fact check is deliberately over-sensitive and flagged roughly one record in six when prototyped, so
it's a triage queue rather than a defect count. `format` entries are rarer and mean the subagent
ignored the sentence template.

## 10. Tag filtering preferences

Run this in the SQL editor **before** deploying the tag-filtering application code — the app's
"Update preferences" button writes to this column, and the write fails if it doesn't exist.

```sql
alter table subscriptions add column if not exists excluded_tags text[] not null default '{}';
```

No backfill is needed. The `default '{}'` fills every existing row with an empty exclusion list,
so current subscribers keep receiving every match exactly as before.

The column stores **exclusions**, not inclusions: the app's filter UI shows all tags checked by
default and stores the complement of the selection. That way a tag added to `TAG_TAXONOMY` later
is visible to existing subscribers by default, rather than silently hidden because it wasn't in
an inclusion list written before the tag existed.

`scripts/send_daily_notifications.py` reads this column defensively (`.get("excluded_tags") or []`),
so if the cron job runs before this SQL is applied it falls back to no filtering rather than
failing the entire run.

## 11. Coarse category filtering preferences

Run this in the SQL editor **before** deploying the category-filtering application code — the
app's "Update preferences" button writes this column, and the write fails if it doesn't exist.

```sql
alter table subscriptions add column if not exists excluded_categories text[] not null default '{}';
```

No backfill is needed. The `default '{}'` fills every existing row with an empty exclusion list, so
current subscribers keep receiving every match exactly as before.

The eight categories are derived from the existing tags at read time
(`core.config.TAG_CATEGORIES` → `core.matching.primary_category`), so there is nothing to migrate
on the `events`, `tags` or `event_tags` side and no re-tagging run. Changing which category a tag
belongs to is a code change that takes effect immediately, with no backfill.

**That "no backfill" ease does not extend to renaming a category itself.** `excluded_categories`
persists the literal `TAG_CATEGORIES` keys (e.g. `"War & Conflict"`) into every subscriber's row,
and `core.matching.included_from_excluded` matches them by exact string. Renaming a key in
`core.config.py` (even a copy-editing tweak like "Sport" -> "Sports") silently orphans every
existing subscriber's exclusion for that category — no error, no failing test, the row just stops
matching and those subscribers start getting notifications they'd opted out of. If a category is
ever renamed, either keep the old string as an alias wherever `TAG_CATEGORIES` is matched, or write
a one-off migration that rewrites the old name to the new one in every `subscriptions.excluded_categories`
array.

Like `excluded_tags`, this column stores **exclusions**, so a category added later is visible to
existing subscribers by default. `core.matching.events_for_subscription` reads it defensively, so
if the cron job runs before this SQL is applied it falls back to tag-only filtering rather than
failing the run.

## 12. Person Wikipedia links

Fills `persons.wikipedia_url`, which the event dialog renders as a "further reading" link. No SQL
is needed — the column has existed since section 3.

```bash
./venv/Scripts/python.exe -m ingest.backfill_person_wikipedia
```

Roughly 10–15 minutes for the full corpus: article titles resolve 50 per request, but each
candidate's birth year is one small Wikidata REST call.

Every candidate article is verified against the person's real birth year, derived from their own
event rows (event date minus age in days), with a ±1 year tolerance for calendar edge cases. Only
verified links are written; disambiguation pages, missing articles, birth-year mismatches and
people whose events imply conflicting birth dates are listed in
`data/tmp/person_wikipedia_review.json` instead. Read that file after a run — the rejections should
look like obscure names and genuine ambiguity, not a systematic failure.

Safe to rerun: rows that already have a URL are skipped entirely, so hand-corrected values are
never overwritten, and every lookup outcome is cached in `data/wikipedia_person_cache.json`. To
force a re-lookup for one person, clear their `wikipedia_url` in the Supabase table editor and
delete their entry from that cache file.

## 13. Mathematical anniversary preferences

Run this in the SQL editor **before** deploying the mathematical-anniversary application code —
the app's "Update preferences" button writes this column, and the write fails if it doesn't exist.

```sql
alter table subscriptions add column if not exists included_sequences text[] not null default '{}';
```

No backfill is needed, and no backfill is *wanted*. Unlike `excluded_tags` and `excluded_categories`,
this column stores **inclusions**: it lists the sequences a subscriber chose to track, and an empty
array means the whole feature is off for them.

That inversion is the entire rollout mechanism. Mathematical anniversaries must not start firing at
existing subscribers who signed up for historical events — and with primes enabled a subscriber
would hear from us roughly every ninth day. `default '{}'` makes every existing row opt-out by
construction, with nothing to migrate and nobody to miss. An exclusion column would have needed a
one-off `UPDATE` over every row plus app code to keep seeding it, either of which fails open if it
misses someone.

`core.sequences.included_sequences_for_subscription` reads the column defensively
(`.get("included_sequences") or []`), so if the cron job runs before this SQL is applied it falls
back to sending no anniversaries — which is also the intended default, so a slipped deployment
order fails safe in both directions.

**Renaming a sequence orphans subscribers, exactly like renaming a category (section 11).** The
eight names in `core.config.SEQUENCE_TAXONOMY` (`"Powers of 2"`, `"Fibonacci numbers"`, …) are
persisted verbatim into this column and matched by exact string. Renaming one — even a copy edit
like "Primes" → "Prime numbers" — silently drops it from every subscriber who selected it. Keep the
old string as an alias, or migrate the arrays.

## 14. Two-channel filter preferences

Run this in the SQL editor **before** deploying the two-channel filter code — the app's "Save
preferences" button writes these columns, and the write fails if they don't exist.

```sql
alter table subscriptions add column if not exists notify_mirrors_calendar    boolean not null default true;
alter table subscriptions add column if not exists notify_excluded_categories text[]  not null default '{}';
alter table subscriptions add column if not exists notify_excluded_sequences  text[]  not null default '{}';
```

No backfill is needed, and that is the point of the mirror flag. `default true` puts every existing
subscriber on "notifications follow my calendar", which is exactly the behaviour they had before
the split — so nobody who muted a category starts hearing about it again.

The three pre-existing columns (`excluded_categories`, `excluded_tags`, `included_sequences`) keep
their meaning unchanged and now describe the **calendar** channel. The two new list columns describe
the **notification** channel, and are read only while `notify_mirrors_calendar` is false. They are
always intersected with the calendar selection on read (`core.preferences.Preferences.notify`), so a
stale entry for a category the subscriber later hid can never resurrect a notification.

**Both override columns store exclusions**, so "absent from the override" means "notifies" on
either axis. They are deliberately not symmetric with the calendar columns: an earlier draft made
`notify_excluded_sequences` inclusions to match `included_sequences`, and it was implemented and found
broken — nothing maintains the override while mirroring, so every marked sequence was absent from
it, and unticking the mirror toggle silenced all of a subscriber's anniversaries at once.

Opt-in still holds, one level up: a sequence nobody marked is not in the calendar selection, and
`Preferences.notify` intersects the override with the calendar, so the override's permission is
unreachable for an unmarked row. That is also why no seeding step is needed when the mirror breaks.

There is no `notify_excluded_tags` — fine tags narrow both channels equally.

**The rename hazard from sections 11 and 13 now applies twice over.** A `TAG_CATEGORIES` key is
persisted verbatim into both `excluded_categories` and `notify_excluded_categories`; a
`SEQUENCE_TAXONOMY` name into both `included_sequences` and `notify_excluded_sequences`. Renaming
either orphans subscriber state in two columns instead of one, silently. Keep an alias or write a
migration that rewrites **both**.

`core.preferences.preferences_from_subscription` reads every column defensively, so if the daily
cron job runs before this SQL is applied it falls back to the calendar channel rather than failing
the run.
