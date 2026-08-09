# Subject attribution refinements: multi-subject, corrections, and commemorative references

## Context

The matching-expansion pipeline (`2026-08-07-matching-expansion-design.md`, implemented and merged) went
live for the first time this session. Running Stage 0/1 against the full dataset, then Stage 2 against a
4-chunk (400-event) sample, surfaced three real quality issues visible in `data/tmp/matching_review.json`
and `data/tmp/wikidata_pending.json` that the original design didn't anticipate:

1. **Multiple legitimate subjects in one event get collapsed to one, and the rest discarded as a
   "conflict."** E.g. `"World War II: The main three leaders of the Allied nations, Winston Churchill,
   Harry S. Truman and Joseph Stalin, meet in the German city of Potsdam..."` — Stage 1 matched Churchill
   (first found), and Stage 2 independently identifying Truman and Stalin as also-real, also-present
   people got each logged as `issue_type: "conflicting_subject"` and dropped, rather than added.

2. **A wrong match from *before this project existed* can't be corrected.** `"The founder of Pakistan,
   Quaid-i-Azam Muhammad Ali Jinnah, joins Sindh-Madrasa-tul-Islam, Karachi."` was matched, by the
   original pre-matching-expansion pipeline, to `"Muhammad Ali"` — not the boxer, but Muhammad Ali of
   Egypt (b. 1769, present in `top_1000_births.json`), a coincidental substring match. The computed age
   (~118 years) is inside the `0..120*365`-day plausibility bound, so nothing ever flagged it. Today,
   the collision-detection fix (matching-expansion final review, finding #1) correctly excludes
   `"muhammad ali"` from the widened pool as a colliding name, so this event reached Stage 2 fresh, which
   correctly identified `"Muhammad Ali Jinnah"`. But the append-time `conflicting_subject` guard (same
   final review, finding #4) — built to catch LLM nondeterminism across reruns — can't distinguish "this
   is a legitimate correction of an old wrong match" from "this is the LLM disagreeing with itself," so it
   blocked the correction and logged it as a conflict instead.

3. **A single, unambiguous, age-plausible match can still be structurally wrong.** The Jinnah case above
   also illustrates this: a person referenced only because something is *named after* them, or because
   the text is describing an *anniversary/memorial* of them, is not "doing something" on that date. The
   `0..120*365` day bound catches the most obviously wrong ages, but a commemorative reference within a
   normal lifespan window (e.g., a 40-year-old-sounding anniversary reference) would sail through
   undetected, and Stage 1 has no LLM to apply judgment — it's pure regex/date arithmetic.

A fourth issue is not a correctness bug but an efficiency one: **~76% of the 400-event sample resolved to
"no subject in text" (306/400)** — genuinely correct outcomes (treaties, battles, disasters — the app's
mechanic is intentionally exact-age-match-only, so text with no age-matchable person is expected to stay
unused; see `2026-08-07-matching-expansion-design.md`'s Context). But `ingest.match_events.run_stage_one`
regenerates `data/tmp/subject_pending.json` fresh on every run by reclassifying the *entire* event set —
so every rerun re-queues all ~16,000 already-checked-and-confirmed-empty events for another Stage 2 LLM
pass, with no memory of having checked them before.

This spec addresses all four, without deleting or altering the source event data
(`data/historical_events.json`) — keeping the door open for a future cross-reference against a different
data source that might find people in currently person-less events.

## Section 1: Stage 1 auto-accepts multiple co-subjects, no LLM needed

`ingest.name_index.find_names_in_text` already returns every non-nested matching name in a text (the
matching-expansion final review's maximal-match fix already ensures two returned names are never one a
substring of the other — that case is a single truncated match, not two people). So when 2+ candidates
survive, they are, by construction, genuinely distinct real people literally named in the text. No LLM
judgment is required to accept them as co-subjects — each one's age is independently computable and
independently checkable against the same `MAX_AGE_DAYS` bound Stage 1 already applies.

**Contract change:** `classify_event`'s `"matched"` status now always carries a **list** of 1+ event
records (previously a single dict). For a single-candidate text this list has one element, unchanged in
substance from today. For a multi-candidate text (previously `"ambiguous"`), it carries one record per
candidate — each independently checked against the plausibility bound, so one candidate can still be
individually rejected even if others in the same text pass. This is a breaking change to an already-
shipped function signature (`src/ingest/match_events.py`); the implementation plan must update
`run_stage_one` and every existing test (`tests/test_match_events.py`) that currently expects a
single-dict `"matched"` payload.

`"ambiguous"` as a status is retired by this change — a text with 2+ locally-known candidates is now
`"matched"` (multiple records) unless Section 2's heuristic redirects it first.

No changes are needed to the reword step (`ingest.llm_utils`/`ingest.enrichment.build_prompt`) — it
already operates per matched-event-record (keyed by `(name, text)`), with `name` given as context, so two
records sharing a `text` but differing in `name` are already reworded independently and correctly (e.g.
Churchill's record naturally gets phrased around his own role, Stalin's around his).

## Section 2: Commemorative/named-after heuristic gates before matching

A new check in `classify_event`, run **before** the single/multi-candidate logic above and before the
`"unusable"`/plausibility checks that follow it: if the normalized event text contains any of a small
fixed set of trigger phrases — `"named after"`, `"in honor of"`, `"in honour of"`, `"anniversary of"`,
`"in memory of"`, `"dedicated to"`, `"commemorat"` (substring, covers commemorate/commemorating/
commemoration) — the event is never auto-accepted, regardless of candidate count. It's queued for Stage 2
with a distinct pending-queue `reason: "possible_reference"` — a new value alongside the existing
`"unmatched"` reason. (Section 1 retires `"ambiguous"` as a pending-queue reason entirely: a multi-candidate
text is now resolved directly by Stage 1 unless this heuristic redirects it first, so the two reasons in
`data/tmp/subject_pending.json` after this spec are just `"unmatched"` and `"possible_reference"`.) The
queue file itself documents why each event needed a second look.

`subject_prompt.md` gets new explicit instructions for this exact pattern: a school, award, ship, or place
*named after* a person, or text describing an *anniversary/memorial* of a person's death or an earlier
event, does not mean that person did anything on the date being described. Return `null` for that person
unless the text separately, genuinely describes a different named person doing something on this date.

This is the fix for the Muhammad-Ali-of-Egypt-at-118 case specifically: re-run today, the "joins
Sindh-Madrasa-tul-Islam" text contains no trigger phrase itself, but this instruction still matters for
the broader class of anniversary/dedication text that Stage 1's plausibility bound alone cannot catch
within a normal lifespan window.

## Section 3: Substring rule replaces the blunt conflict-guard

`append_matched_events` (`src/ingest/match_events.py`), when a new `(name, text)` candidate's `text`
already exists under a different `name`, currently logs an unconditional `"conflicting_subject"` review
entry and drops the new candidate. After Section 1, a `text` can already have *multiple* existing entries
(legitimate co-subjects), so the check is against each existing name attached to that `text`, not just one:

1. Normalize the new candidate's name and every existing name already recorded for that `text`
   (`core.matching.normalize_name`, already used throughout this pipeline).
2. If the new name has a substring relationship (either direction) with **any** existing name for that
   `text`, this is the *same person* as that one existing entry, mis-truncated in one of the two records —
   **not** a different person. Keep whichever of the pair is longer (more complete); if the new candidate
   is longer, it **replaces** that one existing entry (a real removal/update capability
   `append_matched_events` does not have today — this is the substantive implementation addition), leaving
   any *other* existing co-subject entries for the same `text` untouched. If the new candidate is not
   longer, it's rejected and logged to review (`issue_type: "shorter_duplicate"`) rather than silently
   dropped, since the existing entry is presumed already correct but the disagreement is still worth a
   human being able to see.
3. If the new name has no substring relationship with *any* existing name for that `text`, it's a
   genuinely different person — a legitimate additional co-subject. It's appended alongside the existing
   entry/entries, exactly like Section 1's locally-discovered multi-subject case, just discovered later (by
   Stage 2 or Stage 3 instead of Stage 1).

This directly fixes the Jinnah case: `"muhammad ali"` is a substring of `"muhammad ali jinnah"`, so the
fuller name replaces the wrong one the next time this text is reprocessed as a candidate (see Scope
boundary below for what "reprocessed" does and doesn't cover).

## Section 4: Version-tagged no-subject cache

New `data/no_subject_cache.json` (top-level under `data/`, matching `wikidata_persons_cache.json`'s
placement — not under `data/tmp/`, since this is meaningful state meant to persist across runs, not
scratch output), keyed by `text`, storing the `subject_prompt.md` version in effect when that text was
confirmed to have no subject.

New `subject_extraction.PROMPT_VERSION` constant (an integer, bumped by hand whenever `subject_prompt.md`
changes in a way that could plausibly change what the LLM finds — including the Section 2 addition in this
same spec, which bumps it from the implicit baseline to `2`).

`prepare_subject_chunks` filters `data/tmp/subject_pending.json` against this cache before chunking:
an entry is skipped only if its `text` has a cached confirmed-no-subject result **at the current
`PROMPT_VERSION`**. `data/tmp/subject_pending.json` itself stays a complete, unfiltered snapshot of
everything Stage 1 currently can't auto-resolve — the filtering is Stage 2's own optimization, kept out of
Stage 1 to avoid a circular import (`subject_extraction` already imports from `match_events`; the reverse
would be required if the version-aware filtering lived in `run_stage_one` instead).

Only a genuine LLM-returned `null` (`"no_subject"` in `merge_subject_chunk`'s existing outcome vocabulary)
gets cached. A `"rejected"` outcome (the LLM proposed a name that failed validation — e.g. a hallucinated
name not present in the text) is **not** cached, since that's a model error worth a fresh attempt on
rerun, not a confirmed absence of a subject.

**Known residual inefficiency, explicitly not addressed here:** an event that Section 1 now resolves
locally as multi-match, or that Stage 2 resolves as `"matched"`, is still reclassified by Stage 1 on every
rerun (Stage 1 has no memory of Stage 2/3 outcomes). This does not produce wrong data — `append_matched_events`'s
exact-`(name, text)` dedup (already shipped) makes a repeat resolution a no-op — but it does mean Stage 2
may occasionally reprocess a small number of already-correctly-resolved events. Given the much larger
no-subject majority (76% of the sample) is the actual cost driver this spec targets, this residual case is
left as a known, low-severity limitation rather than folded into this spec's scope.

## Scope boundary: no retroactive audit of already-migrated events

This spec's corrections apply the next time a text is reprocessed as a *new* candidate through this
pipeline. It does **not** sweep the original 1,232 events already sitting in `data/displayable_events.json`
and already migrated to Supabase before this session — including the live, currently-wrong Jinnah entry.
Auditing and correcting already-migrated data (which would also need a plan for updating live Supabase
rows, not just local JSON) is deliberately deferred to a separate follow-up effort, not built here.

## Testing

- `tests/test_match_events.py`: update every existing test asserting a single-dict `"matched"` payload to
  the new list-of-records contract. New tests: multi-candidate text yields one record per candidate; one
  candidate in a multi-candidate text can independently fail the plausibility bound while others pass; the
  commemorative-heuristic trigger phrases route to `"unmatched"`-with-`reason: "possible_reference"`
  regardless of candidate count (0, 1, or 2+); a non-triggering text is unaffected.
- New tests for `append_matched_events`'s substring rule: shorter-name replaced by longer (correction);
  longer-name-already-present rejects a new shorter candidate (`issue_type: "shorter_duplicate"`); two
  unrelated names for the same text both persist (multi-subject via correction path, not just Section 1's
  direct-discovery path).
- `tests/test_subject_extraction.py`: new tests for the no-subject cache — a cached text at the current
  `PROMPT_VERSION` is excluded from chunk prep; a cached text at a stale version is included; a
  `"rejected"` outcome does not get cached; cache file round-trips through disk.
- Full `pytest` run after implementation, plus a rerun of the existing `tests/test_funnel_integration.py`
  seam test, extended to cover a multi-subject fixture event end-to-end.

## Out of scope

- **Retroactive audit of the original 1,232 already-migrated events** (see Scope boundary above) —
  explicitly deferred to a separate follow-up.
- **Cross-referencing person-less events against a new data source.** This spec deliberately preserves
  `data/historical_events.json` unmodified and doesn't delete or prune any event, specifically to leave
  this door open, but building it is not part of this spec.
- **Any UI change.** Multi-subject events use the existing calendar/dialog mechanic unchanged — they are
  just more rows in the same shape as every other matched event.
- **Retrying `"rejected"` outcomes automatically.** They remain in `data/tmp/matching_review.json` for
  manual visibility; nothing here builds an automatic retry loop for them.
