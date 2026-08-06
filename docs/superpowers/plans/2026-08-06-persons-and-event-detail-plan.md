# Persons Table, Event Detail Fields, and event_phrase Restructuring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `persons` table (name + Wikipedia link), a `detailed_description` field on events, and restructure `display_text` into a stored `event_phrase` suffix with the "The same age that {name} was when " prefix built at display time, per `docs/superpowers/specs/2026-08-06-persons-and-event-detail-design.md`.

**Architecture:** Two new Supabase tables/columns (SQL migration run by hand), a shared `full_sentence()` helper so the UI and notification script build the same text one way, a renamed/simplified fallback-phrase function in the local ingestion pipeline, and a one-off Python backfill script that populates `persons` and splits existing `event_phrase` values — all layered on the existing Supabase-backed app with no changes to its overall shape.

**Tech Stack:** Python 3.11, Streamlit, `supabase-py` (postgrest client), pytest, `unittest.mock`.

## Global Constraints

- `person_id` and `detailed_description` on `events` are nullable/optional — never assume they're populated.
- `persons.wikipedia_url` is the only Wikipedia field; there is no event-level override (per approved design).
- `events.name` stays a denormalized text column — do not remove it or replace direct reads of `event["name"]` with a join.
- No new automated UI tests — `src/app/ui.py` has never had automated tests in this project; verify it manually/in-browser instead.
- The live Supabase migration (running the SQL, running the backfill script, confirming notifications) requires the user's real Supabase project and credentials and must happen together with them — it is not something any task here can complete unattended.

---

### Task 1: Add `full_sentence()` helper to `core/matching.py`

**Files:**
- Modify: `src/core/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Produces: `full_sentence(event: Dict) -> str` — later used by Task 5 (`ui.py`) and Task 6 (`send_daily_notifications.py`). Expects `event["name"]` and `event["event_phrase"]` to be present.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_matching.py` (after the existing imports, add `full_sentence` to the import line):

```python
from core.matching import events_by_age_days, full_sentence, name_matches_text, normalize_name
```

Add this test function anywhere after the existing tests:

```python
def test_full_sentence_combines_name_and_phrase():
    event = {"name": "George Washington", "event_phrase": "he hoisted the flag"}
    assert full_sentence(event) == "The same age that George Washington was when he hoisted the flag"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_matching.py::test_full_sentence_combines_name_and_phrase -v`
Expected: FAIL with `ImportError: cannot import name 'full_sentence'`

- [ ] **Step 3: Write minimal implementation**

In `src/core/matching.py`, add this function (e.g. after `events_by_age_days`):

```python
def full_sentence(event: Dict) -> str:
    """Build the full display sentence from an event's name and event_phrase suffix."""
    return f"The same age that {event['name']} was when {event['event_phrase']}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_matching.py -v`
Expected: all tests PASS (existing tests plus the new one)

- [ ] **Step 5: Commit**

```bash
git add src/core/matching.py tests/test_matching.py
git commit -m "Add full_sentence() helper to build display text from name + event_phrase"
```

---

### Task 2: Rename `display_text` to `event_phrase` (suffix-only) across the local ingestion pipeline

**Files:**
- Modify: `src/ingest/llm_utils.py`
- Modify: `src/ingest/migrate_to_supabase.py`
- Test: `tests/test_llm_utils.py`

**Interfaces:**
- Produces: `_fallback_event_phrase(event: Dict) -> str` (renamed from `_fallback_display_text`, now returns only the lowercased suffix, no "The same age..." prefix).
- `merge_reworded_chunk(...)` now writes an `"event_phrase"` key into `displayable_path` instead of `"display_text"`.
- `migrate_to_supabase._to_event_row` reads `entry["event_phrase"]` and writes it to the `event_phrase` column (matching the Task 4 SQL rename) instead of reading/writing `"display_text"`.

- [ ] **Step 1: Write the failing test**

Replace the full contents of `tests/test_llm_utils.py` with:

```python
import json

from ingest.llm_utils import merge_reworded_chunk


def test_merge_reworded_chunk_uses_result_and_falls_back(tmp_path):
    chunk = [
        {"name": "George Washington", "text": "hoisted the flag", "year": "1776", "month": 1, "day": 1, "age": 100},
        {"name": "Anton Chekhov", "text": "wrote a play", "year": "1904", "month": 1, "day": 17, "age": 200},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Only the first record comes back reworded; the second is missing entirely.
    result = [
        {**chunk[0], "event_phrase": "he hoisted the flag over Prospect Hill"},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(chunk_path, result_path, displayable_path=displayable_path)

    assert merged_count == 2
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_name = {event["name"]: event for event in merged}

    assert by_name["George Washington"]["event_phrase"] == "he hoisted the flag over Prospect Hill"
    assert by_name["Anton Chekhov"]["event_phrase"] == "wrote a play"


def test_merge_reworded_chunk_falls_back_on_missing_result_file(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result_path = tmp_path / "does_not_exist.json"
    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(chunk_path, result_path, displayable_path=displayable_path)

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "published notes"


def test_merge_reworded_chunk_appends_to_existing_file(tmp_path):
    displayable_path = tmp_path / "displayable_events.json"
    displayable_path.write_text(
        json.dumps([{"name": "Existing Person", "text": "did something", "event_phrase": "already here"}]),
        encoding="utf-8",
    )

    chunk = [{"name": "New Person", "text": "did something else", "year": "2000", "month": 1, "day": 1, "age": 10}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
    result_path = tmp_path / "does_not_exist.json"

    merge_reworded_chunk(chunk_path, result_path, displayable_path=displayable_path)

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert len(merged) == 2
    assert {event["name"] for event in merged} == {"Existing Person", "New Person"}
```

(Note: the fallback for "Anton Chekhov" — whose record has no matching result — now asserts `"wrote a play"`, i.e. the raw `text` lowercased-at-the-first-letter with no prefix; since "wrote" already starts lowercase this example doesn't change case, unlike the old assertion which prepended "The same age that Anton Chekhov was when ".)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_utils.py -v`
Expected: FAIL — assertions on `event["event_phrase"]` raise `KeyError` since the current code still writes `"display_text"`.

- [ ] **Step 3: Write minimal implementation**

In `src/ingest/llm_utils.py`, replace the `_fallback_display_text` function:

```python
def _fallback_display_text(event: Dict) -> str:
    """Deterministic display_text used when a subagent can't produce usable output."""
    text = event.get("text", "") or ""
    name = event.get("name", "")
    lowered = text[:1].lower() + text[1:] if text else text
    return f"The same age that {name} was when {lowered}"
```

with:

```python
def _fallback_event_phrase(event: Dict) -> str:
    """Deterministic event_phrase suffix used when a subagent can't produce usable output.

    Returns only the fragment that goes after "The same age that {name} was when " —
    that static prefix is built at display time by core.matching.full_sentence, not stored.
    """
    text = event.get("text", "") or ""
    return text[:1].lower() + text[1:] if text else text
```

Then in `merge_reworded_chunk`, change:

```python
    merged: List[Dict] = []
    for event in chunk:
        result = reworded_by_key.get(_event_key(event))
        if result and result.get("display_text"):
            merged.append(result)
        else:
            merged.append({**event, "display_text": _fallback_display_text(event)})
```

to:

```python
    merged: List[Dict] = []
    for event in chunk:
        result = reworded_by_key.get(_event_key(event))
        if result and result.get("event_phrase"):
            merged.append(result)
        else:
            merged.append({**event, "event_phrase": _fallback_event_phrase(event)})
```

Now update `src/ingest/migrate_to_supabase.py`'s `_to_event_row` — change:

```python
def _to_event_row(entry: Dict) -> Dict:
    return {
        "name": entry["name"],
        "text": entry["text"],
        "display_text": entry["display_text"],
        "year": int(entry["year"]),
        "month": int(entry["month"]),
        "day": int(entry["day"]),
        "age_days": int(entry["age"]),
        "event_type": "achievement",
        "source": "initial_migration",
    }
```

to:

```python
def _to_event_row(entry: Dict) -> Dict:
    return {
        "name": entry["name"],
        "text": entry["text"],
        "event_phrase": entry["event_phrase"],
        "year": int(entry["year"]),
        "month": int(entry["month"]),
        "day": int(entry["day"]),
        "age_days": int(entry["age"]),
        "event_type": "achievement",
        "source": "initial_migration",
    }
```

(This script already ran once for the initial 1232 rows; this change only matters if it's ever run again for a fresh batch of events, and keeps it consistent with the renamed column from Task 4.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_utils.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ingest/llm_utils.py src/ingest/migrate_to_supabase.py tests/test_llm_utils.py
git commit -m "Rename display_text to event_phrase (suffix-only) in local ingestion pipeline"
```

---

### Task 3: SQL migration + `backfill_persons_and_phrases.py` with testable pure helpers

**Files:**
- Modify: `SUPABASE_SETUP.md`
- Create: `src/ingest/backfill_persons_and_phrases.py`
- Test: `tests/test_backfill_persons_and_phrases.py`

**Interfaces:**
- Consumes: `core.db.get_client()` (existing, from `src/core/db.py`).
- Produces: `expected_prefix(name: str) -> str`, `strip_prefix(name: str, event_phrase: str) -> str | None`, `build_person_rows(names: List[str]) -> List[Dict]` — pure functions, unit tested directly. `main()` orchestrates the live Supabase read/write using these and is not unit tested (requires a live project — verified manually in Task 7).

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill_persons_and_phrases.py`:

```python
from ingest.backfill_persons_and_phrases import build_person_rows, expected_prefix, strip_prefix


def test_expected_prefix_uses_name():
    assert expected_prefix("George Washington") == "The same age that George Washington was when "


def test_strip_prefix_returns_suffix_when_prefix_matches():
    full_text = "The same age that George Washington was when he hoisted the flag"
    assert strip_prefix("George Washington", full_text) == "he hoisted the flag"


def test_strip_prefix_returns_none_when_prefix_does_not_match():
    assert strip_prefix("George Washington", "He hoisted the flag as a young man") is None


def test_build_person_rows_dedupes_and_sorts():
    rows = build_person_rows(["Ada Lovelace", "George Washington", "Ada Lovelace"])
    assert rows == [{"name": "Ada Lovelace"}, {"name": "George Washington"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_persons_and_phrases.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.backfill_persons_and_phrases'`

- [ ] **Step 3: Write minimal implementation**

Create `src/ingest/backfill_persons_and_phrases.py`:

```python
"""One-off migration: create persons rows, backfill events.person_id, and split
events.event_phrase (still holding the old full sentences right after the SQL
rename in SUPABASE_SETUP.md) into just the suffix after the static prefix.

Run once, after applying the SQL in SUPABASE_SETUP.md's "Persons and event
detail fields" section:

    python -m ingest.backfill_persons_and_phrases
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.db import get_client


def expected_prefix(name: str) -> str:
    """The static prefix that used to be baked into every display_text value."""
    return f"The same age that {name} was when "


def strip_prefix(name: str, event_phrase: str) -> Optional[str]:
    """Return the suffix if event_phrase starts with the expected prefix for name, else None."""
    prefix = expected_prefix(name)
    if event_phrase.startswith(prefix):
        return event_phrase[len(prefix):]
    return None


def build_person_rows(names: List[str]) -> List[Dict]:
    """One persons row per distinct name, sorted for a deterministic insert order."""
    return [{"name": name} for name in sorted(set(names))]


def main() -> None:
    client = get_client()

    events = client.table("events").select("id, name, event_phrase").execute().data

    # 1. One persons row per distinct name (does not attempt to split real name collisions).
    person_rows = build_person_rows([event["name"] for event in events])
    persons = client.table("persons").upsert(person_rows, on_conflict="name").execute().data
    name_to_person_id = {person["name"]: person["id"] for person in persons}

    # 2 & 3. Backfill person_id, and split event_phrase into just the suffix.
    unstrippable: List[Tuple[int, str]] = []
    for event in events:
        update: Dict = {"person_id": name_to_person_id[event["name"]]}

        suffix = strip_prefix(event["name"], event["event_phrase"])
        if suffix is not None:
            update["event_phrase"] = suffix
        else:
            unstrippable.append((event["id"], event["event_phrase"]))

        client.table("events").update(update).eq("id", event["id"]).execute()

    print(f"Backfilled {len(events)} events across {len(person_rows)} persons.")
    if unstrippable:
        print(f"{len(unstrippable)} rows did not match the expected prefix and were left as full sentences:")
        for event_id, text in unstrippable:
            print(f"  id={event_id}: {text!r}")


if __name__ == "__main__":  # pragma: no cover - manual one-off script
    main()
```

Now add this section to `SUPABASE_SETUP.md`, right after the existing schema's SQL block (before "3. Configure secrets" — renumber the later "Configure secrets"/"Install ntfy"/"Test end-to-end" sections up by one, e.g. "3. Persons and event detail fields" becomes the new step 3, and the old steps 3-5 become 4-6):

````markdown
## 3. Persons and event detail fields

Run this in the SQL editor to add the `persons` table and the new optional event fields:

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

Then run the one-off backfill (same environment/credentials as the original migration):

```bash
python -m ingest.backfill_persons_and_phrases
```

This creates one `persons` row per distinct event name, links every event to its person, and
splits each `event_phrase` value down to just the suffix after "The same age that {name} was
when " (the prefix is now built at display time). Any row whose text didn't match that exact
prefix is printed at the end instead of being silently mangled — check the output for stragglers
and fix them by hand in the Supabase table editor if any show up.

`wikipedia_url` (on `persons`) and `detailed_description` (on `events`) are left empty — fill
them in later, e.g. via the Supabase table editor, as you get to it.
````

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backfill_persons_and_phrases.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add SUPABASE_SETUP.md src/ingest/backfill_persons_and_phrases.py tests/test_backfill_persons_and_phrases.py
git commit -m "Add persons/event-detail SQL migration and backfill script"
```

---

### Task 4: `core/db.py` — join persons data into `fetch_events()`

**Files:**
- Modify: `src/core/db.py`
- Test: `tests/test_db.py` (new file)

**Interfaces:**
- Modifies: `fetch_events() -> List[Dict]` (existing signature unchanged; each returned dict now has a nested `"persons"` key — either `{"wikipedia_url": ...}` or `None` when `person_id` is unset).

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
from unittest.mock import MagicMock, patch

from core.db import fetch_events


def test_fetch_events_selects_with_person_join():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.execute.return_value.data = [
        {"id": 1, "name": "Ada Lovelace", "persons": {"wikipedia_url": "https://en.wikipedia.org/wiki/Ada_Lovelace"}},
        {"id": 2, "name": "Unlinked Person", "persons": None},
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    mock_client.table.assert_called_once_with("events")
    mock_client.table.return_value.select.assert_called_once_with("*, persons(wikipedia_url)")
    assert result[0]["persons"]["wikipedia_url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"
    assert result[1]["persons"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `mock_client.table.return_value.select.assert_called_once_with("*, persons(wikipedia_url)")` raises `AssertionError` since the current code calls `select("*")`.

- [ ] **Step 3: Write minimal implementation**

In `src/core/db.py`, change:

```python
def fetch_events() -> List[Dict]:
    """Return every row from the events table."""
    client = get_client()
    response = client.table("events").select("*").execute()
    return response.data
```

to:

```python
def fetch_events() -> List[Dict]:
    """Return every row from the events table, joined with each event's person data."""
    client = get_client()
    response = client.table("events").select("*, persons(wikipedia_url)").execute()
    return response.data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/db.py tests/test_db.py
git commit -m "Join persons data into fetch_events() for Wikipedia link lookup"
```

---

### Task 5: UI — detailed description, Wikipedia link, and `full_sentence()`

**Files:**
- Modify: `src/app/ui.py`

**Interfaces:**
- Consumes: `full_sentence(event: Dict) -> str` from Task 1 (`core.matching`); `event["persons"]` shape from Task 4 (`core.db.fetch_events`).

- [ ] **Step 1: Update the import and dialog function**

In `src/app/ui.py`, change the import line:

```python
from core.matching import events_by_age_days
```

to:

```python
from core.matching import events_by_age_days, full_sentence
```

Then replace `show_event_dialog`:

```python
@st.dialog("Matching event")
def show_event_dialog(day_date: date, matches: List[Dict]) -> None:
    match_years, match_months, match_days = age_breakdown(birthdate, day_date)
    st.markdown(
        f"On **{day_date.strftime('%B %d, %Y')}** you are/were "
        f"**{match_years} years, {match_months} months, {match_days} days** old:"
    )
    for event in matches:
        st.markdown(f"- {event['display_text']}")
```

with:

```python
@st.dialog("Matching event")
def show_event_dialog(day_date: date, matches: List[Dict]) -> None:
    match_years, match_months, match_days = age_breakdown(birthdate, day_date)
    st.markdown(
        f"On **{day_date.strftime('%B %d, %Y')}** you are/were "
        f"**{match_years} years, {match_months} months, {match_days} days** old:"
    )
    for event in matches:
        st.markdown(f"- {full_sentence(event)}")
        description = event.get("detailed_description") or event.get("text")
        if description:
            st.caption(description)
        person = event.get("persons") or {}
        wikipedia_url = person.get("wikipedia_url")
        if wikipedia_url:
            st.markdown(f"[Read more on Wikipedia]({wikipedia_url})")
```

- [ ] **Step 2: Run the full test suite to confirm no regressions**

Run: `pytest -v`
Expected: all tests PASS (`ui.py` has no automated tests itself, but this confirms nothing else broke)

- [ ] **Step 3: Commit**

```bash
git add src/app/ui.py
git commit -m "Show detailed description and Wikipedia link in the event dialog"
```

**Manual verification (do this together with the user once Task 7's live migration has run — not before, since `full_sentence`/`persons` data won't exist in Supabase until then):**
1. `streamlit run src/app/ui.py`
2. Navigate to a month with a starred match day, click it.
3. Confirm the dialog shows a full, correctly-reconstructed sentence (not a broken half-sentence).
4. Confirm a caption with the raw event text appears below it.
5. If that event's person has a `wikipedia_url` set (won't be true for any row until one is manually added, since Task 3's backfill leaves it null), confirm the "Read more on Wikipedia" link appears; otherwise confirm no broken/empty link renders when it's absent.

---

### Task 6: Notification script — use `full_sentence()`

**Files:**
- Modify: `scripts/send_daily_notifications.py`

**Interfaces:**
- Consumes: `full_sentence(event: Dict) -> str` from Task 1.

- [ ] **Step 1: Update the import and notification body**

In `scripts/send_daily_notifications.py`, change:

```python
from core.db import fetch_all_subscriptions, fetch_events
from core.matching import events_by_age_days
```

to:

```python
from core.db import fetch_all_subscriptions, fetch_events
from core.matching import events_by_age_days, full_sentence
```

Then in `_send_ntfy_notification`, change:

```python
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=event["display_text"].encode("utf-8"),
        headers=headers,
        timeout=10,
    )
```

to:

```python
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=full_sentence(event).encode("utf-8"),
        headers=headers,
        timeout=10,
    )
```

- [ ] **Step 2: Run the full test suite to confirm no regressions**

Run: `pytest -v`
Expected: all tests PASS (this script has no automated tests of its own; this confirms nothing else broke)

- [ ] **Step 3: Commit**

```bash
git add scripts/send_daily_notifications.py
git commit -m "Build notification body with full_sentence() instead of stored display_text"
```

---

### Task 7: Live migration and end-to-end verification (together with the user)

**Files:** none (operational task — running SQL and a script against the real Supabase project)

**Interfaces:** none — this task consumes everything built in Tasks 1-6.

- [ ] **Step 1: Run the full local test suite one more time**

Run: `pytest -v`
Expected: all tests PASS

- [ ] **Step 2: Apply the SQL migration**

In the Supabase SQL editor (the user's project), run the SQL block added to `SUPABASE_SETUP.md` in Task 3 (creates `persons`, adds `person_id`/`detailed_description` to `events`, renames `display_text` to `event_phrase`).

- [ ] **Step 3: Run the backfill script**

With `SUPABASE_URL`/`SUPABASE_KEY` set (same as the original migration):

```bash
python -m ingest.backfill_persons_and_phrases
```

Confirm the printed summary shows 1232 events backfilled and check whether any rows were reported as not matching the expected prefix; if so, fix those specific rows by hand in the Supabase table editor (set `event_phrase` to the correct suffix).

- [ ] **Step 4: Browser walkthrough**

Follow the manual verification steps listed at the end of Task 5.

- [ ] **Step 5: Trigger the notification workflow**

Manually run `.github/workflows/daily_notify.yml` from the GitHub Actions tab, with a subscription whose birthday makes today a match. Confirm the push notification text matches what the dialog shows in Step 4.
