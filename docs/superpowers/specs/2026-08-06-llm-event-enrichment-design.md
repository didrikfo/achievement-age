# LLM event enrichment: tags, subject correction, and phrasing quality

## Context

Event descriptions are reworded for display (`event_phrase`) by spawning Claude Haiku subagents from
within a Claude Code session — see `src/ingest/llm_utils.py`. That module has no network/API-calling
code itself; it prepares chunk files for a subagent to process and merges the result back in. Today's
earlier spec (`2026-08-06-persons-and-event-detail-design.md`) flagged a gap while restructuring
`display_text` into `event_phrase`: "there's no stored prompt template in the repo today (the prompt
is crafted live per batch run)."

[IDEAS.md](../../../IDEAS.md) records three concrete improvements wanted for this LLM processing step:

1. Assign appropriate tags to events (single event may need multiple tags).
2. Check whether the regex-matched name is actually the right subject for an event — some event
   descriptions mention multiple names, and the one the matching pipeline (`core.matching`,
   `ingest.pipeline.match_births_to_events`) happened to match first isn't always the grammatical
   subject.
3. Instruct the LLM to avoid strange-reading output — the generated `event_phrase` should read
   naturally as a sentence continuation.

This spec covers all three, plus fixing the "no stored prompt template" gap along the way.

## Architecture: two entry points, one shared module

Two things need enriching: **new events** not yet migrated to Supabase (today's local-JSON chunk/merge
flow in `llm_utils.py`) and the **~1232 already-migrated events** in Supabase (need a one-off backfill,
like `backfill_persons_and_phrases.py`). There's no active recurring ingestion right now (the last
data load was the one-time historical import), so this spec does not unify both into a single
Supabase-native pipeline — that would rework an already-tested module to solve a problem (live
ingestion) that doesn't exist yet.

Instead, a new shared module, `src/ingest/enrichment.py`, holds the tag taxonomy, prompt-building, and
validation logic once. Both entry points import from it, so the prompt and validation rules can't drift
out of sync between the two paths.

## Tag taxonomy

20 single-word categories, chosen to cover the kinds of "on this day" events in this dataset:

```
military, politics, science, technology, exploration, space, arts, music, film, sports,
religion, royalty, economics, law, disaster, health, social, education, philosophy, engineering
```

- Lives as a Python constant, `TAG_TAXONOMY`, in `src/ingest/enrichment.py`. Both the prompt template
  and the merge-validation step import from this single source of truth.
- Each event gets **1–3 tags** — never 0 (every event fits at least one broad category) and capped at
  3 (keeps tags meaningful for filtering rather than exhaustive).
- Seeded into the `tags` table via a new SQL migration in `SUPABASE_SETUP.md` (name + a placeholder
  color per tag — actual color scheme is a UI concern and out of scope here, since nothing displays
  tags yet).

## Subject-correction logic

The LLM is not trusted to reassign a subject on its own — it doesn't have access to the full births
list, so it can't know whether an alternate name is one we can compute an age for. Its job is narrow:
given the event text and the currently-matched name, say whether that name is really the
grammatical/semantic subject, or whether another *named person appearing in the text* would be more
appropriate. It returns that alternate name verbatim as it appears in the text, or omits/nulls the
field if the current match is fine.

All validation happens in Python, in `enrichment.resolve_subject(suggested_name, event, births_lookup)`,
after the subagent responds:

1. **Presence check** — the suggested name must actually appear in the event text (reuses
   `core.matching.name_matches_text`). Guards against the LLM inventing a name that isn't in the text.
2. **Known-person check** — the suggested name must resolve to an entry in `data/top_1000_births.json`
   (so a real birth date is available to compute an age from). Reuses the same lookup logic as
   `ingest.pipeline._load_birth_lookup`, matching on `core.matching.normalize_name` (casefolded,
   diacritics/punctuation stripped) rather than exact string equality, consistent with how the rest of
   the matching pipeline compares names.
3. **If both pass**: recompute `age_days` against the new person's birth date, upsert a `persons` row
   for them if one doesn't already exist (same upsert-by-name pattern as
   `backfill_persons_and_phrases.py`), and update the event's `name`/`person_id`/`age_days`.
4. **If either check fails**: leave the event's subject untouched, and record it in the review report
   (see below) instead of guessing.

## Prompt template, output schema, and validation

**Prompt template** — a new markdown file, `src/ingest/reword_prompt.md`, with a `{tags}` placeholder.
`enrichment.build_prompt(events)` fills that placeholder from `TAG_TAXONOMY` before handing the
instructions to a subagent, so the prompt text and the taxonomy constant can never disagree about what
tags exist. A markdown file (not a Python string) keeps it easy to read and edit directly, and gives it
version-controlled diff history — fixing the "prompt crafted live per batch run" gap.

**Phrasing-quality rules**, included as explicit instructions in the prompt:

- Must read as a natural continuation of "The same age that {name} was when **\_\_\_**."
- Use a pronoun (he/she/they) instead of repeating the name.
- Preserve the original fact — reword, don't embellish or add unstated detail.
- Keep tense consistent with "was when" (past tense).
- Strip Wikipedia-ism artifacts (trailing citation brackets, "(b. ...)" birth-year asides, etc.) if the
  source text has them.

There is deliberately **no code-level check for phrasing quality** — "reads nicely" isn't something
worth writing brittle grammar heuristics for. It's handled by the prompt instructions plus the
human-in-the-loop review that already happens naturally (reviewing a subagent's output before running
the merge). The existing fallback (blank → lowercased original text, in `_fallback_event_phrase`)
still covers the one case code *can* catch: nothing usable came back at all.

**Full output schema per event**, replacing today's `event_phrase`-only shape:

```json
{
  "name": "...",
  "text": "...",
  "event_phrase": "...",
  "tags": ["science", "exploration"],
  "suggested_subject": null
}
```

**Validation in the merge step:**

- `tags`: filtered to exact matches against `TAG_TAXONOMY` (case-insensitive) via
  `enrichment.validate_tags`, keeping the first 3 valid tags in the order the subagent returned them if
  more than 3 survive filtering. If nothing survives filtering, the event gets no tags and an entry in
  the review report ("no valid tags returned").
- `suggested_subject`: handled per `resolve_subject` above; failures also land in the review report.
- Both failure types write to one shared report, `data/tmp/enrichment_review.json` (a JSON array; each
  entry has `event_id`, `issue_type` — `"subject"` or `"tags"` — plus enough detail to act on it
  manually: current name, suggested name, event text, or the raw tags returned). A single file, rather
  than console-only output like `backfill_persons_and_phrases.py`'s `unstrippable` list, so it survives
  after the terminal scrolls away.

## Entry point 1: local-JSON path (new events), `src/ingest/llm_utils.py`

- `prepare_reword_chunks` unchanged.
- Subagent dispatch instructions built via `enrichment.build_prompt` instead of crafted live.
- `merge_reworded_chunk` extended to also run `validate_tags`/`resolve_subject` on each returned
  record, storing the (possibly corrected) `name`/`age`/`tags` fields alongside `event_phrase` in
  `displayable_events.json`.
- `migrate_to_supabase.py` needs a small extension: today it only inserts `events` rows, so tags and
  subject corrections would otherwise be silently dropped at migration time. After each batch insert,
  it will also upsert `persons` rows (by name) and insert the corresponding `event_tags` rows
  (resolving tag names to `tag_id`s via the now-seeded `tags` table).

## Entry point 2: one-off Supabase backfill (existing ~1232 events)

New `src/ingest/backfill_event_enrichment.py`, following `backfill_persons_and_phrases.py`'s shape:

- Fetch all events directly from Supabase (`id, name, text, event_phrase`), paginated like
  `core.db.fetch_events`.
- Chunk fetched events by id for subagent dispatch, reusing `enrichment.build_prompt`.
- "Pending" = events with no rows in `event_tags` yet, so the script is safely resumable/rerunnable
  without reprocessing already-enriched events.
- Merge step (shared validation from `enrichment.py`) updates `events` (`event_phrase`, and
  `name`/`person_id`/`age_days` where a subject correction was resolved) and inserts `event_tags` rows.

`SUPABASE_SETUP.md` gets a new section documenting the tag-seeding SQL and this backfill script, same
as prior migrations were documented.

## Testing

- New `tests/test_enrichment.py`: `validate_tags` (drops unknown tags, caps at 3), `resolve_subject`
  (presence check, known-person check, age recompute, rejection path), prompt-building substitution.
- `tests/test_llm_utils.py` updated for the extended merge schema.
- New `tests/test_backfill_event_enrichment.py`, following `test_backfill_persons_and_phrases.py`'s
  pattern.
- Full `pytest` run after implementation.

## Out of scope (explicitly deferred)

- Populating `detailed_description`/`wikipedia_url` — already deferred in the earlier persons/event-detail
  spec; not part of this one either.
- Any UI display or filtering by tag — tags get stored and queryable, nothing consumes them visually
  yet.
- Automated grading of phrasing quality — handled by prompt instructions + human review only, no code
  attempts to score "reads nicely."
- Detecting genuine same-name-different-person collisions — already an explicitly deferred edge case
  from the persons/event-detail spec; the known-person check here only confirms a name exists in the
  births list, it doesn't disambiguate two different real people who share a name.
