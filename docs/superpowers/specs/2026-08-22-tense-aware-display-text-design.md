# Tense-aware display text, prompt rewrite, and notification link visibility

## Context

The display sentence for a historical-event match currently reads "The same age that {name} was
when {event}." — third person, no direct address, and no distinction between a day the visitor
already passed, today, or a day still ahead of them (the calendar lets a visitor browse any
month, past or future — see `core.sequences`'s module docstring for the same point made about
mathematical anniversaries).

Similarly, `app/ui.py`'s day-dialog header hardcodes "On {date} you are/were {age} old:" as a
literal string — both words are always shown, unconditionally, not actually selected by tense.

Separately, the daily push notification only links back to the app via ntfy's `Click` header,
which is invisible until tapped, and offers no way to reach the app without opening the
notification first.

This spec covers three related changes: making both display texts tense-aware, rewriting the
reword prompt so the LLM stops writing the now-variable opening itself, and making the
notification's app link visible as text, not just a tap target.

## Tense computation

`core/age.py` gains:

```python
Tense = Literal["past", "today", "future"]

def tense_for(day: date, today: date) -> Tense:
    """Whether day is before, on, or after today, from the viewer's perspective."""
    if day < today:
        return "past"
    if day > today:
        return "future"
    return "today"
```

Callers compute this once per rendered day and reuse it for every element on that day (the
dialog header and every event bullet within it), rather than each element re-deriving it from
the same two dates.

## The event sentence: `core.matching.full_sentence`

The tensed opening cannot be baked into the stored `event_phrase` at generation time, because the
same event can be viewed by the same subscriber before, on, or after their match day — the tense
is a property of *when it's rendered*, not of the event itself. So `event_phrase` goes back to
storing only the name-onward clause (e.g. "Sir Richard Owen was when a dinner party was held
inside a life-size model of an iguanodon…"), and the tensed opening is prepended at display time:

```python
TENSE_OPENERS = {"past": "You were", "today": "You're", "future": "You'll be"}

def full_sentence(event: Dict, tense: Tense) -> str:
    return f"{TENSE_OPENERS[tense]} the same age {_phrase_body(event)}"
```

The LLM keeps exactly the freedom it has today over the name-onward clause — title placement
("Sir Richard Owen"), pronoun use, clause restructuring, multi-sentence rewording — it just no
longer writes the leading "The same age that" itself, since that's no longer its call to make.

`full_sentence`'s signature changes from `full_sentence(event)` to `full_sentence(event, tense)`.
Both call sites (`app/ui.py`, `scripts/send_daily_notifications.py`) pass a tense they've already
computed.

### `_phrase_body`: a 3-way legacy normalizer

Because the reprocessing pass (below) runs over the whole corpus manually and isn't atomic, three
`event_phrase` shapes coexist in Supabase for the length of that pass:

1. **New format** (written under the rewritten prompt): starts directly with the name, e.g.
   "Sir Richard Owen was when…". Used as-is.
2. **Current full-sentence format** (written under the existing prompt, `reword_prompt_version =
   1`): starts with the literal "The same age that ". That fixed prefix is stripped off, leaving
   the name-onward remainder.
3. **Oldest suffix-only format** (predates both prompts, `reword_prompt_version = 0` and no
   recognizable opening): reconstructed as `f"{event['name']} was when {phrase}"`, same fallback
   `core.matching.full_sentence` already does today.

This is the same shape of guard the current `full_sentence` normalizer already provides for one
legacy format; it now handles one more.

## Day-dialog header

`app/ui.py`'s `show_day_dialog` computes `tense = tense_for(day_date, today)` once, and uses it
for:

- The header verb, from a small local mapping (`{"past": "were", "today": "are", "future": "will
  be"}`) kept next to its one call site rather than centralized, since it's a different shape of
  output from `TENSE_OPENERS` (bare verb vs. full sentence opener) and has only one caller:
  `"On {date} you were/are/will be {age} old:"`.
- Every `full_sentence(event, tense)` call for that day's event bullets.

The top-of-page "You are X years, Y months, Z days old today." blurb is unaffected — it always
describes age as of the real today, so it's inherently present tense with nothing to vary.

## Reword prompt rewrite and full reprocessing

`src/ingest/reword_prompt.md`'s `event_phrase` field description drops the "Begin with exactly
`The same age that`" instruction. The LLM now starts the phrase directly with the person (title
included, as today) and everything else about the rewording rules (fidelity, past tense, one to
three sentences, stripping Wikipedia artifacts) is unchanged. The worked example is updated to
show the new shape — "Sir Richard Owen was when a dinner party…" rather than "The same age that
Sir Richard Owen was when…".

`ingest/enrichment.REWORD_PROMPT_VERSION` bumps `1 → 2`. This is a pure version bump — no new
pipeline code. The existing `mode="phrasing"` machinery in `backfill_event_enrichment.py`
(`pending_phrasing_events`, keyed on `reword_prompt_version < REWORD_PROMPT_VERSION`) already
does exactly what's needed: every row currently at version 1 becomes pending, and the same
manual sequence documented in `SUPABASE_SETUP.md` (prepare_chunks → dispatch a Haiku subagent per
chunk → merge_chunk → review `enrichment_review.json`) reprocesses the full corpus (~1200+ rows)
under the new prompt.

`ingest/enrichment.check_phrase_format` is updated to match the new shape: no fixed opening to
check for; it now requires the phrase to contain `" was when "`, the span before that hinge to
contain the event's `name` (via `normalize_name`, so a title prefix still passes), and the phrase
to end in terminal punctuation. `check_facts_preserved` is unaffected — it doesn't reason about
the opening. `PHRASE_OPENING` in `enrichment.py` was only ever read by `check_phrase_format`'s old
"starts with" branch, so it's removed along with that branch.

`LEGACY_PHRASE_PREFIX` in `matching.py` keeps its current name and value ("The same age that "),
but changes role: it's no longer something a well-formed row is expected to start with — it
becomes purely the string `_phrase_body`'s branch 2 strips off existing rows still in that shape.

`ingest/llm_utils._fallback_event_phrase` — the deterministic template used when a subagent
returns nothing usable during the (separate, still-live) local-JSON ingestion of genuinely new
events — mirrors the same change: it drops "The same age that " and returns
`f"{name} was when {lowered_text}"` instead, so a fallback-generated row is already in the new
shape rather than needing `_phrase_body` to strip it later.

## Notification changes

`scripts/send_daily_notifications.py`:

- **Title** (`_send_ntfy_notification`): unchanged wording, `"You're now the same age {name}
  was"` — the notification always fires same-day, so this is independent of the tense system
  above (there's no ambiguity to resolve).
- **Body**: `full_sentence(event, "today")` — always "today" tense, for the same reason.
- **Visible link**: when `token` and `APP_BASE_URL` are both present, the same URL that already
  goes into the `Click` header (tap-through) is now *also* appended as the last line of the body
  text, so it's readable and copyable without tapping the notification. Both mechanisms point at
  the identical link, computed once.
- **Anniversary notifications** (`_send_anniversary_notification`) get the same visible-link
  treatment for consistency, since they share the same `Click`/link mechanics. `anniversary_sentence`
  itself is untouched — it has no tense verb to change ("Your age in days (X) is …").

## Out of scope

- Mathematical-anniversary sentence wording (`anniversary_sentence`) — no tense verb, nothing to
  change.
- A Wikipedia link in the notification — explicitly declined; the notification's only link is
  back to the app.
- A separate deterministic strip-only pass — superseded by the full LLM reprocess, which is more
  thorough and reuses existing infrastructure unchanged.

## Testing

- `tests/test_age.py`: `tense_for` for all three branches (day before/equal/after today).
- `tests/test_matching.py`: `full_sentence` for all three tense × all three legacy-format
  combinations (9 cases, though several collapse — e.g. tense doesn't interact with which legacy
  branch fires).
- `tests/test_enrichment.py`: `check_phrase_format` updated for the new shape (no opening check;
  hinge + name-before-hinge + terminal punctuation still checked).
- `tests/test_send_daily_notifications.py`: body includes the visible link when token/base URL
  present, omits it when either is missing (mirroring today's `Click`-header conditional); title
  and body text updated for the new wording.
- Full `pytest` run after implementation.
- The reprocessing pass itself is verified the same way the Aug 8 pass was: one chunk through a
  subagent, read against a couple of worked examples, before committing to the full run.
