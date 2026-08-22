# Tense-Aware Display Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the event-match sentence and the day-dialog header tense-aware (past/today/future), rewrite the reword prompt so the LLM stops writing the now-variable opening, and make the notification's app link visible as text, not just a tap target.

**Architecture:** A new `core.age.tense_for(day, today)` classifies a viewed calendar day as `"past" | "today" | "future"`. `core.matching.full_sentence` splits into a tensed opener (computed at render time) plus a normalized name-onward body (`_phrase_body`, which handles three coexisting `event_phrase` shapes during the reprocessing window). `app/ui.py` and `scripts/send_daily_notifications.py` are the two call sites, each computing its own tense. `src/ingest/reword_prompt.md` and `ingest/enrichment.check_phrase_format` are updated to match the new stored shape, gated by a `REWORD_PROMPT_VERSION` bump that makes the existing `mode="phrasing"` backfill machinery re-queue every row with no new pipeline code.

**Tech Stack:** Python 3.11+, Streamlit, Supabase (via `ingest.enrichment`/`backfill_event_enrichment`), pytest.

## Global Constraints

- Full-sentence tense openers: `{"past": "You were", "today": "You're", "future": "You'll be"}`, always followed by `" the same age "` then the phrase body. (Spec: "1. The event sentence".)
- Day-dialog header tense verbs: `{"past": "were", "today": "are", "future": "will be"}`. (Spec: "2. Day-dialog header".)
- Notification title stays exactly `"You're now the same age {name} was"` — independent of the tense system, since notifications always fire same-day. (Spec: "4. Notification changes".)
- Notification body is always rendered at `"today"` tense. (Spec: "4. Notification changes".)
- The notification's visible link is the same URL already used for the `Click` header (`f"{APP_BASE_URL}?u={token}"`), appended as the body's last line separated by a blank line, only when both `token` and `APP_BASE_URL` are present. Applies to both event and anniversary notifications.
- No Wikipedia link in the notification — explicitly out of scope.
- `event_phrase` stores the name-onward clause only (no opening) going forward; `REWORD_PROMPT_VERSION` bumps `1 → 2`.
- `anniversary_sentence` is unchanged — no tense verb to vary.
- Run `./venv/Scripts/python.exe -m pytest <path> -v` for every test command below (Windows venv, per `SUPABASE_SETUP.md`'s existing convention).

---

### Task 1: `core.age.tense_for`

**Files:**
- Modify: `src/core/age.py`
- Test: `tests/test_age.py`

**Interfaces:**
- Produces: `Tense = Literal["past", "today", "future"]`, `tense_for(day: date, today: date) -> Tense`. Both consumed by Task 2 (`core.matching`) and Task 6 (`app/ui.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_age.py` (new import line, then new tests at the end of the file):

```python
from core.age import age_breakdown, days_between_dates, tense_for
```

```python
def test_tense_for_a_day_before_today_is_past():
    assert tense_for(date(2020, 1, 1), date(2020, 6, 1)) == "past"


def test_tense_for_today_is_today():
    assert tense_for(date(2020, 6, 1), date(2020, 6, 1)) == "today"


def test_tense_for_a_day_after_today_is_future():
    assert tense_for(date(2020, 12, 1), date(2020, 6, 1)) == "future"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_age.py -v`
Expected: the three new tests FAIL with `ImportError: cannot import name 'tense_for'`.

- [ ] **Step 3: Implement `tense_for`**

In `src/core/age.py`, add `Literal` to the `typing` import and append the new type and function at the end of the file:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal
```

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_age.py -v`
Expected: all tests PASS (the 4 pre-existing plus the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add src/core/age.py tests/test_age.py
git commit -m "feat: add tense_for for classifying a day as past/today/future"
```

---

### Task 2: `core.matching.full_sentence` becomes tense-aware

**Files:**
- Modify: `src/core/matching.py:97-117`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `core.age.Tense` (Task 1).
- Produces: `full_sentence(event: Dict, tense: Tense) -> str` — signature changed from the current `full_sentence(event)`. Consumed by Task 5 (`scripts/send_daily_notifications.py`) and Task 6 (`app/ui.py`).

- [ ] **Step 1: Write the failing tests**

Replace the three existing `full_sentence` tests in `tests/test_matching.py` (currently `test_full_sentence_prefixes_a_legacy_suffix_only_phrase`, `test_full_sentence_passes_through_a_stored_full_sentence`, `test_full_sentence_passes_through_regardless_of_case_or_leading_space`, lines 63-78) with:

```python
def test_full_sentence_uses_a_new_format_phrase_as_is():
    event = {"name": "Sir Richard Owen", "event_phrase": "Sir Richard Owen was when he unveiled the model."}
    assert full_sentence(event, "today") == "You're the same age Sir Richard Owen was when he unveiled the model."


def test_full_sentence_strips_the_legacy_full_sentence_opening():
    phrase = "The same age that Sir Richard Owen was when a dinner party was held inside an iguanodon."
    event = {"name": "Richard Owen", "event_phrase": phrase}
    assert full_sentence(event, "today") == (
        "You're the same age Sir Richard Owen was when a dinner party was held inside an iguanodon."
    )


def test_full_sentence_strips_the_legacy_opening_regardless_of_case_or_leading_space():
    event = {
        "name": "Ada Lovelace",
        "event_phrase": "  the same age that Ada Lovelace was when she published her notes.",
    }
    assert full_sentence(event, "today") == "You're the same age Ada Lovelace was when she published her notes."


def test_full_sentence_reconstructs_a_legacy_suffix_only_phrase():
    event = {"name": "George Washington", "event_phrase": "he hoisted the flag"}
    assert full_sentence(event, "today") == "You're the same age George Washington was when he hoisted the flag"


def test_full_sentence_uses_the_past_tense_opener():
    event = {"name": "George Washington", "event_phrase": "he hoisted the flag"}
    assert full_sentence(event, "past") == "You were the same age George Washington was when he hoisted the flag"


def test_full_sentence_uses_the_future_tense_opener():
    event = {"name": "George Washington", "event_phrase": "he hoisted the flag"}
    assert full_sentence(event, "future") == "You'll be the same age George Washington was when he hoisted the flag"
```

(Every other test in this file, including the `_event` helper at line 33, is unaffected — none of them call `full_sentence`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_matching.py -v`
Expected: the new `full_sentence` tests FAIL — `full_sentence()` currently takes one argument, so calling it with two raises `TypeError`.

- [ ] **Step 3: Implement the tense split**

In `src/core/matching.py`, add the import and replace lines 97-117 (`LEGACY_PHRASE_PREFIX = "The same age that "` through the end of the current `full_sentence` function):

```python
from core.age import Tense
from core.config import TAG_CATEGORIES, TAG_TAXONOMY
```

(`Tense` added alongside the existing `core.config` import near the top of the file.)

```python
LEGACY_PHRASE_PREFIX = "The same age that "
PHRASE_HINGE = " was when "

TENSE_OPENERS = {"past": "You were", "today": "You're", "future": "You'll be"}


def _phrase_body(event: Dict) -> str:
    """Return event_phrase normalized to its name-onward clause, no tensed opening.

    Three shapes of event_phrase coexist in the database while the reprocessing
    pass (docs/superpowers/specs/2026-08-22-tense-aware-display-text-design.md)
    is still running manually:

    1. New format, already name-onward ("Sir Richard Owen was when ..."): used
       as-is.
    2. Current full-sentence format, opening with LEGACY_PHRASE_PREFIX ("The
       same age that Sir Richard Owen was when ..."): the fixed prefix is
       stripped off.
    3. Oldest suffix-only format (predates both prompts, no recognizable
       opening): reconstructed as "{name} was when {phrase}", the same
       fallback this module has always used for pre-2026-08-08 rows.
    """
    phrase = event["event_phrase"]
    stripped = phrase.lstrip()
    if stripped.lower().startswith(LEGACY_PHRASE_PREFIX.lower()):
        return stripped[len(LEGACY_PHRASE_PREFIX):]

    hinge_at = phrase.lower().find(PHRASE_HINGE)
    if hinge_at != -1 and normalize_name(event["name"]) in normalize_name(phrase[:hinge_at]):
        return phrase

    return f"{event['name']}{PHRASE_HINGE}{phrase}"


def full_sentence(event: Dict, tense: Tense) -> str:
    """Return the event's tensed display sentence.

    tense is the caller's own comparison of the viewed day to the real today
    (core.age.tense_for) - not recomputed here, since a caller rendering
    several events for the same day should compute it once and reuse it.
    """
    return f"{TENSE_OPENERS[tense]} the same age {_phrase_body(event)}"
```

Note `normalize_name` is defined later in this same file (it's used here before its definition line, which is fine at call time since both are module-level functions resolved when `full_sentence`/`_phrase_body` actually run, not when they're defined).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_matching.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/matching.py tests/test_matching.py
git commit -m "feat: make full_sentence tense-aware, split opening from the stored phrase"
```

---

### Task 3: Reword prompt rewrite, `REWORD_PROMPT_VERSION` bump, `check_phrase_format` update

**Files:**
- Modify: `src/ingest/reword_prompt.md:17-20,50-58`
- Modify: `src/ingest/enrichment.py:27,58-93`
- Modify: `SUPABASE_SETUP.md:280-285` (documentation only, no test)
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Produces: `ingest.enrichment.REWORD_PROMPT_VERSION == 2`, `check_phrase_format(event_phrase, name) -> Optional[str]` with the same signature but a new set of rejection reasons (no "does not start with" branch). Consumed by Task 4 (`ingest/llm_utils.py`, unchanged call site) and by `backfill_event_enrichment.py` (unchanged call site — its `REWORD_PROMPT_VERSION` import picks up the new value automatically).

- [ ] **Step 1: Write the failing tests**

In `tests/test_enrichment.py`, replace the `check_phrase_format` tests (lines 122-157) with:

```python
def test_check_phrase_format_accepts_a_well_formed_phrase():
    phrase = "Jerry Rawlings was when a coup d'état removed the PNP government."
    assert check_phrase_format(phrase, "Jerry Rawlings") is None


def test_check_phrase_format_accepts_a_title_in_front_of_the_name():
    # The whole point of the rewrite: titles move next to the name, and the bare
    # name in the DB still has to be recognisable before the hinge.
    phrase = "Flight lieutenant Jerry Rawlings was when a coup d'état removed the PNP government."
    assert check_phrase_format(phrase, "Jerry Rawlings") is None


def test_check_phrase_format_rejects_a_phrase_with_no_hinge_at_all():
    reason = check_phrase_format("he hoisted the flag.", "George Washington")
    assert reason is not None
    assert "was when" in reason


def test_check_phrase_format_rejects_a_phrase_missing_the_hinge():
    reason = check_phrase_format("George Washington hoisted the flag.", "George Washington")
    assert reason is not None
    assert "was when" in reason


def test_check_phrase_format_rejects_a_substituted_person():
    phrase = "Benjamin Franklin was when he hoisted the flag."
    reason = check_phrase_format(phrase, "George Washington")
    assert reason is not None
    assert "George Washington" in reason


def test_check_phrase_format_rejects_a_missing_terminal_period():
    phrase = "George Washington was when he hoisted the flag"
    reason = check_phrase_format(phrase, "George Washington")
    assert reason is not None
    assert "punctuation" in reason
```

Leave `test_check_facts_preserved_*` (lines 160-204) untouched — `check_facts_preserved` doesn't reason about the opening, so those fixtures are unaffected either way.

Replace `test_reword_prompt_version_is_one` (lines 207-210) with:

```python
def test_reword_prompt_version_is_two():
    from ingest.enrichment import REWORD_PROMPT_VERSION

    assert REWORD_PROMPT_VERSION == 2
```

Replace `test_build_prompt_asks_for_a_full_sentence_with_titles` (lines 213-223) with:

```python
def test_build_prompt_asks_for_a_name_onward_phrase_with_titles():
    prompt = build_prompt()
    # The hinge, the freedom to restructure, and title placement are the three
    # things this rewrite exists to convey; the old fixed opening must be gone.
    assert "was when" in prompt
    assert "title, rank, honorific" in prompt
    assert "Preserving the source's sentence structure is not a goal" in prompt
    assert "Don't capitalize the first word" not in prompt
    assert "The same age that" not in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_enrichment.py -v`
Expected: FAIL — `check_phrase_format` still requires the old opening, `REWORD_PROMPT_VERSION` is still `1`, and `reword_prompt.md` still contains "The same age that".

- [ ] **Step 3: Rewrite `reword_prompt.md`**

In `src/ingest/reword_prompt.md`, replace lines 17-20:

```markdown
- `event_phrase`: the **complete display sentence** shown to the app user. It always follows this
  template, with the `was when` hinge never varied:

  > The same age that **{person}** was when **{event}**.
```

with:

```markdown
- `event_phrase`: the sentence shown to the app user, written from the person onward. It always
  follows this template, with the `was when` hinge never varied:

  > **{person}** was when **{event}**.

  The app prepends a tensed opening in front of this ("You were the same age", "You're the same
  age", "You'll be the same age", depending on whether the reader is viewing a day before, on, or
  after today) — that part is computed by the app, not written by you. Begin your output directly
  with the person.
```

Then replace the worked example (lines 50-58):

```markdown
  Worked example — for `name` "Richard Owen" and `text` "A dinner party is held inside a life-size
  model of an iguanodon created by Benjamin Waterhouse Hawkins and Sir Richard Owen in south London,
  England.":

  > The same age that Sir Richard Owen was when a dinner party was held inside a life-size model of an
  > iguanodon, which he had created with Benjamin Waterhouse Hawkins, in south London, England.

  Note the title moved up next to the name, and the model's creation was recast so he is the one doing
  it. `name` in your output stays the bare "Richard Owen" — titles belong only in `event_phrase`.
```

with:

```markdown
  Worked example — for `name` "Richard Owen" and `text` "A dinner party is held inside a life-size
  model of an iguanodon created by Benjamin Waterhouse Hawkins and Sir Richard Owen in south London,
  England.":

  > Sir Richard Owen was when a dinner party was held inside a life-size model of an iguanodon, which
  > he had created with Benjamin Waterhouse Hawkins, in south London, England.

  Note the title moved up next to the name, and the model's creation was recast so he is the one doing
  it. `name` in your output stays the bare "Richard Owen" — titles belong only in `event_phrase`.
```

- [ ] **Step 4: Update `enrichment.py`**

Change line 27:

```python
REWORD_PROMPT_VERSION = 1
```

to:

```python
REWORD_PROMPT_VERSION = 2
```

Replace lines 58-93 (`PHRASE_OPENING = "The same age that "` through the end of `check_phrase_format`):

```python
PHRASE_OPENING = "The same age that "
PHRASE_HINGE = " was when "

_PARENTHESISED = re.compile(r"\([^)]*\)")
_PROPER_NOUN = re.compile(r"\b[A-Z][^\W\d_]*\b")
_NUMERAL = re.compile(r"\b\d[\d,]*\b")


def check_phrase_format(event_phrase: str, name: str) -> Optional[str]:
    """Structural check on a full-sentence event_phrase. Advisory - never blocks a write.

    Returns a rejection reason, or None when the phrase is well formed:
    opens with PHRASE_OPENING, contains PHRASE_HINGE, names `name` between the
    two (so a title prefix passes but a substituted person doesn't), and ends
    with terminal punctuation.
    """
    phrase = (event_phrase or "").strip()
    if not phrase:
        return "phrase is empty"

    lowered = phrase.lower()
    if not lowered.startswith(PHRASE_OPENING.lower()):
        return f"phrase does not start with {PHRASE_OPENING.strip()!r}"

    hinge_at = lowered.find(PHRASE_HINGE.lower())
    if hinge_at == -1:
        return f"phrase does not contain {PHRASE_HINGE.strip()!r}"

    subject_span = phrase[len(PHRASE_OPENING) : hinge_at]
    if normalize_name(name) not in normalize_name(subject_span):
        return f"opening names {subject_span!r}, expected it to contain {name!r}"

    if phrase[-1] not in ".!?":
        return "phrase does not end with terminal punctuation"

    return None
```

with:

```python
PHRASE_HINGE = " was when "

_PARENTHESISED = re.compile(r"\([^)]*\)")
_PROPER_NOUN = re.compile(r"\b[A-Z][^\W\d_]*\b")
_NUMERAL = re.compile(r"\b\d[\d,]*\b")


def check_phrase_format(event_phrase: str, name: str) -> Optional[str]:
    """Structural check on an event_phrase. Advisory - never blocks a write.

    Returns a rejection reason, or None when the phrase is well formed:
    contains PHRASE_HINGE, names `name` before it (so a title prefix passes
    but a substituted person doesn't), and ends with terminal punctuation.
    There's no fixed opening to check for - the phrase starts directly with
    the person, whatever their name or title.
    """
    phrase = (event_phrase or "").strip()
    if not phrase:
        return "phrase is empty"

    lowered = phrase.lower()
    hinge_at = lowered.find(PHRASE_HINGE.lower())
    if hinge_at == -1:
        return f"phrase does not contain {PHRASE_HINGE.strip()!r}"

    subject_span = phrase[:hinge_at]
    if normalize_name(name) not in normalize_name(subject_span):
        return f"opening names {subject_span!r}, expected it to contain {name!r}"

    if phrase[-1] not in ".!?":
        return "phrase does not end with terminal punctuation"

    return None
```

(`PHRASE_OPENING` is removed — it was only read by the branch just deleted.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_enrichment.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Update `SUPABASE_SETUP.md` for accuracy**

Replace lines 280-285:

```markdown
`event_phrase` now stores the **complete** display sentence ("The same age that Sir Richard Owen was
when …"), not just the fragment after "…was when ". The reword subagent writes the whole sentence so
it can place a title next to the name; `events.name` still holds the bare name.

`core.matching.full_sentence` prefixes anything that doesn't already open with "The same age that",
so rows in the older suffix-only format keep displaying correctly until the backfill below has run.
```

with:

```markdown
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
```

- [ ] **Step 7: Commit**

```bash
git add src/ingest/reword_prompt.md src/ingest/enrichment.py tests/test_enrichment.py SUPABASE_SETUP.md
git commit -m "feat: rewrite reword prompt for a name-onward phrase, bump REWORD_PROMPT_VERSION to 2"
```

---

### Task 4: `ingest.llm_utils._fallback_event_phrase` matches the new shape

**Files:**
- Modify: `src/ingest/llm_utils.py:51-64`
- Test: `tests/test_llm_utils.py`

**Interfaces:**
- Consumes: nothing new (no signature change, same call site inside `merge_reworded_chunk`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_llm_utils.py`, change line 44:

```python
    assert by_name["Anton Chekhov"]["event_phrase"] == "The same age that Anton Chekhov was when wrote a play"
```

to:

```python
    assert by_name["Anton Chekhov"]["event_phrase"] == "Anton Chekhov was when wrote a play"
```

And change line 66:

```python
    assert merged[0]["event_phrase"] == "The same age that Ada Lovelace was when published notes"
```

to:

```python
    assert merged[0]["event_phrase"] == "Ada Lovelace was when published notes"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_llm_utils.py -v`
Expected: `test_merge_reworded_chunk_uses_result_and_falls_back` and `test_merge_reworded_chunk_falls_back_on_missing_result_file` FAIL — the fallback still produces the old "The same age that " shape.

- [ ] **Step 3: Update `_fallback_event_phrase`**

In `src/ingest/llm_utils.py`, replace lines 51-64:

```python
def _fallback_event_phrase(event: Dict) -> str:
    """Deterministic full sentence used when a subagent can't produce usable output.

    Mirrors exactly what core.matching.full_sentence used to reconstruct: the
    plain name (never a title - we have no source for one without the LLM) plus
    the original text, lowercased at the join. Degraded but well-formed.

    Records built this way are deliberately NOT stamped with
    REWORD_PROMPT_VERSION by the caller, so the phrasing backfill re-queues
    them later.
    """
    text = event.get("text", "") or ""
    lowered = text[:1].lower() + text[1:] if text else text
    return f"The same age that {event.get('name', '')} was when {lowered}"
```

with:

```python
def _fallback_event_phrase(event: Dict) -> str:
    """Deterministic name-onward phrase used when a subagent can't produce usable output.

    Mirrors exactly what core.matching.full_sentence's normalizer reconstructs
    for the oldest suffix-only rows: the plain name (never a title - we have no
    source for one without the LLM) plus the original text, lowercased at the
    join. Degraded but well-formed, and already in the current shape - no
    tensed opening, since that's computed at display time, not stored.

    Records built this way are deliberately NOT stamped with
    REWORD_PROMPT_VERSION by the caller, so the phrasing backfill re-queues
    them later.
    """
    text = event.get("text", "") or ""
    lowered = text[:1].lower() + text[1:] if text else text
    return f"{event.get('name', '')} was when {lowered}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_llm_utils.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ingest/llm_utils.py tests/test_llm_utils.py
git commit -m "fix: drop the fixed opening from the reword fallback phrase"
```

---

### Task 5: Notification title, tensed body, and visible link

**Files:**
- Modify: `scripts/send_daily_notifications.py:23-88`
- Test: `tests/test_send_daily_notifications.py`

**Interfaces:**
- Consumes: `core.matching.full_sentence(event, tense)` (Task 2).
- Produces: no new public interface — `_send_ntfy_notification`/`_send_anniversary_notification` keep their existing signatures; `main()` is unaffected.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_send_daily_notifications.py` (after the existing imports, `MagicMock` isn't needed — `patch.object` is already imported):

```python
def test_send_ntfy_notification_uses_the_same_age_title():
    event = {"name": "George Washington", "event_phrase": "x"}
    with patch.object(notify, "APP_BASE_URL", ""), \
         patch.object(notify.requests, "post") as post:
        notify._send_ntfy_notification("topic", event, "")

    kwargs = post.call_args.kwargs
    assert kwargs["headers"]["Title"] == "You're now the same age George Washington was".encode("utf-8")


def test_send_ntfy_notification_appends_a_visible_link_when_token_and_base_url_present():
    event = {"name": "George Washington", "event_phrase": "George Washington was when he hoisted the flag."}
    with patch.object(notify, "APP_BASE_URL", "https://almanac-of-you.streamlit.app"), \
         patch.object(notify.requests, "post") as post:
        notify._send_ntfy_notification("topic", event, "tok123")

    kwargs = post.call_args.kwargs
    body = kwargs["data"].decode("utf-8")
    link = "https://almanac-of-you.streamlit.app?u=tok123"
    assert body == f"You're the same age George Washington was when he hoisted the flag.\n\n{link}"
    assert kwargs["headers"]["Click"] == link


def test_send_ntfy_notification_omits_the_link_when_token_is_missing():
    event = {"name": "George Washington", "event_phrase": "George Washington was when he hoisted the flag."}
    with patch.object(notify, "APP_BASE_URL", "https://almanac-of-you.streamlit.app"), \
         patch.object(notify.requests, "post") as post:
        notify._send_ntfy_notification("topic", event, "")

    kwargs = post.call_args.kwargs
    body = kwargs["data"].decode("utf-8")
    assert body == "You're the same age George Washington was when he hoisted the flag."
    assert "Click" not in kwargs["headers"]


def test_send_anniversary_notification_appends_a_visible_link():
    anniversary = {"sequence": "Powers of 2", "age_days": 2048, "description": "a power of two, 2¹¹"}
    with patch.object(notify, "APP_BASE_URL", "https://almanac-of-you.streamlit.app"), \
         patch.object(notify.requests, "post") as post:
        notify._send_anniversary_notification("topic", anniversary, "tok123")

    kwargs = post.call_args.kwargs
    body = kwargs["data"].decode("utf-8")
    link = "https://almanac-of-you.streamlit.app?u=tok123"
    assert body == f"Your age in days (2,048) is a power of two, 2¹¹.\n\n{link}"
    assert kwargs["headers"]["Click"] == link
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_send_daily_notifications.py -v`
Expected: the four new tests FAIL — the title still says "You're now as old as", `full_sentence` is called with one argument (`TypeError`), and there's no visible link in the body.

- [ ] **Step 3: Implement the notification changes**

In `scripts/send_daily_notifications.py`, add `Optional` to the `typing` import (line 27):

```python
from typing import Callable, Dict, List, Optional
```

Replace lines 61-88 (`_send_ntfy_notification` and `_send_anniversary_notification`):

```python
def _send_ntfy_notification(topic: str, event: Dict, token: str) -> None:
    headers = {"Title": f"You're now the same age {event['name']} was".encode("utf-8")}
    link = _subscriber_link(token)
    body = full_sentence(event, "today")
    if link:
        headers["Click"] = link
        body = f"{body}\n\n{link}"
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers=headers,
        timeout=10,
    )


def _send_anniversary_notification(topic: str, anniversary: Dict, token: str) -> None:
    """Push one mathematical anniversary.

    A sibling of _send_ntfy_notification rather than a branch inside it: that
    function builds its title from event['name'], and an anniversary has no
    name, no person and no date - only a number and what's interesting about it.
    """
    headers = {"Title": "You've hit a mathematical anniversary".encode("utf-8")}
    link = _subscriber_link(token)
    body = anniversary_sentence(anniversary)
    if link:
        headers["Click"] = link
        body = f"{body}\n\n{link}"
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers=headers,
        timeout=10,
    )
```

And add `_subscriber_link` just above `_send_ntfy_notification` (replacing the old line 61 insertion point, i.e. immediately after `APP_BASE_URL`/`Matcher` are defined and before the two send functions):

```python
def _subscriber_link(token: str) -> Optional[str]:
    """The subscriber's app link (Click header target and visible body text), or
    None when there's nothing to build one from - matches the existing
    Click-header conditional, now shared by two call sites and by body text.
    """
    if token and APP_BASE_URL:
        return f"{APP_BASE_URL}?u={token}"
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_send_daily_notifications.py -v`
Expected: all tests PASS, including the pre-existing selection-logic tests (`_send_ntfy_notification`/`_send_anniversary_notification` stay patched out there, so their new internals don't affect those tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/send_daily_notifications.py tests/test_send_daily_notifications.py
git commit -m "feat: tensed notification body and a visible app link alongside the tap target"
```

---

### Task 6: Day-dialog header and event sentences become tense-aware

**Files:**
- Modify: `src/app/ui.py:19,114-143`
- Manual verification only — no `tests/test_ui.py` exists in this repo (Streamlit UI is verified in-browser, consistent with existing convention).

**Interfaces:**
- Consumes: `core.age.tense_for` (Task 1), `core.matching.full_sentence(event, tense)` (Task 2).

- [ ] **Step 1: Update the import**

In `src/app/ui.py`, change line 19:

```python
from core.age import age_breakdown
```

to:

```python
from core.age import age_breakdown, tense_for
```

- [ ] **Step 2: Add the header verb mapping and thread tense through `show_day_dialog`**

Replace lines 114-143:

```python
@st.dialog("This day")
def show_day_dialog(day_date: date, events: List[Dict], anniversaries: List[Dict]) -> None:
    match_years, match_months, match_days = age_breakdown(birthdate, day_date)
    st.markdown(
        f"On **{day_date.strftime('%B %d, %Y')}** you are/were "
        f"**{match_years} years, {match_months} months, {match_days} days** old:"
    )
    # A scrolling box only past a few entries - day 1 alone carries seven
    # anniversaries, but a one-match dialog shouldn't sit in a mostly-empty box.
    body = st.container(height=340) if len(events) + len(anniversaries) > 3 else st.container()
    with body:
        # Kept in separate sections, never interleaved: a sentence about Ada
        # Lovelace and a sentence about the number 2,048 have nothing to do with
        # each other beyond landing on the same date.
        if events:
            st.markdown("**Historical matches**")
            for event in events:
                event_date = date(int(event["year"]), int(event["month"]), int(event["day"]))
                st.markdown(f"- {full_sentence(event)} *({event_date.strftime('%B %d, %Y')})*")
                description = event.get("detailed_description") or event.get("text")
                if description:
                    st.caption(description)
                links = further_reading_links(event)
                if links:
                    joined = " · ".join(f"[{label}]({url})" for label, url in links)
                    st.caption(f"Further reading on Wikipedia: {joined}")
        if anniversaries:
            st.markdown("**Mathematical anniversaries**")
            for anniversary in anniversaries:
                st.markdown(f"- {anniversary_sentence(anniversary)}")
```

with:

```python
DIALOG_TENSE_VERB = {"past": "were", "today": "are", "future": "will be"}


@st.dialog("This day")
def show_day_dialog(day_date: date, events: List[Dict], anniversaries: List[Dict]) -> None:
    tense = tense_for(day_date, date.today())
    match_years, match_months, match_days = age_breakdown(birthdate, day_date)
    st.markdown(
        f"On **{day_date.strftime('%B %d, %Y')}** you {DIALOG_TENSE_VERB[tense]} "
        f"**{match_years} years, {match_months} months, {match_days} days** old:"
    )
    # A scrolling box only past a few entries - day 1 alone carries seven
    # anniversaries, but a one-match dialog shouldn't sit in a mostly-empty box.
    body = st.container(height=340) if len(events) + len(anniversaries) > 3 else st.container()
    with body:
        # Kept in separate sections, never interleaved: a sentence about Ada
        # Lovelace and a sentence about the number 2,048 have nothing to do with
        # each other beyond landing on the same date.
        if events:
            st.markdown("**Historical matches**")
            for event in events:
                event_date = date(int(event["year"]), int(event["month"]), int(event["day"]))
                st.markdown(f"- {full_sentence(event, tense)} *({event_date.strftime('%B %d, %Y')})*")
                description = event.get("detailed_description") or event.get("text")
                if description:
                    st.caption(description)
                links = further_reading_links(event)
                if links:
                    joined = " · ".join(f"[{label}]({url})" for label, url in links)
                    st.caption(f"Further reading on Wikipedia: {joined}")
        if anniversaries:
            st.markdown("**Mathematical anniversaries**")
            for anniversary in anniversaries:
                st.markdown(f"- {anniversary_sentence(anniversary)}")
```

- [ ] **Step 3: Verify in the browser**

Start the app (from the main checkout, not a worktree — the Browser pane's `preview_start {name}` mode always launches from the main checkout regardless of working directory, per prior experience with this repo):

```bash
./venv/Scripts/streamlit.exe run src/app/ui.py --server.port 8517
```

Open `http://localhost:8517` in the Browser pane. Enter a birthday far enough in the past that both past and future match days exist in the visible calendar range (e.g. `2000-01-01`). Navigate to a month containing at least one marked day before today and, if the corpus has one, a day after today (browse forward past the current month — the app allows arbitrary future navigation). For each:

1. Click a **past** marked day. Confirm the header reads "...you were **X years...** old:" and each event bullet starts with "You were the same age...".
2. Click **today** if it's marked (or temporarily use a birthday that makes today a match). Confirm "...you are **X years...** old:" and "You're the same age...".
3. Click a **future** marked day. Confirm "...you will be **X years...** old:" and "You'll be the same age...".

Take a screenshot of at least one dialog showing the new wording.

- [ ] **Step 4: Commit**

```bash
git add src/app/ui.py
git commit -m "feat: tense-aware day-dialog header and event sentences"
```

---

### Task 7: Full reprocessing pass over the live corpus (operational, not code)

**Files:** none (Supabase data only — `data/` is gitignored, no commit at the end of this task).

**Interfaces:**
- Consumes: `ingest.backfill_event_enrichment.prepare_chunks(mode="phrasing")`, `ingest.enrichment.build_prompt()`, `ingest.backfill_event_enrichment.merge_chunk(chunk_path, result_path, mode="phrasing")` — all unchanged in signature; their behavior now reflects Tasks 1-4's version bump and prompt rewrite.

This task writes to the **live production Supabase database** through many LLM calls (one Haiku subagent per ~100-row chunk, roughly a dozen for the current ~1200+-row corpus). Confirm with the user before running it, and run it only after Tasks 1-6 are merged, tested, and — for Task 6 — verified in the browser. This mirrors the run sequence documented in `SUPABASE_SETUP.md` section 9, which stays procedurally accurate (only the prose above it changed in Task 3, Step 6).

- [ ] **Step 1: Confirm with the user**

State that this step reprocesses every event in Supabase through Haiku subagents and overwrites `event_phrase`/`reword_prompt_version` for all of them, and wait for explicit go-ahead before continuing.

- [ ] **Step 2: Prepare chunks**

```bash
./venv/Scripts/python.exe -c "from ingest.backfill_event_enrichment import prepare_chunks; print(len(prepare_chunks(mode='phrasing')))"
```

Every row's `reword_prompt_version` is `1 < REWORD_PROMPT_VERSION (2)`, so this should report the full corpus split into `CHUNK_SIZE`-row (100) chunk files under `data/tmp/enrichment_chunks/`. Report the chunk count to the user.

- [ ] **Step 3: Dispatch one subagent per chunk**

For each `data/tmp/enrichment_chunks/chunk_NNNN.json`, dispatch a Haiku subagent (via the `Agent` tool, `model: "haiku"`) whose prompt is `ingest.enrichment.build_prompt()`'s text plus the chunk file's JSON content, instructing it to write its response as `data/tmp/enrichment_chunks/chunk_NNNN_result.json` (a JSON array, one object per input record, per the prompt's own output-format instructions). This is the same one-subagent-per-chunk pattern used for the original 1232-event `display_text` batch and the Aug 8 phrasing pass.

- [ ] **Step 4: Merge each chunk**

For each chunk, once its `_result.json` exists:

```bash
./venv/Scripts/python.exe -c "from ingest.backfill_event_enrichment import merge_chunk; merge_chunk('data/tmp/enrichment_chunks/chunk_NNNN.json', 'data/tmp/enrichment_chunks/chunk_NNNN_result.json', mode='phrasing')"
```

(substituting the real chunk number for `NNNN`). Each call prints nothing but writes to Supabase and appends to `data/tmp/enrichment_review.json`.

- [ ] **Step 5: Review flagged rows**

```bash
./venv/Scripts/python.exe -c "import json, collections; entries = json.load(open('data/tmp/enrichment_review.json', encoding='utf-8')); print(collections.Counter(e['issue_type'] for e in entries))"
```

Report the counts by `issue_type` to the user (expect `facts` to dominate, per the ~16% rate observed when this check was prototyped — a triage queue, not a defect count). Do not attempt to auto-fix flagged rows as part of this task; that's a separate, human-reviewed decision.

---

## Self-Review

**Spec coverage:**
- Tense computation (`tense_for`) — Task 1.
- Event sentence tense split (`full_sentence`, `_phrase_body`, `TENSE_OPENERS`) — Task 2.
- Day-dialog header tense (`DIALOG_TENSE_VERB`) — Task 6.
- Reword prompt rewrite + `REWORD_PROMPT_VERSION` bump + `check_phrase_format` update — Task 3.
- `llm_utils._fallback_event_phrase` (identified as a spec gap during planning, added to the spec before writing this plan) — Task 4.
- Notification title/body/visible-link, both event and anniversary — Task 5.
- Full reprocessing pass — Task 7.
- Out-of-scope items (Wikipedia link, `anniversary_sentence` wording, deterministic-only strip) — correctly absent from every task above.

**Placeholder scan:** none found — every step has literal code/text, no "TBD"/"handle appropriately".

**Type consistency:** `Tense` (Task 1) is used identically in Task 2's `full_sentence(event: Dict, tense: Tense)` and Task 6's `tense_for(day_date, date.today())` call; `TENSE_OPENERS` keys (`"past"/"today"/"future"`) match `DIALOG_TENSE_VERB`'s keys and `tense_for`'s return values exactly across all three tasks.
