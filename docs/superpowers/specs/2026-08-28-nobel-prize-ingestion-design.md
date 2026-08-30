# Nobel Prize laureate ingestion

## Context

The calendar's `events` table has two ingestion paths so far: a text-matching pipeline
(`ingest.match_events` / `ingest.resolve_wikidata`) that finds *who* a scraped Wikipedia sentence is
about, and one-off migration scripts that write already-known facts straight to Supabase
(`ingest.migrate_to_supabase`). Nobel Prize laureates are a new source of the second kind: a
structured dataset (`data/raw_data/nobel_prizes_1901-2025_cleaned.csv`, 995 awards, 1901-2025) where
the subject is already named — no free-text matching needed — but a phrasing pass is still wanted so
the display sentence reads in the same voice as the rest of the calendar.

`plan.md` (written long before this pass) already anticipated a `nobel_json.py` source adapter under
Phase 3, so this direction predates this spec.

### What's actually in the file (corrects the Kaggle listing description)

The Kaggle dataset page's feature table claims "747 individuals + 243 organizations." Inspecting the
actual CSV shows that's wrong for this file: there are **zero organizations** in it (checked by
pattern-matching every `known_name` against institutional keywords — Committee, Union, Institute,
Red Cross, etc. — zero hits). All 990 distinct laureates are named individuals. The real split is:

- **752 rows / 747 distinct people** carry full biographical data: day-precision `birth_date`,
  `wikipedia_url`, birth/death city and country.
- **243 rows** carry only award data (name, category, year, `date_awarded`, `motivation`) — every
  biographical field, including `birth_date` and `wikipedia_url`, is blank. These are real people
  (Hans Bethe, Milton Friedman, Henry Kissinger, ...), not organizations; the dataset's own
  "cleaning" pass simply didn't backfill their biography.

`date_awarded` is a real day-precision date (`MM/DD/YYYY`) for all 995 rows — the Kaggle page's
"award_year only" framing undersold the file too.

### Scope decision

All 990 laureates are ingested, not just the 747 with ready biographical data. The 243 thin rows get
a birth-date resolution pass reusing the existing Wikidata Stage-3 machinery
(`ingest.sources.wikidata.lookup_birth_date`) rather than being deferred to a follow-up.

### Out of scope

- **Organizations.** Moot for this file (see above), but if a future refresh of the dataset
  reintroduces org rows, they are excluded — `age_days` has no meaning for a non-person laureate, and
  the `events`/`persons` schema has no room for one.
- **Prize amount, portion, affiliation, geographic coordinates, `sort_order`, `winners_per_*`.** None
  of these columns feed the age-match feature. They are read and then discarded, not stored anywhere.
- **Re-verifying `wikipedia_url` values the CSV already supplies.** They're taken as given, the same
  trust level `migrate_to_supabase` already extends to hand-authored data.

## Architecture

### `src/ingest/sources/nobel.py` (new) — parsing only, no network/DB calls

Loads the CSV (stripping the BOM the export leaves on the first header, `award_year`), parses
`date_awarded` (`MM/DD/YYYY`) and `birth_date` (`YYYY-MM-DD`) into ints, and emits one record per row:

```python
{
    "laureate_id": str, "name": str,       # known_name
    "category": str,                        # one of the 6 Nobel categories
    "award_year": int, "award_month": int, "award_day": int,
    "motivation": str,
    "wikipedia_url": Optional[str],
    "birth_year": Optional[int], "birth_month": Optional[int], "birth_day": Optional[int],
}
```

`split_by_birth_data(records)` returns `(with_birth_date, missing_birth_date)` — the 752/243 split
above, mirroring the shape of `sources/pantheon.py`'s helpers.

### `src/ingest/resolve_nobel_wikidata.py` (new) — resolves the 243 thin rows

For each `missing_birth_date` record, calls `ingest.sources.wikidata.lookup_birth_date(name,
award_year, cache)` unchanged — same on-disk cache, same plausibility bound (born before the award,
not more than 120 years before it), same `resolved`/`ambiguous`/`not_found`/`insufficient_precision`
outcomes. A `resolved` record rejoins the pool with its new birth date. Anything else is written to
`data/tmp/nobel_wikidata_review.json` via the existing `ingest.enrichment.write_review_entries` and is
simply not ingested this run — rerunning the script later costs no extra network calls for names
already cached, same as Stage 3 today.

### Age and the raw `text` field

`ingest.pipeline.calculate_age` computes `age_days` from birth date to award date unchanged, gated by
the same `0 <= age_days <= 120*365` bound used everywhere else in this pipeline (a real guard here:
the 243 Wikidata-resolved rows carry same-name-collision risk that the 752 CSV-supplied rows don't).

Each record's `events.text` — the stable "raw fact," playing the same role the scraped Wikipedia
sentence plays for the rest of the corpus — is built deterministically:

```
{name} won the Nobel Prize in {category}: "{motivation}"
```

with two special cases: `category == "Economic Sciences"` renders as *"the Nobel Memorial Prize in
Economic Sciences"*, since it's not one of the five prizes established in Alfred Nobel's 1895 will,
and `category == "Peace"` renders as *"the Nobel Peace Prize"* rather than the grammatically-awkward
"the Nobel Prize in Peace".

### `src/ingest/migrate_nobel_to_supabase.py` (new) — person resolution, the duplicate-day guard, and the final write

This is the one module that talks to Supabase, playing the same role `migrate_to_supabase.py` plays
for the historical corpus, and it owns the last two rollout stages (see Rollout order below): resolving
each record's `person_id` and guarding against duplicates *before* any LLM cost is spent, then —
after the phrasing pass has run — inserting the finished rows.

**Person matching.** Persons are upserted by exact `name` on `on_conflict="name"`, the same pattern
`ingest.backfill_persons_and_phrases.build_person_rows` already uses. Checking Nobel `known_name`
against the live `persons` table found **39 exact matches** (Marie Curie, Einstein, Churchill, Mother
Teresa, ...) — these correctly reuse their existing person row rather than duplicating it.

Exact-string matching alone misses near-duplicates from spelling variants: the same check, run through
`core.matching.normalize_name`, found **one** case — Nobel's "J.J. Thomson" vs. the existing "J. J.
Thomson." Rather than auto-merging (this codebase's stated position on same-name collisions is
detect-don't-guess, see `SUPABASE_SETUP.md`), a normalize_name collision pass runs before the main
import and writes any hit to `data/tmp/nobel_person_review.json` for a one-line manual fix to the
source record.

`persons.wikipedia_url` is written only where the existing row's value is currently null — for the 752
CSV-supplied URLs and any the Wikidata resolution pass turns up — never overwriting an
already-verified value, the same rule `backfill_person_wikipedia.py` already follows.

**Duplicate-day guard.** Wikipedia's "on this day" corpus — the source of the *existing* `events` rows
— already covers some Nobel Prize announcements. Querying the live DB found **6 exact person+date
collisions already present** (Jean-Paul Sartre, Martin Luther King Jr., Mikhail Gorbachev, Nadine
Gordimer, Shimon Peres, Svante Pääbo — e.g. `events.id=979` already reads "Jean-Paul Sartre is awarded
the Nobel Prize in Literature..." on the exact date the CSV also records). Left unguarded, ingestion
would create a duplicate event for each.

Once a record has a resolved `person_id`, this module checks existing `events` for a row with the same
`person_id` **and** exact `(year, month, day)`. A hit skips the record and is logged to
`data/tmp/nobel_duplicate_review.json` rather than silently dropped — one event per person per day,
per the accepted rule.

**Known accepted gap**: the same query also found 17 same-person/same-year-but-different-day cases
(e.g. Kawabata's and Kissinger's awards are recorded a day apart between the two sources; Selma
Lagerlöf is 29 days apart — announcement date vs. the December ceremony date). These are *not*
same-day, so the guard does not block them; they end up as two separate, slightly redundant but
individually truthful events. Not solved here — flagged so it isn't mistaken for a guard bug later.

**File handoff.** Records surviving person resolution and the duplicate-day guard are written to
`data/tmp/nobel_pending.json` — the Nobel equivalent of `events_with_age.json` in the historical
pipeline. This is what `nobel_llm_utils.prepare_nobel_chunks()` reads (see below). After the phrasing
pass merges results into `data/nobel_displayable.json` (equivalent to `displayable_events.json`),
re-running `migrate_nobel_to_supabase.py` reads that file and performs the actual `events`/
`event_tags` inserts — the same two-file, two-pass shape `migrate_to_supabase.py` already uses for the
historical corpus.

### Phrasing: a dedicated prompt, not `reword_prompt.md`

Nobel records don't need what `reword_prompt.md` exists for — there's no ambiguous subject to
correct, and no tags to guess (see below). A smaller prompt, `src/ingest/nobel_reword_prompt.md`,
takes `{name, category, award_year, motivation}` and returns `{name, event_phrase}` only, following
the same name-onward `"{person} was when {event}."` convention as the rest of the calendar.

`src/ingest/nobel_llm_utils.py` (new, mirrors `llm_utils.py`) implements the same two-phase
chunk/dispatch/merge flow already used for the original 1232-event batch and for
`backfill_event_enrichment.py`: `prepare_nobel_chunks()` writes numbered JSON files to
`data/tmp/nobel_reword_chunks/`, a Haiku subagent is dispatched per chunk, `merge_nobel_chunk()`
validates and writes results.

Validation reuses `ingest.enrichment.check_phrase_format` and `check_facts_preserved` unchanged — both
already operate on just `(text, event_phrase, name)`. A record with no usable `event_phrase` gets a
deterministic fallback (`"{name} was when they won the Nobel Prize in {category}."`), matching the
existing corpus's fallback behavior, rather than being dropped.

### Tags: deterministic, not LLM-chosen

```python
NOBEL_CATEGORY_TAGS = {
    "Physics": "science",
    "Chemistry": "science",
    "Physiology or Medicine": "health",
    "Literature": "arts",
    "Peace": "politics",
    "Economic Sciences": "economics",
}
```

Each event gets exactly one tag, assigned from this table at merge time — never asked of the LLM. This
is a deliberate divergence from `llm_utils.merge_reworded_chunk`, which does let the subagent choose
tags: that flexibility exists there because scraped event text is open-ended and needs judgment about
*what it's about*. A Nobel category is already a closed, known fact; letting an LLM re-derive the tag
would risk it disagreeing with the mapping above (e.g. filing Peace under "social" instead of
"politics") on no better information than this table already has.

`event_type` is `"achievement"` (existing value, no schema constraint on it — a Nobel win fits the
existing semantics). `source` is `"nobel_prize_dataset"`, a new provenance label playing the same role
`"initial_migration"` already plays.

### A safeguard `backfill_event_enrichment.py` needs

That module's `mode="phrasing"` backfill selects every event below the current
`REWORD_PROMPT_VERSION` and re-words it with `ingest.enrichment.build_prompt()` — the generic
historical-event prompt, which expects scraped free text and asks the LLM to choose tags. Left as-is,
a future bump to `REWORD_PROMPT_VERSION` would sweep Nobel rows into that pass too, re-wording them
with a prompt built for different input and re-tagging them by free LLM choice, silently undoing the
category mapping above.

`pending_phrasing_events` (and the `_fetch_all_events` selection feeding it) is extended to exclude
rows whose `source` is `"nobel_prize_dataset"`. Nobel rows get their own prompt-version story if
`nobel_reword_prompt.md` is ever revised — out of scope here since there's no revision to plan for yet.

## Testing

- `tests/test_nobel.py` (new) — CSV parsing (including the BOM strip and the two date formats),
  `split_by_birth_data`, `NOBEL_CATEGORY_TAGS` coverage (one entry per category found in the file),
  the constructed `text` sentence including the Economic Sciences special case.
- `tests/test_resolve_nobel_wikidata.py` (new) — thin-row resolution, reusing the existing
  `sources.wikidata` test-mock pattern (`test_wikidata.py`) for `resolved`/`ambiguous`/`not_found`.
- `tests/test_nobel_llm_utils.py` (new) — chunk/merge logic, the fallback phrase, review-entry
  generation for format/fact-check failures — mirroring `test_llm_utils.py`'s structure.
- `tests/test_migrate_nobel_to_supabase.py` (new) — the person-upsert/near-duplicate-name check (using
  the real "J.J. Thomson" / "J. J. Thomson" case as a fixture) and the person_id+exact-date duplicate
  guard (using two or three of the six real collisions found above as fixtures — Sartre and Gordimer
  are simple single-award cases, good for a minimal regression test).
- `tests/test_backfill_event_enrichment.py` gains a case: a Nobel-sourced row (`source =
  "nobel_prize_dataset"`) below `REWORD_PROMPT_VERSION` is excluded from `mode="phrasing"` selection.

## Rollout order

1. Parse the CSV (`sources.nobel`).
2. Resolve the 243 thin rows' birth dates via Wikidata (`resolve_nobel_wikidata`); rows that don't
   resolve are excluded from this run, not blocking it.
3. `migrate_nobel_to_supabase.py`, phase 1: resolve `person_id` for every remaining record (persons
   upsert, near-duplicate-name check) and run the duplicate-day guard against live Supabase — the last
   point before any LLM cost is spent, so a bad guard or upsert never wastes a phrasing pass. Survivors
   are written to `data/tmp/nobel_pending.json`.
4. LLM phrasing pass (`nobel_llm_utils`): `prepare_nobel_chunks()` reads `nobel_pending.json` and
   writes numbered chunk files; a Haiku subagent is dispatched per chunk; `merge_nobel_chunk()`
   validates each result and appends to `data/nobel_displayable.json`.
5. `migrate_nobel_to_supabase.py`, phase 2: reads `nobel_displayable.json` and inserts the `events`/
   `event_tags` rows.

Nothing is written to Supabase until step 5 — steps 1-4 are file-based and rerunnable, same as the
existing corpus's staged pipeline, so an interrupted or partially-reviewed run never leaves half-worded
rows live.

## Housekeeping

None of `IDEAS.md`'s existing entries describe this feature, so there's nothing there to update.
