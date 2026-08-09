# `event_phrase` as a full sentence: freer rewording, titles, and a reprocessing pass

## Context

`event_phrase` currently stores only the fragment after "The same age that {name} was when ", with
that prefix reconstructed at display time by `core.matching.full_sentence`. The reword prompt
(`src/ingest/reword_prompt.md`) asks a Haiku subagent for that fragment alone.

Reviewing the output showed a recurring quality problem: when the source `text` has complicated
syntax, the subagent tends to make the *minimal* edit needed to fit the required pattern rather than
genuinely rephrasing. The result reads awkwardly, and titles land in strange places because the
fragment can't touch the name:

| Source `text` | Current `event_phrase` | Problem |
|---|---|---|
| "…created by Benjamin Waterhouse Hawkins and Sir Richard Owen…" | "…created by Benjamin Waterhouse Hawkins and him, a Sir, in south London" | Title stranded mid-sentence |
| "…replaces it with the PNDC led by Flight lieutenant Jerry Rawlings." | "…replaced it with the PNDC he led as Flight lieutenant" | Title forced into an apposition |
| "Battle of Kolín between Prussian forces under Frederick the Great and…" | "at the Battle of Kolín, his Prussian forces faced an Austrian army under…" | Clause-by-clause translation, not a rephrasing |

Two root causes:

1. **The prompt optimizes for fidelity to the original syntax** rather than to the original *meaning*.
   The goal is a description that reads well to an app user, not one that preserves clause structure.
2. **The fragment boundary is in the wrong place.** Anything attached to the person — a title, rank,
   honorific, or epithet — belongs next to their name, but the name sits in a static prefix the LLM
   can't reach.

This spec moves the whole sentence into `event_phrase` so the LLM writes it end to end, loosens the
rewording rules, and defines a reprocessing pass over the existing corpus.

### Evidence gathered while scoping

Measured against the working tree's `data/displayable_events.json` (3341 records):

- **1232 records** carry the pre-restructure `display_text` key (full sentence, no `tags`). These are
  already in Supabase.
- **2109 records** carry suffix-only `event_phrase` and non-empty `tags`. These are **not** yet in
  Supabase.
- The two populations are **disjoint** by `(name, text)` — overlap is 0 — and no record carries
  `original_name`.
- Simulating `backfill_persons_and_phrases.strip_prefix` over the 1232 finds **6 rows** where the
  strip fails, because the phrasing runs "…was when, on his 56th birthday…" — a comma directly after
  "when", so `startswith(prefix + " ")` misses. Those 6 are still stored as full sentences in
  Supabase.
- A prototype of the fact-preservation check (below) flags **16.2%** of the 2109 existing phrases, and
  the flags are dominated by genuine content loss, not noise: dropped Wikipedia topic prefixes
  (`War` ×72, `Civil` ×33, `Revolutionary` ×8) and dropped titles (`King` ×36, `Queen` ×18, `Sir` ×13,
  `Dr` ×10, `General` ×8).

## What `event_phrase` becomes

`event_phrase` stores the **complete display sentence**, including the "The same age that … was when"
opening. The `"was when"` hinge stays fixed — the LLM controls the person's rendering before it and the
event description after it, but never the connective itself. This was a deliberate choice over fully
freeform sentence construction: it rules out some natural-sounding variants, but guarantees the opening
is always grammatical, which a freeform template cannot.

The `events.name` column is **unchanged**: it keeps the bare name with no title. The titled form
("Sir Richard Owen") exists only inside `event_phrase`. No new column stores it — `name` is what the
notification `Title` header (`"You're now as old as {name} was"`) and all name-matching logic read, and
those want the plain name.

## The new prompt (`src/ingest/reword_prompt.md`)

The `event_phrase` field description is replaced with these rules. Everything else in the prompt
(`id`/`name`/`text` passthrough, `tags`, `suggested_subject`) is unchanged.

**Shape**

- Begin with exactly `The same age that `, then the person, then ` was when `, then the event.
- End with a period. May run to more than one sentence after the opening one, if that reads better.

**Naming the person**

- Default to `name` exactly as given.
- If `text` attaches a title, rank, honorific, or established epithet to that person, include it in
  the opening: "Sir Richard Owen", "Flight lieutenant Jerry Rawlings". Never invent one that isn't in
  `text`, and never substitute a different person.
- After the opening, use a pronoun rather than repeating the name. If `text` doesn't establish the
  person's pronouns, use a short form of their name instead — don't guess.

**Rewording freedom (the core change)**

- Reword for how it *reads*, not to stay close to the original wording. Restructure clauses, reorder,
  and split into multiple sentences whenever the original syntax is convoluted. Preserving the source's
  sentence structure is not a goal; preserving its meaning is.
- If `text` doesn't already have `name` as its grammatical subject, restructure so they are — "he led
  the Prussian forces at the Battle of Kolín" rather than a clause-by-clause translation that leaves
  them in a prepositional phrase.

**Fidelity**

- Preserve every concrete fact: other people's names, places, organizations, numbers, and outcomes.
- Weave in any `Topic:` framing prefix (`World War II:`, `Cuban Revolution:`) rather than dropping it.
  This is called out explicitly because it is the single largest source of lost content today.
- Strip Wikipedia artifacts — citation brackets, `(b. …)`/`(d. …)` asides.
- Keep it to roughly one to three sentences. It renders as a bullet in the calendar dialog and as a
  push-notification body.

The rule "don't capitalize the first word" is **removed** — it inverts, since the phrase now opens the
sentence.

## Display-time normalizer (`core.matching.full_sentence`)

`full_sentence` does **not** become an identity function. It becomes a normalizer:

```
if event_phrase already starts with "The same age that" (case-insensitive):
    return it unchanged
else:
    return f"The same age that {name} was when {event_phrase}"   # legacy suffix-only row
```

Both call sites (`app/ui.py:show_event_dialog`, `scripts/send_daily_notifications.py`) keep calling
`full_sentence(event)` with no diff.

This matters because the reprocessing pass is run manually and on the user's own schedule, so the
window between shipping this code and finishing the backfill is open-ended. A pure identity function
would render every not-yet-reprocessed row with no prefix at all ("he hoisted the flag") for that whole
window. The normalizer also stays useful permanently, as a guard against a subagent that ignores the
template.

## Fallback (`llm_utils._fallback_event_phrase`)

Used when a subagent returns nothing usable for a record. It reverts to producing the **full** sentence:

```
f"The same age that {name} was when {lowercased_first_char(text)}"
```

That is, exactly what `full_sentence` used to reconstruct. A degraded but well-formed result: plain
name, no title, original syntax.

## Guardrail checks (new, in `ingest/enrichment.py`)

Two checks, both **advisory** — neither blocks a write. Each appends to the existing review report
(`data/tmp/enrichment_review.json`), the same mechanism the tag and subject checks already use.

**`check_phrase_format(event_phrase, name) -> Optional[str]`** — a structural check:

- starts with `The same age that ` (case-insensitive)
- contains ` was when `
- the span between those two contains `name` under `core.matching.normalize_name` comparison, so a
  title prefix passes but a substituted person does not
- ends with terminal punctuation

Issue type `"format"`.

**`check_facts_preserved(text, event_phrase) -> List[str]`** — a heuristic returning tokens present in
`text` but absent from `event_phrase`:

- extracts capitalized word runs and numerals from `text`
- excludes the sentence-initial word (often capitalized without being a proper noun)
- excludes tokens inside parenthesized asides, since the prompt tells the subagent to strip those
- compares case-insensitively

Issue type `"facts"`, with the missing tokens in the detail.

Note the design change from the initial sketch: this check does **not** exempt the subject's own name
tokens. Under the new format the name and any title belong *in* the sentence, so including them makes
the check strictly better — it catches exactly the title-dropping this spec exists to fix.

Expected volume: the prototype flagged 16.2% of existing phrases, so a full pass over ~3341 rows should
produce roughly 500 `"facts"` entries. That is a triage queue, not a defect count — the check is
deliberately over-sensitive. A flag rate far above that suggests the check needs tuning rather than the
data being bad.

## Schema: `reword_prompt_version`

```sql
alter table events add column reword_prompt_version integer not null default 0;
```

Paired with `enrichment.REWORD_PROMPT_VERSION = 1` (a Python constant, bumped by hand whenever
`reword_prompt.md` changes in a way that could change results). Rows written under the new prompt get
stamped with the current value; everything pre-existing keeps the default `0`.

Pending, for the reprocessing pass, means `reword_prompt_version < REWORD_PROMPT_VERSION`.

This was chosen over the cheaper alternative of treating "already starts with *The same age that*" as
the done-marker, because that rule has a precise blind spot: the 6 comma-phrasing rows identified above
are already stored as full sentences and would be silently skipped — and they are exactly the malformed
ones most in need of reprocessing. A version column also makes future prompt revisions re-runnable
instead of one-shot. The repo already uses this pattern in `subject_extraction.PROMPT_VERSION`.

`llm_utils.merge_reworded_chunk` also writes `reword_prompt_version` into each local JSON record, so
the value survives migration rather than being re-derived.

Note this is a *different* rule from the `full_sentence` normalizer's, deliberately. The normalizer
asks "is this row displayable as written?" — and the 6 comma rows are, since they're already full
sentences. The version column asks "was this row written under the current prompt?", which those 6
were not. Using the prefix for both would conflate the two questions.

## Reprocessing: one mechanism, one population

Rather than building a force-reprocess path into the local JSON flow *and* the Supabase flow, the 2109
un-migrated records are migrated first, then a single phrasing pass runs over everything in Supabase.

`migrate_to_supabase.py` handles the mixed file shape as-is — verified against the working tree:

- `filter_new_entries` keys on `(name, text)` and drops rows already in Supabase, so the 1232 old
  records are skipped even though they're in the same file.
- `_to_event_row` already falls back to `display_text` when `event_phrase` is absent.
- Tag insertion already reads `entry.get("tags") or []`, so the 1232's missing `tags` key is a no-op.

It needs exactly **one** change: `_to_event_row` must carry the version through, as
`"reword_prompt_version": entry.get("reword_prompt_version", 0)`. Without it, a locally-reworded record
stamped with the current version would land in Supabase at the column default of `0` and be needlessly
re-queued by the next phrasing pass. For this particular migration the 2109 records were all written
under the old prompt and correctly resolve to `0` either way, so the change matters for future runs
rather than this one.

**Preflight (new).** One residual risk: if a Supabase-side subject correction ever changed an
`events.name`, that row's local counterpart would no longer key-match and would re-insert as a
duplicate. Add `migrate_to_supabase.report_unmatched_legacy_entries(entries, existing_keys) -> List[Dict]`,
returning local records that have `display_text` but no `event_phrase` and whose `(name, text)` is
absent from Supabase's keys. `main()` prints the count before inserting anything. Expected: **0**.
Anything higher means stop and reconcile by hand rather than migrate.

### `backfill_event_enrichment.py` gains a mode parameter

`prepare_chunks(mode=...)` and `merge_chunk(..., mode=...)`, with `mode="tags"` preserving today's exact
behavior (pending = no `event_tags` rows) and `mode="phrasing"` being the new pass:

- **pending** = `reword_prompt_version < REWORD_PROMPT_VERSION`
- **merge writes only** `event_phrase` and `reword_prompt_version`

In phrasing mode, `suggested_subject` is **recorded in the review report but not applied**. Applying it
would pull age recomputation, `persons` upserts, and `person_id` rewrites into what should be a
single-purpose, easily-reversible pass. The subject errors still get surfaced for a separate decision —
which matters, because the fact-check prototype already caught one: the event "Mary Wollstonecraft
Shelley (anonymously) publishes … *Frankenstein*" is matched to **Mary Wollstonecraft**, the mother, who
died in 1797. Wrong person, wrong age. There are likely more.

### Run sequence (manual, by the user)

1. Run the `alter table` SQL above in the Supabase SQL editor.
2. `python -m ingest.migrate_to_supabase` — prints the preflight count first (confirm it reports 0
   unmatched), then inserts the 2109 at version 0.
4. `prepare_chunks(mode="phrasing")` — ~34 chunk files of 100.
5. Dispatch a Haiku subagent per chunk, prompt from `enrichment.build_prompt()`.
6. `merge_chunk(..., mode="phrasing")` per chunk.
7. Review `data/tmp/enrichment_review.json`.

`SUPABASE_SETUP.md` gets a section documenting this, matching how prior migrations were documented.

## Testing

- `tests/test_matching.py`: `test_full_sentence_combines_name_and_phrase` currently asserts prefix
  reconstruction and must be updated — one case per normalizer branch (legacy suffix gets a prefix, new
  full sentence passes through untouched).
- `tests/test_llm_utils.py`: fallback now asserts the full sentence rather than suffix-only; merge
  asserts `reword_prompt_version` is stamped.
- New tests in `tests/test_enrichment.py` for `check_phrase_format` (each failure branch, plus a titled
  name passing) and `check_facts_preserved` (missing token detected; parenthesized aside not flagged;
  sentence-initial word not flagged).
- `tests/test_backfill_event_enrichment.py`: both modes — `mode="tags"` unchanged, `mode="phrasing"`
  selecting on version and writing only the two fields, with a suggested subject recorded but not
  applied.
- Full `pytest` run after implementation.
- Prompt-quality itself is verified by running one chunk through a subagent and reading the output
  against the three table cases at the top of this spec, before committing to a full pass.

## Out of scope

- **Applying subject corrections during the phrasing pass** — surfaced in the review report only. The
  Mary Wollstonecraft class of bug is a separate piece of work.
- **Fully freeform sentence construction** — the `"was when"` hinge stays fixed, per the decision above.
- **Storing the titled name form** in its own column — it lives only inside `event_phrase`.
- **Automated grading of phrasing quality.** The two checks here are structural and lexical; neither
  scores whether the result reads well. That stays human review, consistent with the prior enrichment
  spec.
- **A force-reprocess mode for the local JSON flow** — obviated by migrating first.
