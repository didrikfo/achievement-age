# Persons table, event detail fields, and display-text restructuring

## Context

The app was just migrated from local JSON files to Supabase (events, tags, event_tags, subscriptions
tables; see `SUPABASE_SETUP.md`). While reviewing that work, three follow-on gaps became clear:

1. Clicking an event in the calendar only ever shows one short, LLM-reworded sentence
   (`display_text`) — there's no room for a longer writeup or a source link.
2. `display_text` currently stores the *entire* sentence ("The same age that George Washington was
   when he hoisted the flag..."), duplicating the static "The same age that {name} was when " prefix
   1232 times instead of storing it once and building it at display time.
3. People are identified only by a free-text `name` column on `events`. Two different historical
   figures who happen to share a name can't be told apart, and there's nowhere to attach
   person-level metadata (starting with a Wikipedia link) without repeating it on every one of that
   person's events.

This spec covers adding a `persons` table, two new optional fields on `events`
(`person_id`, `detailed_description`), and restructuring `display_text` into `event_phrase` (suffix
only) with the static prefix reconstructed at display time.

## Schema changes

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

- `persons` is intentionally minimal: `name` + `wikipedia_url` only. `occupation`/`industry`/`domain`
  (available in the old `data/top_1000_births.json` source but unused since the Event/Person
  dataclasses were deleted in the Supabase migration) are explicitly **not** carried over now — add
  them later if a real need (e.g. tag-like filtering by occupation) shows up.
- `events.name` is **unchanged** and stays denormalized (not replaced by a join). `person_id` is
  added alongside it purely for disambiguation and to resolve the Wikipedia link. This was a
  deliberate choice over full normalization (dropping `events.name` in favor of always joining to
  `persons`): the matching pipeline (`core.matching.name_matches_text` and friends), the notification
  script, and the UI all currently read `event["name"]` directly, and none of that code needs to
  change under this approach. Full normalization was considered and rejected as a refactor of
  already-working code for a data-correctness benefit that doesn't matter at ~1200 rows.
- `wikipedia_url` is person-level only, not per-event — every event by a given person shows the same
  link (their bio page). No event-level override field.
- Both new `events` columns are nullable/optional, matching the request. All 1232 existing rows will
  have `person_id` backfilled (see below) but `detailed_description` and `persons.wikipedia_url` stay
  `NULL` until populated later (manually, or a future Wikipedia-summary scrape — explicitly deferred,
  not designed here).

## Display-text restructuring (`display_text` → `event_phrase`)

- `event_phrase` stores only the fragment after "...was when " (e.g. "he hoisted the first United
  States flag..."), not the full sentence.
- The full sentence is built at display/notification time via one new shared helper —
  `full_sentence(event) -> str` in `src/core/matching.py` — returning
  `f"The same age that {event['name']} was when {event['event_phrase']}"`. Both `src/app/ui.py`
  (`show_event_dialog`) and `scripts/send_daily_notifications.py` call this instead of reading a
  `display_text`/`event_phrase` field directly, so the two call sites can't drift out of sync the way
  two independent f-strings could.
- `src/ingest/llm_utils.py`: `_fallback_display_text` is renamed to `_fallback_event_phrase` and
  changes to return just the lowercased original `text` (no "The same age that {name} was when "
  prefix) — same logic, minus the static part.
- Any future Haiku-subagent rewording batch (e.g. once death events or new sources are ingested) will
  be prompted to produce only the suffix fragment. There's no stored prompt template in the repo
  today (the prompt is crafted live per batch run); this is a convention to follow next time, not a
  code change.

## UI changes (`src/app/ui.py`, `show_event_dialog`)

- Sentence rendered via `full_sentence(event)` instead of `event["display_text"]`.
- Below the sentence, show `event["detailed_description"]` if set, otherwise fall back to
  `event["text"]` (the original raw event description — already fetched, just not currently
  displayed).
- If the event's person has a `wikipedia_url`, show it as a "Read more on Wikipedia" link.
- `core/db.py`'s `fetch_events()` changes its Supabase select to pull the joined person data in one
  round trip: `select("*, persons(wikipedia_url)")` (Supabase's embedded-resource select via the
  `person_id` foreign key), rather than a second query or a Python-side dict join.

## Notification script changes (`scripts/send_daily_notifications.py`)

- Switches to `full_sentence(event)` for the ntfy notification body, so the notification text matches
  what the UI shows exactly (today it duplicates the sentence-building logic independently).

## Migration & backfill (one-off, run once against the live Supabase project)

New script: `src/ingest/backfill_persons_and_phrases.py`, following the same shape as the existing
`migrate_to_supabase.py` (reads live data via `core/db.py`, no local JSON involved since the events
table is already the source of truth).

1. **Persons**: create one `persons` row per distinct `events.name` string currently in the table.
   This is a 1:1 name→person mapping — it does **not** attempt to detect two different real people
   who happen to share a name. Splitting a genuine name collision into two `persons` rows is a
   manual/human judgement call for later; the schema supports it (each event can point at either
   row), this script just doesn't attempt the detection.
2. **Backfill `person_id`**: update every event row to point at its matching `persons` row by name.
3. **Backfill `event_phrase`**: for each event, compute the expected old prefix
   `f"The same age that {event['name']} was when "` and strip it from the current `display_text`
   value if present at the start (exact match). Rows where that exact prefix isn't found get logged
   (event id + current text) instead of silently mangled or skipped, so the handful of likely
   stragglers (Haiku-generated text that didn't follow the template exactly) can get a small
   manual/LLM touch-up pass afterward. This needs no LLM call for the rows that do match, since the
   fallback-generated rows follow the template exactly and the Haiku-generated ones were prompted to
   follow it too.
4. `wikipedia_url` (persons) and `detailed_description` (events) are left `NULL` for every row.

`SUPABASE_SETUP.md` gets a new section documenting this migration (SQL to run, then
`python -m ingest.backfill_persons_and_phrases`), matching how the original migration was documented.

## Testing

- `tests/test_llm_utils.py`: update for the renamed `_fallback_event_phrase`, asserting suffix-only
  output (no "The same age..." prefix) instead of the full sentence.
- New test for `full_sentence()` in `tests/test_matching.py`, covering the prefix + `event_phrase`
  reconstruction.
- Full `pytest` run after implementation.
- A live end-to-end browser check against the real Supabase project (confirming the dialog shows
  detailed description / Wikipedia link correctly, and a notification renders the reconstructed
  sentence) needs to happen together with the user, since it requires the migration to actually run
  against their live project and their credentials — same constraint as the original Supabase setup.

## Out of scope (explicitly deferred)

- Detecting/splitting genuine same-name-different-person collisions in the backfill.
- Populating `detailed_description` with anything beyond the existing raw `text` (a future
  Wikipedia-summary scrape or similar was mentioned as a later idea, not designed here).
- `occupation`/`industry`/`domain` or any other person metadata beyond `name` + `wikipedia_url`.
- Event-level Wikipedia link overrides.
