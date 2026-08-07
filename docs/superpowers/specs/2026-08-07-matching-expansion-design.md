# Matching expansion: growing the pool of age-matchable events

## Context

The app's single mechanic is "you are exactly as old, in days, as X was when Y happened." An event is
only usable if a person is named in it, that person's birth date is known, and the match is
unambiguous.

Today only **1,232** of the **19,207** scraped Wikipedia "on this day" events
(`data/historical_events_cleaned.json`) reach the app. The cause is a hard gate in
`ingest.pipeline.match_births_to_events`: an event is kept only if a name from
`data/top_1000_births.json` — the top 1,000 Pantheon-ranked people who also appear in the scraped
births data — occurs as a literal substring of the event text.

Measured ceilings for the obvious cheap fixes (both run against the current pipeline):

| Birth-name pool | Unique names | Matched events |
| --- | --- | --- |
| `top_1000_births.json` (current) | 1,000 | 1,232 |
| All 11,341 Pantheon rows matched to a birth record | 3,839 | 1,926 |
| All of `historical_births_cleaned.json`, no fame filter | 39,297 | 3,084 |

So dropping the fame filter entirely — the cheapest possible change — yields 3,084 events and still
leaves ~16,100 events unreached. The remaining gap cannot be closed by re-filtering data already on
disk, because the local births data is itself a single Wikipedia births-page scrape
(`history.muffinlabs.com`, 40,387 records) with no coverage guarantee for the people who actually
appear in event texts.

This spec covers closing that gap. It is scoped strictly to **growing the matched-event pool**. The
decision to keep the app's mechanic pure (exact age match only — no "on this day" browsing mode for
person-less events) is settled and out of scope; events that name no birth-dated person will remain
unused, and that is accepted.

## Relationship to the LLM event enrichment spec

`2026-08-06-llm-event-enrichment-design.md` (spec and plan both written, **neither implemented** —
`src/ingest/enrichment.py` does not exist yet) covers tags, phrasing quality, and subject correction
for events already in the pipeline. This spec composes with it rather than replacing it:

- **Ordering:** matching expansion runs first and grows the pool; enrichment then processes the
  larger pool. Running enrichment first would mean backfilling twice.
- **Shared module:** subject disambiguation here reuses `enrichment.resolve_subject` rather than
  building a parallel mechanism. Because that module does not exist yet, the implementation plan
  derived from this spec must either sequence the enrichment plan's Task covering
  `resolve_subject` first, or create the module as part of this work.
- **Required amendment to the enrichment spec:** its Global Constraints pin subject resolution to
  `data/top_1000_births.json`. Stage 1 below deliberately abandons that file as the gate. When
  `resolve_subject` is implemented it must take the known-person lookup as an injected argument
  rather than hardcoding a path, so both specs share one widened pool. This spec supersedes the
  enrichment spec on that one point.

## Stage 0: replace the matching algorithm

`match_births_to_events` compiles a regex per name and searches every event text against every name —
O(events × names). The full-pool run above took **1,002 seconds** (~17 minutes) at 39,297 names, and
Stage 3 only adds names to the pool.

It also has a correctness bug that matters more as the pool grows: on finding a match it `break`s,
keeping whichever name Python's dict iteration happened to reach first. When an event text names
several known people, the retained "subject" is arbitrary.

**Change:** build one Aho-Corasick automaton (`pyahocorasick`) from all known names, then make a
single pass per event text, finding *every* matching name in time proportional to the text length
rather than the name count. Word-boundary and normalization semantics
(`core.matching.normalize_name`) are preserved — normalize both the names going into the automaton
and the text being scanned, and verify boundaries on each hit so short names cannot match inside
longer words.

`pyahocorasick` is an **ingest-only** dependency. It is added to the ingest requirements, never
imported by `src/app/` or `src/core/db.py`, so the deployed Streamlit app's dependency set is
unchanged.

The multi-candidate case stops being silently resolved and becomes an explicit output: an event with
several matched names is routed to subject disambiguation (Stage 2) instead of guessing.

## Stage 1: widen the local birth pool

Match against all of `historical_births_cleaned.json` instead of `top_1000_births.json`. The Pantheon
fame ranking is no longer a gate; it is retained only as a secondary signal for prioritising review
order, so that the most recognisable people get human attention first.

Expected yield, already measured: **3,084 events** (1,588 unique people).

Losing the fame filter raises false-positive risk — common names, and short names, are likelier to
belong to someone other than the person the event is about. Two mitigations, both cheap and
deterministic:

- The existing plausibility bound (`0 <= age_days <= 120 * 365`) already rejects impossible pairings
  and is kept.
- Single-token names remain excluded (the current `len(name.split()) <= 1` guard), so "John" or
  "Cicero" alone cannot match.

Genuine same-name-different-person collisions are **not** solved here; see Out of scope.

## Stage 2: LLM subject extraction

Input: events Stage 1 left unmatched, plus events where Stage 0 found multiple candidate names.

The subagent's job is narrow and does not rely on its memory of any date: given the event text, name
the person who is the grammatical/semantic subject, **verbatim as the name appears in the text**. It
is not asked for birth dates, and it is not asked to choose from a list it cannot see.

This reuses the existing Haiku-subagent chunk/merge machinery in `src/ingest/llm_utils.py` — the same
pattern already used for phrasing, run from within a Claude Code session rather than a metered API.

The returned name is then resolved in Python, never trusted directly:

1. **Presence check** — the name must actually occur in the event text
   (`core.matching.name_matches_text`), guarding against invention.
2. **Local lookup** — check the name against the Stage 1 widened pool. A hit resolves the event
   offline, with no network call.
3. A name that passes (1) but misses (2) is a genuinely unknown person and falls through to Stage 3.

Both checks are `enrichment.resolve_subject`'s existing responsibility, with the known-person lookup
injected per the amendment noted above.

## Stage 3: Wikidata resolution

Input: candidate names from Stage 2 that are present in the event text but absent from the local
births data. This is the only stage that breaks past the coverage ceiling of the muffinlabs scrape,
and therefore the only one that can close most of the remaining ~16,100-event gap.

New module `src/ingest/sources/wikidata.py`, following the existing one-module-per-source pattern in
`src/ingest/sources/`:

- Resolve a name to candidate entities via Wikidata's `wbsearchentities` API, then read `date of
  birth` (P569) from the chosen entity.
- **Disambiguation:** narrow multiple candidates using context available in the event itself — the
  event year (the person must plausibly have been alive) and occupation/description terms. Candidates
  that cannot be narrowed to exactly one are **not guessed**; they go to the review report.
- **Precision:** only **day-precision** P569 values are usable, since the app computes an age in days.
  Year-, decade-, or century-precision values are recorded as `insufficient_precision` in the review
  report rather than silently dropped — for older figures this is a large and predictable category,
  and it should be visible as a known limit rather than an invisible loss.
- **Caching:** every lookup outcome — resolved, ambiguous, not-found, insufficient-precision — is
  cached in `data/wikidata_persons_cache.json`, keyed by `normalize_name`. Reruns skip names already
  attempted, so a rerun costs no network traffic for anything already seen and recurring subjects are
  queried once.
- Requests are rate-limited and carry a descriptive User-Agent, per Wikidata's API etiquette.

## Quality control

A wrong match is worse than a missing one: it makes the app assert something false in the one
sentence the whole product exists to deliver. So every stage prefers review over inference.

- **Auto-accept** requires an unambiguous subject and a day-precision birth date.
- **Everything else** — rejected subject suggestions, unnarrowable Wikidata candidates,
  insufficient-precision dates, events where no subject could be determined — is written to a single
  shared report, `data/tmp/matching_review.json`. Same shape and spirit as the enrichment spec's
  `enrichment_review.json`: a JSON array whose entries carry enough context to act on manually
  (event text, candidate name(s), stage, issue type).
- Nothing is fabricated to fill a gap, and nothing is discarded without a record.

## Data flow

The existing local-JSON-then-migrate shape is preserved, leaving a reviewable artifact at each step
rather than writing to Supabase mid-pipeline:

```
historical_events.json
  → Stage 0/1  Aho-Corasick match against widened local pool
  → Stage 2    LLM subject extraction (unmatched + multi-candidate events)
  → Stage 3    Wikidata resolution (subjects still unresolved)
  → combined matched set  (+ data/tmp/matching_review.json)
  → LLM enrichment backfill (tags, phrasing — separate spec)
  → migrate_to_supabase.py
```

No UI change is required. Newly matched events flow into the existing calendar mechanic once
migrated, because they have the same shape as the 1,232 already there.

## Testing

- **Aho-Corasick matcher:** multi-candidate detection, no-match, word-boundary correctness (a short
  name must not match inside a longer word), diacritic/punctuation normalization parity with the
  regex implementation it replaces, and the `age_days` plausibility bound.
- **`wikidata.py`:** mocked HTTP throughout, no live network calls in the suite — day-precision
  accepted, coarser precision routed to review, ambiguous candidates routed to review, cache written
  on every outcome and honoured on rerun.
- **Review report:** entries are produced for each failure class rather than dropped.
- Full `pytest` run after implementation.

## Out of scope

- **Any second mechanic for person-less events** (an "on this day" browse mode). Decided against;
  the app stays exact-age-match only.
- **UI changes**, including surfacing the larger event pool differently. New events use the existing
  calendar and dialog unchanged.
- **Same-name-different-person disambiguation** beyond the context heuristics in Stage 3. Two real
  people sharing a name is a known unsolved edge case, already deferred by the enrichment spec;
  unresolvable cases go to review.
- **Tags, phrasing, and `detailed_description`/`wikipedia_url` population** — all owned by the LLM
  event enrichment spec, which runs after this work.
- **Re-scraping or expanding the source event list.** The 19,207 events on disk are the input; this
  spec grows how many of them are *usable*, not how many exist.
