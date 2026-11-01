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

3. In Project Settings -> API Keys, note the **Project URL** and the **publishable key**
   (`sb_publishable_...`), which goes in `SUPABASE_KEY`. Create named **secret keys**
   (`sb_secret_...`) for the privileged consumers listed in section 16. Each one goes in
   `SUPABASE_SECRET_KEY` for that consumer only.

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

> **Do not re-run this after section 9 (person-onward event phrases) has been applied.** Its
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

This writes chunk files under `data/tmp/enrichment_chunks/`. Dispatch a Claude Opus subagent
(Agent tool `model: "opus"`, i.e. `ingest.agent_batches.SUBAGENT_MODEL`) per chunk file, using `ingest.enrichment.build_prompt()` for instructions, and save each subagent's
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

**GitHub repo secrets** (Settings -> Secrets and variables -> Actions): the same three, plus
`SUPABASE_SECRET_KEY` (the `github-actions` secret key from section 16). The Streamlit secrets
leave `SUPABASE_SECRET_KEY` out on purpose, so the public app only ever runs as the publishable key.

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
`data/tmp/subject_chunks/`. Dispatch one Opus subagent (`model: "opus"`) per chunk file using
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
`data/displayable_events.json` into chunk files under `data/tmp/reword_chunks/`. Dispatch an
Opus subagent (`model: "opus"`) per chunk using `ingest.enrichment.build_prompt()` for instructions, then
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

## 9. Person-onward event phrases

`event_phrase` stores the sentence from the person onward ("Sir Richard Owen was when …"), not a
fixed opening. The tensed opening ("You were the same age", "You're the same age", "You'll be the
same age", depending on whether the reader is viewing a day before, on, or after today) is prepended
by the app at display time — see `core.matching.full_sentence` and
`docs/superpowers/specs/2026-08-22-tense-aware-display-text-design.md`. The reword subagent writes
the person-onward clause so it can place a title next to the name; `events.name` still holds the
bare name.

`core.matching.full_sentence`'s normalizer strips or reconstructs whatever shape an older row is in,
so rows written under any previous prompt version keep displaying correctly until the backfill below
has run.

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

Dispatch a Claude Opus subagent (`model: "opus"`) per chunk file under `data/tmp/enrichment_chunks/`, using
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
existing subscribers by default. `core.preferences.preferences_from_subscription` reads it defensively, so
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

`core.preferences.preferences_from_subscription` reads the column defensively
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

## 15. Music events (chart number ones, Eurovision winners)

Adds about 1,100–1,400 music events: Billboard Hot 100 number-one singles, Billboard 200 number-one
albums, and Eurovision Song Contest winners, each on the exact chart or final date. No SQL is needed.
The `music` tag has existed since section 4. Design and source notes are in
`docs/superpowers/specs/2026-09-24-music-events-ingestion-design.md`.

```bash
# 1. Download the pinned source files (~70 MB) into data/raw_data/music/
./venv/Scripts/python.exe -c "from ingest.sources.music import fetch_sources; fetch_sources()"

# 2. Resolve every credited person's birth and death dates (Wikidata, then the births file; cached, rerunnable)
./venv/Scripts/python.exe -m ingest.resolve_music_persons

# 3. Upsert persons and run the guards; writes data/music_displayable.json for review
./venv/Scripts/python.exe -m ingest.migrate_music_to_supabase prepare

# 4. Insert the events and their tags
./venv/Scripts/python.exe -m ingest.migrate_music_to_supabase insert
```

Step 2 takes a while on its first run: one Wikidata search per name the births file can't resolve,
plus one claims fetch per candidate. Every outcome is cached in `data/wikidata_musicians_cache.json`,
so reruns are fast. This cache is deliberately separate from `data/wikidata_persons_cache.json`,
because it holds musician-filtered lookups.

Read the review files before step 4:

- `data/tmp/music_resolution_review.json` lists the Hot 100 dates corrected against the chart
  archive, names with no resolvable birth date, rejected birth dates (a date contradicted by one of
  that person's records is dropped for all of them), and credits that name only a group. Groups get
  no event. It also lists `posthumous_record`s: number ones dated after the artist's Wikidata death
  date, such as "(Sittin' On) The Dock of the Bay". These are dropped, because an age at them means
  nothing. It lists `death_date_unknown` names too: resolved from the births file alone, with no
  death date to check. Look those over by hand.
- `data/tmp/music_person_review.json` lists names that normalize like an existing person's, and names
  whose resolved birth date disagrees with the birth date an existing person's events imply. A
  namesake, e.g. the corpus's "Ray Charles", is born 1918 but the singer was born 1930. These are
  skipped, not merged. Fix the existing person or the record by hand if one of them is wrong. A
  musician who really does share a name with someone else goes in `MUSICIAN_NAMESAKES`, which stores
  them as a separate "Name (musician)" person.
- `data/tmp/music_duplicate_review.json` lists events skipped because the person already has an
  event on that day.

Safe to rerun: `insert` skips any event whose person already has an events row that day. Afterwards,
section 12's `backfill_person_wikipedia` gives the new persons their Wikipedia links, and
`python -m ingest.backfill_music_wikipedia` then links stage names like "Prince", whose plain title is
another article, through their Wikidata item. Music rows are
worded by templates rather than the LLM, and `backfill_event_enrichment` skips them (as it does Nobel
rows) so a future generic re-phrasing pass leaves them alone.

## 16. Row-level security

Without RLS, anyone holding the publishable key can read every row of `subscriptions`. That is
every magic-link token and ntfy topic. They can also edit or delete any table. With RLS on:

| Who | Key | Can do |
| - | - | - |
| Streamlit app, Claude cloud **read** environment | publishable | read `events`, `persons`, `tags`, `event_tags`; insert a subscription; read/update one subscription **by its token** via two SQL functions |
| Cron job, local ingest, Claude cloud **write** environment | secret (bypasses RLS) | everything |

`core.db.get_client()` uses `SUPABASE_SECRET_KEY` when it is set and falls back to
`SUPABASE_KEY`. The ingest scripts therefore need no changes. They just need the secret key in
the environment they run in.

### Keys

In Project Settings -> API Keys -> Secret keys, create one named secret key per consumer. Each
can then be revoked alone if it leaks:

- `github-actions` → GitHub repo secret `SUPABASE_SECRET_KEY`
- `local-dev` → `SUPABASE_SECRET_KEY=` in your local `.env` (needed for every ingest script from now on)
- `claude-cloud` → the Claude cloud write environment (section 17)

### Step A: functions and policies

Run this first. It is harmless while RLS is still off, and the new app code needs the two
functions to exist:

```sql
-- One subscription by its magic-link token. Security definer, so it works
-- although the publishable key has no read access to the table itself.
create or replace function public.get_subscription(p_token text)
returns setof public.subscriptions
language sql stable security definer set search_path = ''
as $$
    select * from public.subscriptions where token = p_token;
$$;

-- Must name every column core.preferences.preferences_to_columns returns
-- (pinned by tests/test_db.py). A column added there but not here is
-- silently never saved.
create or replace function public.update_subscription_preferences(p_token text, p_preferences jsonb)
returns void
language sql security definer set search_path = ''
as $$
    update public.subscriptions set
        excluded_categories        = array(select jsonb_array_elements_text(p_preferences -> 'excluded_categories')),
        excluded_tags              = array(select jsonb_array_elements_text(p_preferences -> 'excluded_tags')),
        included_sequences         = array(select jsonb_array_elements_text(p_preferences -> 'included_sequences')),
        notify_mirrors_calendar    = (p_preferences ->> 'notify_mirrors_calendar')::boolean,
        notify_excluded_categories = array(select jsonb_array_elements_text(p_preferences -> 'notify_excluded_categories')),
        notify_excluded_sequences  = array(select jsonb_array_elements_text(p_preferences -> 'notify_excluded_sequences'))
    where token = p_token;
$$;

grant execute on function public.get_subscription(text) to anon, authenticated;
grant execute on function public.update_subscription_preferences(text, jsonb) to anon, authenticated;

drop policy if exists "public read" on public.events;
drop policy if exists "public read" on public.persons;
drop policy if exists "public read" on public.tags;
drop policy if exists "public read" on public.event_tags;
drop policy if exists "anyone can subscribe" on public.subscriptions;

create policy "public read" on public.events     for select to anon, authenticated using (true);
create policy "public read" on public.persons    for select to anon, authenticated using (true);
create policy "public read" on public.tags       for select to anon, authenticated using (true);
create policy "public read" on public.event_tags for select to anon, authenticated using (true);
create policy "anyone can subscribe" on public.subscriptions for insert to anon, authenticated with check (true);
```

### Step B: deploy, then switch RLS on

The order matters. Under RLS, the old app code's direct `select` on `subscriptions` returns
nothing, so every returning subscriber's link would stop recognising them.

1. Add the `SUPABASE_SECRET_KEY` GitHub secret and the local `.env` entry.
2. Merge and deploy the app code that calls the two functions above.
3. Then run:

```sql
alter table public.events        enable row level security;
alter table public.persons       enable row level security;
alter table public.tags          enable row level security;
alter table public.event_tags    enable row level security;
alter table public.subscriptions enable row level security;

-- Belt and braces: RLS does not govern TRUNCATE, and the publishable key
-- should not hold write privileges it has no policy for anyway.
revoke insert, update, delete, truncate on public.events, public.persons, public.tags, public.event_tags from anon, authenticated;
revoke select, update, delete, truncate on public.subscriptions from anon, authenticated;
```

### Verify

```bash
./venv/Scripts/python scripts/check_supabase_access.py
SUPABASE_SECRET_KEY= ./venv/Scripts/python scripts/check_supabase_access.py
```

The first run (secret key) reports subscriptions readable, `PRIVILEGED`. The second forces the
publishable key and must report `READ-ONLY`. If it says `RLS IS NOT ENABLED`, step B hasn't run.
Then open a saved magic link in the deployed app and save preferences to confirm the function
path works end to end.

**Any table added later starts with RLS off and full publishable-key access.** Give each new table
`enable row level security` and its policies in the same SQL that creates it. The Supabase
dashboard's Security Advisor flags tables that are missing it.

## 17. Claude Code cloud sessions (phone / claude.ai/code)

Two cloud environments, one per trust level. Pick one per session in the environment selector of
the Claude app's Code tab. Configure both at claude.ai/code → environment selector → edit.

`.claude/settings.json` runs `scripts/cloud_session_setup.sh` on session start, which `pip install`s
the project. No setup script is needed on the environment.

### `achievement-age read` (default for everyday development)

- **Network access**: Custom. Allowed domains: `izysgsejftrzrikzctuj.supabase.co`. Tick "Also
  include default list of common package managers" (pip needs PyPI).
- **Environment variables**:
  ```text
  SUPABASE_URL=https://izysgsejftrzrikzctuj.supabase.co
  SUPABASE_KEY=<publishable key>
  ```

The publishable key is safe here even though anyone using the environment can read it. That is
what it was designed for, and RLS caps it at reading public event data.

### `achievement-age write` (inserts and ingest runs)

Same network access and variables as the read environment, plus:

- **Environment variable** `SUPABASE_SECRET_KEY=sb_secret_injected_by_proxy`. This placeholder
  only switches `get_client()` to its secret-key path. The real key never enters the VM.
- **Network secret**: Name `Supabase claude-cloud`, Allowed websites
  `izysgsejftrzrikzctuj.supabase.co`, Secret type Bearer, two custom header rows, both with the
  `claude-cloud` secret key as the value:
  - Name `apikey`, prefix cleared
  - Name `Authorization`, prefix `Bearer`

Anthropic's proxy attaches the key after each request leaves the VM. Claude can use the key but
can never print it or leak it.

**Verify** in a new write session: ask Claude to run `python scripts/check_supabase_access.py`.
It must report `PRIVILEGED`. If it fails with `Invalid API key` instead, the proxy is adding
the headers alongside the placeholder rather than replacing it. In that case, delete the network
secret and set `SUPABASE_SECRET_KEY` to the real `claude-cloud` key as a plain environment
variable. Claude can then see the key. That's acceptable for a personal environment, and you can
revoke `claude-cloud` in Supabase at any time.

The secret key bypasses RLS, so a write session can also delete data or read subscriptions. The
free plan has no point-in-time restore, so take a backup before a large ingest run:
Database → Backups, or `pg_dump` with the connection string from Project Settings → Database.

## 18. Opus re-phrasing backpass (prompt version 3)

Phrasing moved from Claude Haiku to Claude Opus 5.5 subagents (`ingest.agent_batches.SUBAGENT_MODEL`,
Agent tool `model: "opus"`). `REWORD_PROMPT_VERSION` is now 3, which also adds explicit hinge rules
to `src/ingest/reword_prompt.md`. Every generic (non-Nobel, non-music) row below version 3 was
phrased by Haiku and is queued for the backpass.

Why: Haiku sometimes broke the `{person} was when {event}` template. It wrote "was when," (a comma
after the hinge) or a different form of the name ("King Louis of France" for "Louis XIV"). The old
display normalizer read those rows as the oldest suffix-only shape and prepended
"{name} was when", so the hinge appeared twice:

> You'll be the same age Louis XIV was when King Louis XIV of France was when, in Dover, ...

`core.matching` now finds the hinge with a punctuation- and whitespace-tolerant pattern and no
longer rebuilds a phrase that already has one, so these rows display correctly now. The backpass
rewrites them properly.

**1. Audit (read-only, any session):**

```bash
python scripts/audit_event_phrases.py
```

This prints how many rows the backpass will pick up, how many rendered with a doubled hinge under
the old display, and format-check failures grouped by reason. The full list goes to
`data/tmp/phrase_audit.json` (event data only, nothing private).

**2. Back up** (Database → Backups). The pass overwrites `event_phrase` on roughly every
generic row.

**3. Run it in a write session** (section 17), using the same commands as section 9:

```bash
python -c "from ingest.backfill_event_enrichment import prepare_chunks; print(len(prepare_chunks(mode='phrasing')))"
```

Dispatch one Opus subagent (`model: "opus"`) per `data/tmp/enrichment_chunks/chunk_NNNN.json`. Its
prompt is `ingest.enrichment.build_prompt()` plus the chunk's JSON, and it writes
`chunk_NNNN_result.json`. Merge as you go with `python -m ingest.agent_batches merge enrichment`,
which runs `merge_chunk` with the batch's mode on every finished chunk. If the session stops partway,
see section 19. Opus costs
more per token than Haiku. A test chunk first (`prepare_chunks(chunk_size=20, mode='phrasing')`,
merge one chunk, then spot-check it in the app) is cheap insurance.

The format check now gates this pass. If a new phrase fails `check_phrase_format`, the pass doesn't
write it: the row keeps its current phrase, stays below version 3, and gets a
`format` / `not written: ...` entry in `data/tmp/enrichment_review.json`. Rerunning `prepare_chunks`
picks up only those rows. New-event merges (`ingest.llm_utils`, `ingest.nobel_llm_utils`) still
write a phrase that fails the check, because there is nothing better to fall back on, but leave it
unstamped so the next pass retries it.

**4. Re-audit.** Rerun the audit script. "Doubled hinge" and format failures should be near zero.
Whatever is left is in the review file.

**Not covered:** the phrasing backpass skips Nobel rows (`STRUCTURED_SOURCES`), and no
Supabase backfill path exists for them yet. `NOBEL_REWORD_PROMPT_VERSION` is now 2, so future
Nobel ingests use the updated prompt, but existing Nobel rows keep their Haiku phrasing until a
Nobel backfill mode is built. Music rows come from templates, not an LLM.

Subject extraction (section 8, Stage 2) also runs on Opus now. Its no-subject cache
(`data/no_subject_cache.json`) still holds Haiku verdicts, so texts Haiku found no person in aren't
re-checked. To re-check them with Opus, bump `PROMPT_VERSION` in `src/ingest/subject_extraction.py`.

## 19. Subagent batches: stopping and resuming

All four subagent steps share `src/ingest/agent_batches.py`: new-event phrasing
(`ingest.llm_utils`), the enrichment/phrasing backfill, subject extraction and Nobel phrasing. A
session can stop at any point (usage or session limit, closed laptop, crash) without losing merged
work or corrupting anything. The next session continues where the last one stopped.

**Start every session with:**

```bash
python -m ingest.agent_batches status
```

For each batch, it shows how many chunks are merged, how many are ready to merge, and which still
need a subagent. For each of those it prints the chunk path, the result file name, and the prompt
function. Then:

1. `python -m ingest.agent_batches merge` merges every chunk that has a valid result.
2. Dispatch Opus subagents for the chunks still awaiting one, a few at a time. Each writes its
   whole JSON array to `chunk_NNNN_result.json` in a single Write.
3. After each wave, run `merge` again so finished work is saved before the next wave starts.
   Merging is per chunk, so a stop costs at most the chunks in flight.

**What makes stopping safe:**

- **Resume, not re-chunk.** While unmerged chunks exist, `prepare_*` returns them unchanged.
  Pass `fresh=True` to discard them. Starting a new batch clears old chunks, stale result files
  and `merged/`, so an old result can never be paired with a new chunk.
- **No partial results.** A missing, truncated or misshapen result file merges nothing, and the
  chunk is redispatched. Fallback text is never written in its place. That used to put raw scraped
  text into `displayable_events.json` for every unfinished chunk.
- **Dropped records stay pending.** A record a subagent left out of a valid result isn't merged,
  and the next `prepare_*` picks it up. For records a subagent keeps failing on,
  `merge_reworded_chunk(..., fallback_missing=True)` (and the same option on `merge_nobel_chunk`)
  writes the deterministic fallback on purpose.
- **Prompt-version guard.** Each chunk directory's `batch.json` records the prompt version.
  Merging output from an older prompt is refused, so it can't be stamped with the new version.
- **Idempotent merges.** Appends dedupe, `event_tags` is upserted, data files are written
  atomically, and a chunk moves to `merged/` only after its output is saved. A crash between those
  two steps just re-merges harmlessly.

**Cloud sessions:** `data/` is gitignored, and a cloud container is reclaimed after inactivity,
which can easily happen while waiting out a usage limit. What survives depends on the step:

- **Enrichment / phrasing backfill:** progress lives in Supabase, so it survives. A lost chunk
  directory only costs the chunks that were dispatched but not merged, and the next
  `prepare_chunks(mode=...)` re-derives the rest from the database.
- **Subject extraction, new-event phrasing and Nobel phrasing:** these read and write local files
  under `data/`, so run them on a machine where `data/` persists (your local checkout). In a cloud
  session, finish them through migration before the session ends.
