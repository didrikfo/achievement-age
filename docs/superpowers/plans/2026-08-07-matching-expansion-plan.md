# Matching Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow the pool of age-matchable events from 1,232 toward the full 19,207 scraped events, via a faster multi-name matcher, a widened birth pool, LLM subject extraction, and Wikidata birth-date resolution.

**Architecture:** A three-stage funnel, each stage handling only what the previous one could not resolve. Stage 0/1 (`ingest.name_index` + `ingest.match_events`) replaces the O(events × names) regex loop with one Aho-Corasick pass over a widened births pool. Stage 2 (`ingest.subject_extraction`) sends still-unresolved events to a Claude Haiku subagent that names the subject verbatim, validated in Python. Stage 3 (`ingest.sources.wikidata` + `ingest.resolve_wikidata`) resolves subjects absent from local data against Wikidata. All stages append to `data/events_with_age.json`; everything downstream of that file already exists and reruns safely.

**Tech Stack:** Python 3.11+, pytest, `pyahocorasick` (new, ingest-only), `requests` (already present), Supabase (`supabase-py`).

## Deferred from the spec

The spec's Stage 1 says the Pantheon fame ranking "is retained only as a secondary signal for
prioritising review order, so that the most recognisable people get human attention first." **This
plan does not implement that.** Doing so would mean loading and joining `data/legacy_pantheon.tsv`
inside the review writer purely to sort a report, for no change in what gets matched. The review
report is written unsorted; if triaging it by hand proves unwieldy in practice, sorting it by HPI is
a small follow-up. Flagging this rather than silently dropping it.

## Prerequisites

- [ ] **The `worktree-llm-event-enrichment` branch must be merged into `dev` before starting.** This plan consumes `src/ingest/enrichment.py` (`load_births_lookup`, `resolve_subject`, `write_review_entries`), which exists **only** on that branch — `git ls-tree --name-only dev src/ingest/` confirms it is absent from `dev`. Verify with `python -c "from ingest.enrichment import resolve_subject"` before Task 1.

## Global Constraints

- **`pyahocorasick` is ingest-only.** `requirements.txt` is installed by Streamlit Community Cloud for the deployed app, so `pyahocorasick` goes in a new `requirements-ingest.txt` instead. No module under `src/app/` or `src/core/` may import `ahocorasick`, directly or transitively. This is why the new matcher lives in `src/ingest/name_index.py` and not in `src/core/matching.py` (which `src/app/ui.py` imports).
- **Plausibility bound:** an accepted match must satisfy `0 <= age_days <= 120 * 365`, the same bound `ingest.pipeline.match_births_to_events` already uses. `MAX_AGE_DAYS = 120 * 365` is defined once in `ingest.match_events` and imported elsewhere.
- **Single-token names are never matchable.** Only names with 2+ tokens after `core.matching.normalize_name` enter the automaton or the births lookup, so "John" or "Cicero" alone cannot match.
- **Matched-event records keep the existing key names** `{year, month, day, text, name, age}` — note `age`, not `age_days`. `ingest.llm_utils.get_pending_events` and `ingest.migrate_to_supabase._to_event_row` both depend on this shape.
- **Nothing is fabricated and nothing is discarded silently.** Every non-auto-acceptable outcome writes an entry to `data/tmp/matching_review.json` via the existing `ingest.enrichment.write_review_entries`. Auto-accept requires an unambiguous subject and a day-precision birth date.
- **No live network calls in the test suite.** Wikidata tests monkeypatch `ingest.sources.wikidata._get_json`.
- **No changes to `src/app/` or `src/core/db.py`.** This plan adds no UI.

---

### Task 1: Aho-Corasick name index

**Files:**
- Create: `src/ingest/name_index.py`
- Create: `requirements-ingest.txt`
- Test: `tests/test_name_index.py`

**Interfaces:**
- Consumes: `core.matching.normalize_name`.
- Produces: `name_index.build_name_index(names: Iterable[str]) -> ahocorasick.Automaton` and `name_index.find_names_in_text(automaton, text: str) -> List[str]`. `find_names_in_text` returns **normalized** names (the same form `core.matching.normalize_name` produces), sorted and de-duplicated.

- [ ] **Step 1: Create the ingest-only requirements file**

```
# requirements-ingest.txt
# Ingest/data-preparation dependencies only. NOT installed by Streamlit
# Community Cloud (which installs requirements.txt), so nothing here may be
# imported by src/app/ or src/core/.
pyahocorasick
```

- [ ] **Step 2: Install it**

Run: `venv\Scripts\python.exe -m pip install -r requirements-ingest.txt`
Expected: `pyahocorasick` installs successfully.

- [ ] **Step 3: Write the failing tests**

```python
# tests/test_name_index.py
from ingest.name_index import build_name_index, find_names_in_text


def test_finds_a_multi_token_name():
    automaton = build_name_index(["George Washington"])
    assert find_names_in_text(automaton, "In 1776 George Washington crossed.") == ["george washington"]


def test_does_not_match_inside_a_longer_word():
    # A plain substring check would wrongly match "Art Ross" inside "Parts Rossiter".
    automaton = build_name_index(["Art Ross"])
    assert find_names_in_text(automaton, "The parts rossiter was replaced.") == []


def test_matches_across_diacritics_and_punctuation():
    automaton = build_name_index(["José O'Brien"])
    assert find_names_in_text(automaton, "A speech by Jose O Brien followed.") == ["jose o brien"]


def test_returns_every_matching_name_sorted():
    automaton = build_name_index(["Albert Einstein", "Marie Curie"])
    found = find_names_in_text(automaton, "Marie Curie wrote to Albert Einstein.")
    assert found == ["albert einstein", "marie curie"]


def test_deduplicates_repeated_names():
    automaton = build_name_index(["Marie Curie"])
    assert find_names_in_text(automaton, "Marie Curie met Marie Curie again.") == ["marie curie"]


def test_empty_index_finds_nothing():
    automaton = build_name_index([])
    assert find_names_in_text(automaton, "Anything at all.") == []


def test_blank_names_are_skipped():
    automaton = build_name_index(["", "   ", "Marie Curie"])
    assert find_names_in_text(automaton, "Marie Curie arrived.") == ["marie curie"]
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_name_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.name_index'`

- [ ] **Step 5: Write the implementation**

```python
# src/ingest/name_index.py
"""Aho-Corasick index for finding every known person name inside an event text.

Replaces the per-name regex loop in ingest.pipeline.match_births_to_events,
which was O(events x names) - ~17 minutes at 39,297 names. One automaton is
built once, then each event text is scanned in time proportional to the text
length rather than the name count.

Ingest-only: `ahocorasick` comes from requirements-ingest.txt, which the
deployed Streamlit app does not install. Nothing under src/app/ or src/core/
may import this module.
"""

from __future__ import annotations

from typing import Iterable, List

import ahocorasick

from core.matching import normalize_name


def build_name_index(names: Iterable[str]) -> ahocorasick.Automaton:
    """Build an automaton over normalized names. Blank/unnormalizable names are skipped."""
    automaton = ahocorasick.Automaton()
    for name in names:
        normalized = normalize_name(name)
        if normalized:
            automaton.add_word(normalized, normalized)
    if len(automaton) > 0:
        automaton.make_automaton()
    return automaton


def _is_whole_word(text: str, start: int, end: int) -> bool:
    """True if text[start:end + 1] is bounded by non-word characters on both sides.

    normalize_name leaves only word characters and single spaces, so checking
    the neighbouring character is enough to stop a short name matching inside
    a longer word.
    """
    before = text[start - 1] if start > 0 else " "
    after = text[end + 1] if end + 1 < len(text) else " "
    return not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")


def find_names_in_text(automaton: ahocorasick.Automaton, text: str) -> List[str]:
    """Return every indexed name occurring as a whole word in text, normalized and sorted."""
    if len(automaton) == 0:
        return []

    normalized_text = normalize_name(text)
    found = set()
    for end_index, name in automaton.iter(normalized_text):
        start_index = end_index - len(name) + 1
        if _is_whole_word(normalized_text, start_index, end_index):
            found.add(name)
    return sorted(found)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_name_index.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add requirements-ingest.txt src/ingest/name_index.py tests/test_name_index.py
git commit -m "feat: add Aho-Corasick name index for event matching"
```

---

### Task 2: Stage 0/1 event classification

**Files:**
- Create: `src/ingest/match_events.py`
- Test: `tests/test_match_events.py`

**Interfaces:**
- Consumes: `name_index.build_name_index`, `name_index.find_names_in_text` (Task 1); `enrichment.load_births_lookup`; `pipeline.calculate_age`.
- Produces:
  - `match_events.MAX_AGE_DAYS: int` (= `120 * 365`)
  - `match_events.WIDENED_BIRTHS_PATH: Path` (= `DATA_DIR / "historical_births_cleaned.json"`)
  - `match_events.MATCHING_REVIEW_PATH: Path` (= `DATA_DIR / "tmp" / "matching_review.json"`)
  - `match_events.load_widened_births_lookup(path=WIDENED_BIRTHS_PATH) -> Dict[str, Dict]` — keyed by `normalize_name`, single-token names removed.
  - `match_events.classify_event(event: Dict, automaton, births_lookup: Dict[str, Dict]) -> Tuple[str, object]` — status is one of `"matched"`, `"ambiguous"`, `"unmatched"`, `"implausible"`, `"unusable"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_match_events.py
from ingest.match_events import classify_event, load_widened_births_lookup
from ingest.name_index import build_name_index

CURIE = {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7}
EINSTEIN = {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}


def _lookup(*people):
    return {
        __import__("core.matching", fromlist=["normalize_name"]).normalize_name(person["name"]): person
        for person in people
    }


def _event(text, year=1905, month=11, day=21):
    return {"year": year, "month": month, "day": day, "text": text}


def test_single_known_name_matches_and_computes_age():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Albert Einstein published a paper."), automaton, lookup)

    assert status == "matched"
    assert payload["name"] == "Albert Einstein"
    # 1879-03-14 to 1905-11-21, verified: (date(1905,11,21) - date(1879,3,14)).days
    assert payload["age"] == 9748
    assert payload["text"] == "Albert Einstein published a paper."


def test_two_known_names_are_ambiguous_not_guessed():
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Marie Curie wrote to Albert Einstein."), automaton, lookup)

    assert status == "ambiguous"
    assert payload == ["albert einstein", "marie curie"]


def test_no_known_name_is_unmatched():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(_event("A treaty was signed in Vienna."), automaton, lookup)

    assert status == "unmatched"


def test_event_before_the_persons_birth_is_implausible():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Albert Einstein appears.", year=1800), automaton, lookup)

    assert status == "implausible"
    assert payload == "Albert Einstein"


def test_non_numeric_year_is_unusable():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(
        {"year": "c. 1300", "month": 1, "day": 1, "text": "Albert Einstein appears."}, automaton, lookup
    )

    assert status == "unusable"


def test_widened_lookup_drops_single_token_names(tmp_path):
    births = tmp_path / "births.json"
    births.write_text(
        '[{"name": "Cicero", "year": -106, "month": 1, "day": 3},'
        ' {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7}]',
        encoding="utf-8",
    )

    lookup = load_widened_births_lookup(births)

    assert "marie curie" in lookup
    assert "cicero" not in lookup
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.match_events'`

- [ ] **Step 3: Write the implementation**

```python
# src/ingest/match_events.py
"""Stage 0/1 of matching expansion: find every known person named in an event,
using one Aho-Corasick pass over a widened births pool.

Supersedes ingest.pipeline.match_births_to_events, which gated on the top 1,000
Pantheon-ranked people (1,232 matched events) and silently kept whichever name
its dict iteration reached first when a text named several known people. Here
the multi-name case becomes an explicit "ambiguous" status routed to Stage 2
instead of a guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from core.config import DATA_DIR
from ingest.enrichment import load_births_lookup
from ingest.name_index import find_names_in_text
from ingest.pipeline import calculate_age

MAX_AGE_DAYS = 120 * 365

WIDENED_BIRTHS_PATH = DATA_DIR / "historical_births_cleaned.json"
MATCHING_REVIEW_PATH = DATA_DIR / "tmp" / "matching_review.json"


def load_widened_births_lookup(path: Path = WIDENED_BIRTHS_PATH) -> Dict[str, Dict]:
    """Every scraped birth record keyed by normalize_name, minus single-token names.

    Drops the top-1,000 Pantheon fame gate. Single-token names stay excluded so
    "John" or "Cicero" alone can never match an event text.
    """
    lookup = load_births_lookup(path)
    return {key: value for key, value in lookup.items() if len(key.split()) > 1}


def classify_event(event: Dict, automaton, births_lookup: Dict[str, Dict]) -> Tuple[str, object]:
    """Classify one event against the known-person index.

    Returns (status, payload):
    - ("matched", event_record)   - exactly one known person, plausible age.
                                    event_record adds "name" and "age" to event.
    - ("ambiguous", names)        - several known people named; needs Stage 2.
    - ("unmatched", None)         - no known person named; needs Stage 2.
    - ("implausible", name)       - one person, but the age fails the bound.
    - ("unusable", None)          - the event has no numeric year.
    """
    year = event.get("year")
    if year is None or not str(year).isdigit():
        return "unusable", None

    names = find_names_in_text(automaton, event["text"])
    if not names:
        return "unmatched", None
    if len(names) > 1:
        return "ambiguous", names

    birth = births_lookup.get(names[0])
    if birth is None:
        return "unmatched", None

    age = calculate_age(
        birth["year"], birth["month"], birth["day"],
        int(year), int(event["month"]), int(event["day"]),
    )
    if age is None or not (0 <= age <= MAX_AGE_DAYS):
        return "implausible", birth["name"]

    return "matched", {**event, "name": birth["name"], "age": age}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/match_events.py tests/test_match_events.py
git commit -m "feat: add Stage 0/1 event classification over widened births pool"
```

---

### Task 3: Stage 0/1 orchestration and output files

**Files:**
- Modify: `src/ingest/match_events.py` (append to the module from Task 2)
- Modify: `src/ingest/pipeline.py:24-85` (delete the superseded matcher)
- Test: `tests/test_match_events.py` (append)

**Interfaces:**
- Consumes: `classify_event`, `load_widened_births_lookup`, `MATCHING_REVIEW_PATH` (Task 2); `enrichment.write_review_entries`; `core.io.load_json`, `core.io.save_to_json`.
- Produces:
  - `match_events.EVENTS_WITH_AGE_PATH: Path` (= `DATA_DIR / "events_with_age.json"`)
  - `match_events.SUBJECT_PENDING_PATH: Path` (= `DATA_DIR / "tmp" / "subject_pending.json"`)
  - `match_events.append_matched_events(new_events: List[Dict], path=EVENTS_WITH_AGE_PATH) -> int` — appends, skipping any record whose `(name, text)` key already exists in the file; returns how many were actually added. **Tasks 5 and 7 both call this.**
  - `match_events.run_stage_one(...) -> Dict[str, int]` — counts keyed `matched`, `ambiguous`, `unmatched`, `implausible`, `unusable`.

**Note on deleting from `pipeline.py`:** remove `_load_birth_lookup`, `match_births_to_events`, and the `compile_name_pattern`/`normalize_name` import, and rewrite `main()` to call `run_stage_one`. **Keep `calculate_age`** — `ingest.enrichment` and `ingest.match_events` both import it. Keep `only_name_and_text_from_json`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_match_events.py
import json

from ingest.match_events import append_matched_events, run_stage_one


def test_append_matched_events_creates_the_file(tmp_path):
    path = tmp_path / "events_with_age.json"

    added = append_matched_events([{"name": "Marie Curie", "text": "she won a prize", "age": 1}], path)

    assert added == 1
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "Marie Curie"


def test_append_matched_events_skips_duplicates_by_name_and_text(tmp_path):
    path = tmp_path / "events_with_age.json"
    existing = [{"name": "Marie Curie", "text": "she won a prize", "age": 1}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    added = append_matched_events(
        [
            {"name": "Marie Curie", "text": "she won a prize", "age": 1},
            {"name": "Albert Einstein", "text": "he published a paper", "age": 2},
        ],
        path,
    )

    assert added == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored) == 2


def test_run_stage_one_splits_events_and_writes_pending(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [
                {"year": 1905, "month": 11, "day": 21, "text": "Albert Einstein published a paper."},
                {"year": 1911, "month": 12, "day": 10, "text": "A committee met in Oslo."},
                {"year": 1905, "month": 1, "day": 1, "text": "Marie Curie wrote to Albert Einstein."},
            ]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps(
            [
                {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14},
                {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7},
            ]
        ),
        encoding="utf-8",
    )
    matched_path = tmp_path / "events_with_age.json"
    pending_path = tmp_path / "subject_pending.json"
    review_path = tmp_path / "matching_review.json"

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=matched_path,
        pending_path=pending_path,
        review_path=review_path,
    )

    assert counts["matched"] == 1
    assert counts["unmatched"] == 1
    assert counts["ambiguous"] == 1

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert len(pending) == 2
    assert {entry["reason"] for entry in pending} == {"unmatched", "ambiguous"}


def test_run_stage_one_records_implausible_matches_for_review(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps([{"year": 1800, "month": 1, "day": 1, "text": "Albert Einstein appears."}]),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}]),
        encoding="utf-8",
    )
    review_path = tmp_path / "matching_review.json"

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        pending_path=tmp_path / "subject_pending.json",
        review_path=review_path,
    )

    assert counts["implausible"] == 1
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "implausible_age"
    assert review[0]["stage"] == "stage_1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: FAIL with `ImportError: cannot import name 'append_matched_events'`

- [ ] **Step 3: Append the implementation to `src/ingest/match_events.py`**

```python
# append to src/ingest/match_events.py
# (add to the existing imports at the top of the file:)
#   from typing import Dict, List, Tuple
#   from core.io import load_json, save_to_json
#   from ingest.enrichment import load_births_lookup, write_review_entries
#   from ingest.name_index import build_name_index, find_names_in_text

EVENTS_WITH_AGE_PATH = DATA_DIR / "events_with_age.json"
SUBJECT_PENDING_PATH = DATA_DIR / "tmp" / "subject_pending.json"


def _event_key(event: Dict):
    """Natural key for a matched event - same (name, text) key llm_utils uses."""
    return (event.get("name"), event.get("text"))


def append_matched_events(new_events: List[Dict], path: Path = EVENTS_WITH_AGE_PATH) -> int:
    """Append matched events to path, skipping any whose (name, text) is already there.

    Returns how many were actually added. Safe to call repeatedly - Stage 1, 2
    and 3 all append to the same file across separate runs.
    """
    try:
        existing = load_json(path)
    except FileNotFoundError:
        existing = []

    seen = {_event_key(event) for event in existing}
    added = 0
    for event in new_events:
        if _event_key(event) in seen:
            continue
        existing.append(event)
        seen.add(_event_key(event))
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(path, existing)
    return added


def run_stage_one(
    events_path: Path = DATA_DIR / "historical_events.json",
    births_path: Path = WIDENED_BIRTHS_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    pending_path: Path = SUBJECT_PENDING_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> Dict[str, int]:
    """Classify every scraped event, appending matches and queueing the rest for Stage 2.

    Writes three files: matched events (appended), the Stage 2 queue
    (unmatched + ambiguous events), and review entries for implausible ages.
    """
    events = load_json(events_path)
    births_lookup = load_widened_births_lookup(births_path)
    automaton = build_name_index(births_lookup.keys())

    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0, "implausible": 0, "unusable": 0}
    matched: List[Dict] = []
    pending: List[Dict] = []
    review: List[Dict] = []

    for event in events:
        status, payload = classify_event(event, automaton, births_lookup)
        counts[status] += 1

        if status == "matched":
            matched.append(payload)
        elif status in ("unmatched", "ambiguous"):
            entry = {**event, "reason": status}
            if status == "ambiguous":
                entry["candidates"] = payload
            pending.append(entry)
        elif status == "implausible":
            review.append(
                {
                    "stage": "stage_1",
                    "issue_type": "implausible_age",
                    "name": payload,
                    "text": event["text"],
                    "detail": f"age for {payload!r} outside 0..{MAX_AGE_DAYS} days",
                }
            )

    counts["appended"] = append_matched_events(matched, matched_path)

    pending_path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(pending_path, pending)
    write_review_entries(review, review_path)

    return counts


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run_stage_one())
```

- [ ] **Step 4: Delete the superseded matcher from `src/ingest/pipeline.py`**

Delete `_load_birth_lookup` (lines 24-47) and `match_births_to_events` (lines 50-85), change the imports to drop `compile_name_pattern`/`normalize_name`, and replace `main()`:

```python
# src/ingest/pipeline.py - replace the import block and main()
from core.io import load_json


def main() -> None:  # pragma: no cover - manual helper
    """Stage 0/1 matching now lives in ingest.match_events (Aho-Corasick, widened pool)."""
    from ingest.match_events import run_stage_one

    print(run_stage_one())
```

Keep `calculate_age` unchanged — `ingest.enrichment` and `ingest.match_events` both import it. Keep `only_name_and_text_from_json` unchanged.

- [ ] **Step 5: Run the full suite**

Run: `venv\Scripts\python.exe -m pytest -v`
Expected: PASS. If any existing test imports `match_births_to_events`, delete that test — the function is intentionally gone.

- [ ] **Step 6: Commit**

```bash
git add src/ingest/match_events.py src/ingest/pipeline.py tests/test_match_events.py
git commit -m "feat: add Stage 0/1 orchestration, retire the O(events x names) matcher"
```

---

### Task 4: Stage 2 subject-extraction prompt and chunking

**Files:**
- Create: `src/ingest/subject_prompt.md`
- Create: `src/ingest/subject_extraction.py`
- Test: `tests/test_subject_extraction.py`

**Interfaces:**
- Consumes: `match_events.SUBJECT_PENDING_PATH` (Task 3).
- Produces:
  - `subject_extraction.SUBJECT_CHUNK_DIR: Path` (= `DATA_DIR / "tmp" / "subject_chunks"`)
  - `subject_extraction.build_prompt() -> str`
  - `subject_extraction.prepare_subject_chunks(pending_path=..., chunk_size=100) -> List[Path]`

- [ ] **Step 1: Write the prompt template**

```markdown
# Event subject-identification instructions

You will receive a JSON array of historical event records, each with:
- `text`: the raw event description (as scraped from Wikipedia's "on this day" data)
- `year`, `month`, `day`: the event's date
- `reason`: why this event needs you — `"unmatched"` (no known person was found in the
  text) or `"ambiguous"` (several known people were found and we need the right one)
- `candidates`: present only when `reason` is `"ambiguous"` — the names that were found

For each record, return a JSON object with these fields:

- `text`: copied unchanged from the input. This is how the record is matched back, so it
  must be byte-identical to what you were given.
- `subject`: the name of the single person who is the grammatical subject of `text` — the
  person the event is *about*, who performed or underwent the action. Rules:
  - Copy the name **verbatim as it appears in `text`**. Do not expand "Bach" into "Johann
    Sebastian Bach", and do not correct spelling. If the text says "Napoleon", return
    "Napoleon".
  - When `reason` is `"ambiguous"`, prefer whichever of `candidates` is the true subject,
    but if the real subject is a different person named in `text`, return that name instead.
  - Return `null` if `text` names no person at all (a treaty, a battle between countries,
    an organization, a natural disaster), or if you cannot tell which person is the subject.
    `null` is the correct, expected answer for many records — do not invent a person to
    avoid returning it.
  - Never return a name that does not literally appear in `text`.

Return a JSON array in the same order as the input, one object per input record. Return
every record you were given — don't skip any.
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_subject_extraction.py
import json

from ingest.subject_extraction import build_prompt, prepare_subject_chunks


def test_build_prompt_describes_the_subject_field():
    prompt = build_prompt()
    assert "verbatim as it appears in `text`" in prompt
    assert "`subject`" in prompt


def test_prepare_subject_chunks_splits_by_chunk_size(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text(
        json.dumps([{"text": f"Event {index}", "reason": "unmatched"} for index in range(5)]),
        encoding="utf-8",
    )
    chunk_dir = tmp_path / "subject_chunks"

    paths = prepare_subject_chunks(pending_path=pending_path, chunk_size=2, chunk_dir=chunk_dir)

    assert len(paths) == 3
    assert paths[0].name == "chunk_0000.json"
    assert len(json.loads(paths[0].read_text(encoding="utf-8"))) == 2
    assert len(json.loads(paths[2].read_text(encoding="utf-8"))) == 1


def test_prepare_subject_chunks_handles_an_empty_queue(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text("[]", encoding="utf-8")

    paths = prepare_subject_chunks(pending_path=pending_path, chunk_dir=tmp_path / "chunks")

    assert paths == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.subject_extraction'`

- [ ] **Step 4: Write the implementation**

```python
# src/ingest/subject_extraction.py
"""Stage 2: ask a Claude Haiku subagent which person an event is actually about.

Two-phase like ingest.llm_utils, because a Claude Code subagent runs between
the two calls:

    python -c "from ingest.subject_extraction import prepare_subject_chunks; prepare_subject_chunks()"
    # ... dispatch a Haiku subagent per chunk file, using build_prompt() ...
    python -c "from ingest.subject_extraction import merge_subject_chunk; merge_subject_chunk('data/tmp/subject_chunks/chunk_0000.json', 'data/tmp/subject_chunks/chunk_0000_result.json')"

The subagent is never asked for a birth date - only for a name it can read in
the text. Everything it returns is validated in Python before use.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from ingest.match_events import SUBJECT_PENDING_PATH

PROMPT_TEMPLATE_PATH = Path(__file__).parent / "subject_prompt.md"
SUBJECT_CHUNK_DIR = DATA_DIR / "tmp" / "subject_chunks"
CHUNK_SIZE = 100


def build_prompt() -> str:
    """Read subject_prompt.md. No placeholders to fill - the instructions are static."""
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def prepare_subject_chunks(
    pending_path: Path = SUBJECT_PENDING_PATH,
    chunk_size: int = CHUNK_SIZE,
    chunk_dir: Path = SUBJECT_CHUNK_DIR,
) -> List[Path]:
    """Split the Stage 2 queue into numbered chunk files for a subagent to process."""
    pending = load_json(pending_path)

    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        path = chunk_dir / f"chunk_{index:04d}.json"
        save_to_json(path, pending[start : start + chunk_size])
        paths.append(path)
    return paths
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/ingest/subject_prompt.md src/ingest/subject_extraction.py tests/test_subject_extraction.py
git commit -m "feat: add Stage 2 subject-extraction prompt and chunking"
```

---

### Task 5: Stage 2 merge and routing

**Files:**
- Modify: `src/ingest/subject_extraction.py` (append)
- Test: `tests/test_subject_extraction.py` (append)

**Interfaces:**
- Consumes: `enrichment.resolve_subject`, `core.matching.name_matches_text`, `core.matching.normalize_name`, `match_events.append_matched_events`, `match_events.MATCHING_REVIEW_PATH`, `enrichment.write_review_entries`.
- Produces:
  - `subject_extraction.WIKIDATA_PENDING_PATH: Path` (= `DATA_DIR / "tmp" / "wikidata_pending.json"`)
  - `subject_extraction.route_subject(event: Dict, suggested_name, births_lookup) -> Tuple[str, object]` — status one of `"matched"`, `"wikidata_candidate"`, `"no_subject"`, `"rejected"`.
  - `subject_extraction.merge_subject_chunk(chunk_path, result_path, ...) -> Dict[str, int]`

**Why `route_subject` re-checks rather than parsing `resolve_subject`'s reason string:** `resolve_subject` returns a human-readable rejection reason, and branching on its wording would break the moment that string changes. `route_subject` calls `resolve_subject` for the happy path, then does its own two explicit checks to separate "name is real but unknown to us" (→ Stage 3) from "name isn't in the text" (→ review).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_subject_extraction.py
from ingest.subject_extraction import merge_subject_chunk, route_subject

EINSTEIN = {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}
LOOKUP = {"albert einstein": EINSTEIN}
EVENT = {"year": 1905, "month": 11, "day": 21, "text": "Albert Einstein published a paper."}


def test_route_subject_matches_a_known_person():
    status, payload = route_subject(EVENT, "Albert Einstein", LOOKUP)

    assert status == "matched"
    assert payload["name"] == "Albert Einstein"
    assert payload["age"] == 9748


def test_route_subject_sends_an_unknown_but_real_name_to_wikidata():
    event = {"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed the draft."}

    status, payload = route_subject(event, "Mileva Maric", LOOKUP)

    assert status == "wikidata_candidate"
    assert payload == "Mileva Maric"


def test_route_subject_rejects_a_name_absent_from_the_text():
    status, payload = route_subject(EVENT, "Niels Bohr", LOOKUP)

    assert status == "rejected"
    assert "not found in event text" in payload


def test_route_subject_accepts_null_as_no_subject():
    status, _ = route_subject(EVENT, None, LOOKUP)

    assert status == "no_subject"


def test_merge_subject_chunk_routes_each_outcome(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(
        json.dumps(
            [
                EVENT,
                {"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed the draft."},
                {"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed at Versailles."},
            ]
        ),
        encoding="utf-8",
    )
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps(
            [
                {"text": "Albert Einstein published a paper.", "subject": "Albert Einstein"},
                {"text": "Mileva Maric reviewed the draft.", "subject": "Mileva Maric"},
                {"text": "A treaty was signed at Versailles.", "subject": None},
            ]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")

    matched_path = tmp_path / "events_with_age.json"
    wikidata_path = tmp_path / "wikidata_pending.json"
    review_path = tmp_path / "matching_review.json"

    counts = merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=matched_path,
        wikidata_pending_path=wikidata_path,
        review_path=review_path,
    )

    assert counts["matched"] == 1
    assert counts["wikidata_candidate"] == 1
    assert counts["no_subject"] == 1

    assert json.loads(matched_path.read_text(encoding="utf-8"))[0]["name"] == "Albert Einstein"
    assert json.loads(wikidata_path.read_text(encoding="utf-8"))[0]["subject"] == "Mileva Maric"
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "no_subject"


def test_merge_subject_chunk_survives_a_missing_result_file(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps([EVENT]), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")

    counts = merge_subject_chunk(
        chunk_path,
        tmp_path / "does_not_exist.json",
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
    )

    assert counts["no_subject"] == 1
    assert counts["matched"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: FAIL with `ImportError: cannot import name 'route_subject'`

- [ ] **Step 3: Append the implementation**

```python
# append to src/ingest/subject_extraction.py
# (add to the existing imports at the top of the file:)
#   import json
#   from typing import Dict, List, Optional, Tuple
#   from core.matching import name_matches_text, normalize_name
#   from ingest.enrichment import resolve_subject, write_review_entries
#   from ingest.match_events import (
#       EVENTS_WITH_AGE_PATH, MATCHING_REVIEW_PATH, WIDENED_BIRTHS_PATH,
#       append_matched_events, load_widened_births_lookup,
#   )

WIKIDATA_PENDING_PATH = DATA_DIR / "tmp" / "wikidata_pending.json"


def route_subject(
    event: Dict,
    suggested_name: Optional[str],
    births_lookup: Dict[str, Dict],
) -> Tuple[str, object]:
    """Decide what to do with one subagent-suggested subject.

    Returns (status, payload):
    - ("matched", event_record)      - known person, plausible age; ready to store.
    - ("wikidata_candidate", name)   - really in the text, but unknown to us; Stage 3.
    - ("no_subject", None)           - the subagent found no person (an expected outcome).
    - ("rejected", reason)           - failed validation; goes to the review report.
    """
    if not suggested_name:
        return "no_subject", None

    correction, reason = resolve_subject(event, suggested_name, births_lookup)
    if correction:
        return "matched", {**event, "name": correction["name"], "age": correction["age_days"]}

    # Separate "we don't know this person" (resolvable via Wikidata) from every
    # other rejection, without branching on resolve_subject's reason wording.
    if not name_matches_text(suggested_name, event["text"]):
        return "rejected", reason
    if normalize_name(suggested_name) not in births_lookup:
        return "wikidata_candidate", suggested_name
    return "rejected", reason


def merge_subject_chunk(
    chunk_path,
    result_path,
    births_path: Path = WIDENED_BIRTHS_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    wikidata_pending_path: Path = WIKIDATA_PENDING_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> Dict[str, int]:
    """Validate one chunk's subagent output and route each event to its next step.

    Records are matched back to the chunk by `text`, not list position, so a
    subagent that reorders or drops records is still handled. A missing or
    malformed result file leaves every record in the chunk as "no_subject".
    """
    chunk = load_json(chunk_path)
    try:
        results = load_json(result_path)
        results_by_text = {result.get("text"): result for result in results}
    except (FileNotFoundError, json.JSONDecodeError):
        results_by_text = {}

    births_lookup = load_widened_births_lookup(births_path)

    counts = {"matched": 0, "wikidata_candidate": 0, "no_subject": 0, "rejected": 0}
    matched: List[Dict] = []
    wikidata_pending: List[Dict] = []
    review: List[Dict] = []

    for event in chunk:
        result = results_by_text.get(event.get("text")) or {}
        status, payload = route_subject(event, result.get("subject"), births_lookup)
        counts[status] += 1

        if status == "matched":
            matched.append(payload)
        elif status == "wikidata_candidate":
            wikidata_pending.append({**event, "subject": payload})
        else:
            review.append(
                {
                    "stage": "stage_2",
                    "issue_type": status,
                    "text": event.get("text"),
                    "detail": payload if status == "rejected" else "no subject identified in the text",
                }
            )

    counts["appended"] = append_matched_events(matched, matched_path)
    _append_json_list(wikidata_pending_path, wikidata_pending)
    write_review_entries(review, review_path)
    return counts


def _append_json_list(path: Path, entries: List[Dict]) -> None:
    """Append entries to a JSON array file, creating it if missing."""
    try:
        existing = load_json(path)
    except FileNotFoundError:
        existing = []
    existing.extend(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(path, existing)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/subject_extraction.py tests/test_subject_extraction.py
git commit -m "feat: add Stage 2 merge routing matched/wikidata/review outcomes"
```

---

### Task 6: Wikidata birth-date lookup

**Files:**
- Create: `src/ingest/sources/wikidata.py`
- Test: `tests/test_wikidata.py`

**Interfaces:**
- Consumes: `core.matching.normalize_name`, `core.config.DATA_DIR`.
- Produces:
  - `wikidata.CACHE_PATH: Path` (= `DATA_DIR / "wikidata_persons_cache.json"`)
  - `wikidata.parse_birth_claim(claim: Dict) -> Tuple[str, Optional[Dict]]` — status `"resolved"` or `"insufficient_precision"`.
  - `wikidata.lookup_birth_date(name: str, event_year: Optional[int], cache: Dict) -> Dict` — returns `{"status": ..., "name": ..., ...}` where status is one of `"resolved"`, `"ambiguous"`, `"not_found"`, `"insufficient_precision"`. A `"resolved"` result also carries `year`, `month`, `day`, `qid`.
  - `wikidata.load_cache(path=CACHE_PATH) -> Dict`, `wikidata.save_cache(cache, path=CACHE_PATH) -> None`
  - `wikidata._get_json(params: Dict) -> Dict` — **the only function that touches the network**; tests monkeypatch this.

**Wikidata specifics the implementer needs:** the API is `https://www.wikidata.org/w/api.php`. `action=wbsearchentities&search=<name>&language=en&type=item&format=json&limit=10` returns `{"search": [{"id": "Q937", "label": ..., "description": ...}]}`. `action=wbgetentities&ids=Q937&props=claims&format=json` returns `{"entities": {"Q937": {"claims": {"P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1879-03-14T00:00:00Z", "precision": 11}}}}]}}}}`. **Precision 11 = day, 10 = month, 9 = year** — only 11 is usable. Times are prefixed `+` and BC dates are prefixed `-` (a `-` prefix is treated as unusable here, since `datetime.date` cannot represent BC years).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wikidata.py
import json

import pytest

from ingest.sources import wikidata


def test_parse_birth_claim_accepts_day_precision():
    claim = {"mainsnak": {"datavalue": {"value": {"time": "+1879-03-14T00:00:00Z", "precision": 11}}}}

    status, parsed = wikidata.parse_birth_claim(claim)

    assert status == "resolved"
    assert parsed == {"year": 1879, "month": 3, "day": 14}


@pytest.mark.parametrize("precision", [9, 10])
def test_parse_birth_claim_rejects_coarser_precision(precision):
    claim = {"mainsnak": {"datavalue": {"value": {"time": "+1879-01-01T00:00:00Z", "precision": precision}}}}

    status, parsed = wikidata.parse_birth_claim(claim)

    assert status == "insufficient_precision"
    assert parsed is None


def test_parse_birth_claim_rejects_bc_dates():
    claim = {"mainsnak": {"datavalue": {"value": {"time": "-0106-01-03T00:00:00Z", "precision": 11}}}}

    status, _ = wikidata.parse_birth_claim(claim)

    assert status == "insufficient_precision"


def _fake_api(monkeypatch, search, entities):
    def fake_get_json(params):
        if params["action"] == "wbsearchentities":
            return {"search": search}
        return {"entities": entities}

    monkeypatch.setattr(wikidata, "_get_json", fake_get_json)


def test_lookup_resolves_a_single_candidate(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[{"id": "Q937", "label": "Albert Einstein", "description": "physicist"}],
        entities={
            "Q937": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1879-03-14T00:00:00Z", "precision": 11}}}}]
                }
            }
        },
    )

    result = wikidata.lookup_birth_date("Albert Einstein", event_year=1905, cache={})

    assert result["status"] == "resolved"
    assert (result["year"], result["month"], result["day"]) == (1879, 3, 14)
    assert result["qid"] == "Q937"


def test_lookup_reports_not_found_when_search_is_empty(monkeypatch):
    _fake_api(monkeypatch, search=[], entities={})

    result = wikidata.lookup_birth_date("Nobody At All", event_year=1905, cache={})

    assert result["status"] == "not_found"


def test_lookup_narrows_candidates_by_event_year(monkeypatch):
    # Only the second candidate could have been alive in 1905.
    _fake_api(
        monkeypatch,
        search=[
            {"id": "Q1", "label": "John Smith", "description": "medieval monk"},
            {"id": "Q2", "label": "John Smith", "description": "chemist"},
        ],
        entities={
            "Q1": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1400-01-02T00:00:00Z", "precision": 11}}}}]
                }
            },
            "Q2": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1870-05-06T00:00:00Z", "precision": 11}}}}]
                }
            },
        },
    )

    result = wikidata.lookup_birth_date("John Smith", event_year=1905, cache={})

    assert result["status"] == "resolved"
    assert result["qid"] == "Q2"


def test_lookup_reports_ambiguous_when_several_candidates_fit(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[
            {"id": "Q1", "label": "John Smith", "description": "chemist"},
            {"id": "Q2", "label": "John Smith", "description": "poet"},
        ],
        entities={
            "Q1": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1870-05-06T00:00:00Z", "precision": 11}}}}]
                }
            },
            "Q2": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1872-08-09T00:00:00Z", "precision": 11}}}}]
                }
            },
        },
    )

    result = wikidata.lookup_birth_date("John Smith", event_year=1905, cache={})

    assert result["status"] == "ambiguous"


def test_lookup_reports_insufficient_precision(monkeypatch):
    _fake_api(
        monkeypatch,
        search=[{"id": "Q1", "label": "Old Figure", "description": "ruler"}],
        entities={
            "Q1": {
                "claims": {
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1200-01-01T00:00:00Z", "precision": 9}}}}]
                }
            }
        },
    )

    result = wikidata.lookup_birth_date("Old Figure", event_year=1250, cache={})

    assert result["status"] == "insufficient_precision"


def test_lookup_uses_the_cache_and_makes_no_request(monkeypatch):
    def explode(params):
        raise AssertionError("network call made despite a cache hit")

    monkeypatch.setattr(wikidata, "_get_json", explode)
    cache = {"albert einstein": {"status": "not_found", "name": "Albert Einstein"}}

    result = wikidata.lookup_birth_date("Albert Einstein", event_year=1905, cache=cache)

    assert result["status"] == "not_found"


def test_lookup_writes_every_outcome_into_the_cache(monkeypatch):
    _fake_api(monkeypatch, search=[], entities={})
    cache = {}

    wikidata.lookup_birth_date("Nobody At All", event_year=1905, cache=cache)

    assert cache["nobody at all"]["status"] == "not_found"


def test_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "cache.json"
    wikidata.save_cache({"marie curie": {"status": "resolved"}}, path)

    assert wikidata.load_cache(path) == {"marie curie": {"status": "resolved"}}


def test_load_cache_returns_empty_when_missing(tmp_path):
    assert wikidata.load_cache(tmp_path / "absent.json") == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_wikidata.py -v`
Expected: FAIL with `ImportError: cannot import name 'wikidata'`

- [ ] **Step 3: Write the implementation**

```python
# src/ingest/sources/wikidata.py
"""Wikidata birth-date lookup for people absent from the scraped births data.

The scraped births file only contains whoever appeared on a Wikipedia "on this
day" births section. Wikidata covers far more people, and is the only source
here that can push past that ceiling.

Only day-precision birth dates are usable, because the app compares ages in
days. Coarser precision is reported, not silently dropped - for older figures
it is a large and predictable category.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from core.config import DATA_DIR
from core.matching import normalize_name

API_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = "achievement-age/0.1 (https://github.com/didrikfo/achievement-age) ingest script"
REQUEST_DELAY_SECONDS = 0.2
SEARCH_LIMIT = 10
MAX_LIFESPAN_YEARS = 120
DAY_PRECISION = 11

CACHE_PATH = DATA_DIR / "wikidata_persons_cache.json"


def load_cache(path: Path = CACHE_PATH) -> Dict:
    """Load the name -> outcome cache, or an empty dict if it doesn't exist yet."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def save_cache(cache: Dict, path: Path = CACHE_PATH) -> None:
    """Persist the cache. Written as a JSON object, not the array core.io expects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2)


def _get_json(params: Dict) -> Dict:
    """The only network call in this module - monkeypatched in tests.

    Rate-limited and identified by User-Agent, per Wikidata's API etiquette.
    """
    time.sleep(REQUEST_DELAY_SECONDS)
    response = requests.get(
        API_URL,
        params={**params, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def parse_birth_claim(claim: Dict) -> Tuple[str, Optional[Dict]]:
    """Turn one P569 claim into ("resolved", {year, month, day}) or ("insufficient_precision", None).

    Wikidata precision: 11 = day, 10 = month, 9 = year. Only 11 is usable. BC
    dates (a leading "-") are rejected too, since datetime.date can't hold them.
    """
    try:
        value = claim["mainsnak"]["datavalue"]["value"]
        if int(value["precision"]) != DAY_PRECISION:
            return "insufficient_precision", None
        time_string = value["time"]
    except (KeyError, TypeError, ValueError):
        return "insufficient_precision", None

    if not time_string.startswith("+"):
        return "insufficient_precision", None

    try:
        date_part = time_string[1:].split("T", 1)[0]
        year, month, day = (int(part) for part in date_part.split("-"))
    except (ValueError, IndexError):
        return "insufficient_precision", None

    if month == 0 or day == 0:
        return "insufficient_precision", None
    return "resolved", {"year": year, "month": month, "day": day}


def _search_candidates(name: str) -> List[Dict]:
    response = _get_json(
        {"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": SEARCH_LIMIT}
    )
    return response.get("search", [])


def _fetch_birth_claims(qid: str) -> List[Dict]:
    response = _get_json({"action": "wbgetentities", "ids": qid, "props": "claims"})
    entity = response.get("entities", {}).get(qid, {})
    return entity.get("claims", {}).get("P569", [])


def _plausible_for_event(birth_year: int, event_year: Optional[int]) -> bool:
    """The person must have been born by the event and not impossibly long before it."""
    if event_year is None:
        return True
    return birth_year <= event_year <= birth_year + MAX_LIFESPAN_YEARS


def lookup_birth_date(name: str, event_year: Optional[int], cache: Dict) -> Dict:
    """Resolve one name to a day-precision birth date, caching every outcome.

    Returns a dict whose "status" is one of "resolved", "ambiguous",
    "not_found", or "insufficient_precision". "resolved" also carries year,
    month, day and qid. Cache hits make no network request.
    """
    key = normalize_name(name)
    if key in cache:
        return cache[key]

    candidates = _search_candidates(name)
    if not candidates:
        return _remember(cache, key, {"status": "not_found", "name": name})

    plausible: List[Dict] = []
    saw_coarse_precision = False
    for candidate in candidates:
        for claim in _fetch_birth_claims(candidate["id"]):
            status, parsed = parse_birth_claim(claim)
            if status != "resolved":
                saw_coarse_precision = True
                continue
            if _plausible_for_event(parsed["year"], event_year):
                plausible.append({**parsed, "qid": candidate["id"], "label": candidate.get("label")})
            break

    if len(plausible) == 1:
        return _remember(cache, key, {"status": "resolved", "name": name, **plausible[0]})
    if len(plausible) > 1:
        return _remember(
            cache,
            key,
            {
                "status": "ambiguous",
                "name": name,
                "candidates": [{"qid": item["qid"], "label": item["label"]} for item in plausible],
            },
        )
    if saw_coarse_precision:
        return _remember(cache, key, {"status": "insufficient_precision", "name": name})
    return _remember(cache, key, {"status": "not_found", "name": name})


def _remember(cache: Dict, key: str, result: Dict) -> Dict:
    cache[key] = result
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_wikidata.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/sources/wikidata.py tests/test_wikidata.py
git commit -m "feat: add Wikidata birth-date lookup with precision filtering and caching"
```

---

### Task 7: Stage 3 resolution run

**Files:**
- Create: `src/ingest/resolve_wikidata.py`
- Test: `tests/test_resolve_wikidata.py`

**Interfaces:**
- Consumes: `wikidata.lookup_birth_date`, `wikidata.load_cache`, `wikidata.save_cache` (Task 6); `subject_extraction.WIKIDATA_PENDING_PATH` (Task 5); `match_events.append_matched_events`, `match_events.MAX_AGE_DAYS`, `match_events.MATCHING_REVIEW_PATH` (Tasks 2-3); `pipeline.calculate_age`; `enrichment.write_review_entries`.
- Produces: `resolve_wikidata.run_stage_three(...) -> Dict[str, int]` with counts keyed `resolved`, `ambiguous`, `not_found`, `insufficient_precision`, `implausible`, `appended`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_resolve_wikidata.py
import json

from ingest import resolve_wikidata
from ingest.sources import wikidata


def _pending(tmp_path, entries):
    path = tmp_path / "wikidata_pending.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_resolved_lookup_becomes_a_matched_event(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1875, "month": 12, "day": 19, "qid": "Q1"
        },
    )
    pending_path = _pending(
        tmp_path,
        [{"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed it.", "subject": "Mileva Maric"}],
    )
    matched_path = tmp_path / "events_with_age.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=matched_path,
        review_path=tmp_path / "matching_review.json",
        cache_path=tmp_path / "cache.json",
    )

    assert counts["resolved"] == 1
    assert counts["appended"] == 1
    stored = json.loads(matched_path.read_text(encoding="utf-8"))
    assert stored[0]["name"] == "Mileva Maric"
    assert stored[0]["age"] == 10605


def test_ambiguous_lookup_goes_to_review_not_the_matched_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {"status": "ambiguous", "name": name, "candidates": []},
    )
    pending_path = _pending(
        tmp_path, [{"year": 1905, "month": 1, "day": 1, "text": "John Smith spoke.", "subject": "John Smith"}]
    )
    matched_path = tmp_path / "events_with_age.json"
    review_path = tmp_path / "matching_review.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=matched_path,
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )

    assert counts["ambiguous"] == 1
    assert counts["appended"] == 0
    assert not matched_path.exists() or json.loads(matched_path.read_text(encoding="utf-8")) == []
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "ambiguous"
    assert review[0]["stage"] == "stage_3"


def test_insufficient_precision_is_recorded_as_its_own_category(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {"status": "insufficient_precision", "name": name},
    )
    pending_path = _pending(
        tmp_path, [{"year": 1250, "month": 1, "day": 1, "text": "Old Figure ruled.", "subject": "Old Figure"}]
    )
    review_path = tmp_path / "matching_review.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=tmp_path / "events_with_age.json",
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )

    assert counts["insufficient_precision"] == 1
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "insufficient_precision"


def test_implausible_age_is_rejected_even_when_wikidata_resolved(tmp_path, monkeypatch):
    # Born 1879, event in 1800 - the bound must still reject it.
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1879, "month": 3, "day": 14, "qid": "Q1"
        },
    )
    pending_path = _pending(
        tmp_path, [{"year": 1800, "month": 1, "day": 1, "text": "Someone Odd appeared.", "subject": "Someone Odd"}]
    )
    review_path = tmp_path / "matching_review.json"

    counts = resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=tmp_path / "events_with_age.json",
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )

    assert counts["implausible"] == 1
    assert counts["appended"] == 0
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "implausible_age"


def test_cache_is_persisted_after_the_run(tmp_path, monkeypatch):
    def fake_lookup(name, event_year, cache):
        cache[name.lower()] = {"status": "not_found", "name": name}
        return cache[name.lower()]

    monkeypatch.setattr(wikidata, "lookup_birth_date", fake_lookup)
    pending_path = _pending(
        tmp_path, [{"year": 1905, "month": 1, "day": 1, "text": "Nobody spoke.", "subject": "Nobody"}]
    )
    cache_path = tmp_path / "cache.json"

    resolve_wikidata.run_stage_three(
        pending_path=pending_path,
        matched_path=tmp_path / "events_with_age.json",
        review_path=tmp_path / "matching_review.json",
        cache_path=cache_path,
    )

    assert json.loads(cache_path.read_text(encoding="utf-8"))["nobody"]["status"] == "not_found"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_resolve_wikidata.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.resolve_wikidata'`

- [ ] **Step 3: Write the implementation**

```python
# src/ingest/resolve_wikidata.py
"""Stage 3: resolve subjects that Stage 2 identified but the local births data
doesn't know, by looking up their birth date on Wikidata.

Run after Stage 2's merge step has populated data/tmp/wikidata_pending.json:

    python -m ingest.resolve_wikidata

Safe to rerun: every lookup outcome is cached by name, so a second run makes no
network requests for names already attempted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from core.io import load_json
from ingest.enrichment import write_review_entries
from ingest.match_events import (
    EVENTS_WITH_AGE_PATH,
    MATCHING_REVIEW_PATH,
    MAX_AGE_DAYS,
    append_matched_events,
)
from ingest.pipeline import calculate_age
from ingest.sources import wikidata
from ingest.subject_extraction import WIKIDATA_PENDING_PATH


def run_stage_three(
    pending_path: Path = WIKIDATA_PENDING_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
    cache_path: Path = wikidata.CACHE_PATH,
) -> Dict[str, int]:
    """Resolve each pending subject against Wikidata, storing matches and flagging the rest."""
    pending = load_json(pending_path)
    cache = wikidata.load_cache(cache_path)

    counts = {
        "resolved": 0, "ambiguous": 0, "not_found": 0,
        "insufficient_precision": 0, "implausible": 0,
    }
    matched: List[Dict] = []
    review: List[Dict] = []

    for entry in pending:
        subject = entry["subject"]
        event_year = int(entry["year"]) if str(entry.get("year", "")).isdigit() else None
        result = wikidata.lookup_birth_date(subject, event_year, cache)
        status = result["status"]

        if status != "resolved":
            counts[status] += 1
            review.append(
                {
                    "stage": "stage_3",
                    "issue_type": status,
                    "name": subject,
                    "text": entry.get("text"),
                    "detail": f"Wikidata lookup for {subject!r} returned {status}",
                }
            )
            continue

        age = calculate_age(
            result["year"], result["month"], result["day"],
            int(entry["year"]), int(entry["month"]), int(entry["day"]),
        )
        if age is None or not (0 <= age <= MAX_AGE_DAYS):
            counts["implausible"] += 1
            review.append(
                {
                    "stage": "stage_3",
                    "issue_type": "implausible_age",
                    "name": subject,
                    "text": entry.get("text"),
                    "detail": f"age for {subject!r} outside 0..{MAX_AGE_DAYS} days",
                }
            )
            continue

        counts["resolved"] += 1
        matched.append(
            {
                "year": entry["year"], "month": entry["month"], "day": entry["day"],
                "text": entry["text"], "name": subject, "age": age,
            }
        )

    counts["appended"] = append_matched_events(matched, matched_path)
    wikidata.save_cache(cache, cache_path)
    write_review_entries(review, review_path)
    return counts


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run_stage_three())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_resolve_wikidata.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/resolve_wikidata.py tests/test_resolve_wikidata.py
git commit -m "feat: add Stage 3 Wikidata resolution run"
```

---

### Task 8: Make `migrate_to_supabase` incremental

**Files:**
- Modify: `src/ingest/migrate_to_supabase.py`
- Test: `tests/test_migrate_to_supabase.py` (create)

**Interfaces:**
- Produces: `migrate_to_supabase.EVENTS_PAGE_SIZE: int` (= 1000), `migrate_to_supabase.fetch_existing_event_keys(client) -> Set[Tuple[str, str]]`, `migrate_to_supabase.filter_new_entries(entries: List[Dict], existing_keys) -> List[Dict]`.

**Why:** `main()` currently loads all of `displayable_events.json` and `.insert()`s every row with no check against Supabase. It was written for a one-time load. Once this plan grows that file past the original 1,232 events, rerunning it would duplicate every already-migrated event.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrate_to_supabase.py
from ingest.migrate_to_supabase import filter_new_entries


def test_filter_new_entries_drops_already_migrated_events():
    entries = [
        {"name": "Marie Curie", "text": "she won a prize"},
        {"name": "Albert Einstein", "text": "he published a paper"},
    ]
    existing = {("Marie Curie", "she won a prize")}

    assert filter_new_entries(entries, existing) == [
        {"name": "Albert Einstein", "text": "he published a paper"}
    ]


def test_filter_new_entries_keeps_everything_when_supabase_is_empty():
    entries = [{"name": "Marie Curie", "text": "she won a prize"}]

    assert filter_new_entries(entries, set()) == entries


def test_filter_new_entries_deduplicates_within_the_input():
    entries = [
        {"name": "Marie Curie", "text": "she won a prize"},
        {"name": "Marie Curie", "text": "she won a prize"},
    ]

    assert len(filter_new_entries(entries, set())) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_migrate_to_supabase.py -v`
Expected: FAIL with `ImportError: cannot import name 'filter_new_entries'`

- [ ] **Step 3: Add the two functions and wire them into `main()`**

```python
# add to src/ingest/migrate_to_supabase.py
from typing import Dict, List, Set, Tuple

EVENTS_PAGE_SIZE = 1000


def fetch_existing_event_keys(client) -> Set[Tuple[str, str]]:
    """Every (name, text) pair already in Supabase, so a rerun can't duplicate them.

    Paginated - PostgREST caps a single response at ~1000 rows.
    """
    keys: Set[Tuple[str, str]] = set()
    start = 0
    while True:
        page = (
            client.table("events")
            .select("name, text")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        keys.update((row["name"], row["text"]) for row in page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return keys


def filter_new_entries(entries: List[Dict], existing_keys: Set[Tuple[str, str]]) -> List[Dict]:
    """Drop entries already in Supabase, and any duplicated within entries itself."""
    seen = set(existing_keys)
    new_entries: List[Dict] = []
    for entry in entries:
        key = (entry["name"], entry["text"])
        if key in seen:
            continue
        seen.add(key)
        new_entries.append(entry)
    return new_entries
```

Then change the top of `main()` so it only inserts new rows:

```python
def main() -> None:
    entries: List[Dict] = load_json(DATA_DIR / "displayable_events.json")

    client = get_client()

    already_migrated = fetch_existing_event_keys(client)
    entries = filter_new_entries(entries, already_migrated)
    if not entries:
        print("Nothing new to migrate.")
        return
    print(f"{len(entries)} new event(s) to migrate.")

    # ... the rest of main() is unchanged, starting at build_person_rows(...)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_migrate_to_supabase.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/migrate_to_supabase.py tests/test_migrate_to_supabase.py
git commit -m "fix: skip already-migrated events instead of inserting duplicates"
```

---

### Task 9: Runbook and full-suite verification

**Files:**
- Modify: `SUPABASE_SETUP.md`
- Modify: `requirements-ingest.txt` (only if Task 1 left it incomplete)

- [ ] **Step 1: Run the full test suite**

Run: `venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no failures, no errors. Fix anything broken before continuing.

- [ ] **Step 2: Confirm the app has no ingest-only dependency**

Run: `venv\Scripts\python.exe -c "import ast,pathlib,sys; bad=[p for p in list(pathlib.Path('src/app').rglob('*.py'))+list(pathlib.Path('src/core').rglob('*.py')) if 'ahocorasick' in p.read_text(encoding='utf-8')]; print('LEAK:',bad) if bad else print('OK: no ahocorasick under src/app or src/core')"`
Expected: `OK: no ahocorasick under src/app or src/core`

- [ ] **Step 3: Add the runbook section to `SUPABASE_SETUP.md`**

Append this section, following the style of the existing numbered sections:

````markdown
## Matching expansion runbook

Grows the pool of age-matchable events. Requires the ingest-only dependencies:

```bash
venv\Scripts\python.exe -m pip install -r requirements-ingest.txt
```

**Stage 0/1 — widened local matching** (no network, no LLM; ~1 minute):

```bash
venv\Scripts\python.exe -m ingest.match_events
```

Appends matches to `data/events_with_age.json`, queues the rest in
`data/tmp/subject_pending.json`, and logs implausible ages to
`data/tmp/matching_review.json`.

**Stage 2 — LLM subject extraction** (run from inside a Claude Code session):

```bash
venv\Scripts\python.exe -c "from ingest.subject_extraction import prepare_subject_chunks; print(prepare_subject_chunks())"
```

Dispatch one Haiku subagent per chunk file using `ingest.subject_extraction.build_prompt()`
as the instructions, write each subagent's JSON array to `<chunk>_result.json`, then merge:

```bash
venv\Scripts\python.exe -c "from ingest.subject_extraction import merge_subject_chunk; print(merge_subject_chunk('data/tmp/subject_chunks/chunk_0000.json', 'data/tmp/subject_chunks/chunk_0000_result.json'))"
```

**Stage 3 — Wikidata resolution** (hits the network; rate-limited and cached):

```bash
venv\Scripts\python.exe -m ingest.resolve_wikidata
```

**Then the existing enrichment + migration flow**, which picks up the new events
automatically (see the reword and migration sections above):

```bash
venv\Scripts\python.exe -c "from ingest.llm_utils import prepare_reword_chunks; print(prepare_reword_chunks())"
# ... subagent per chunk, then merge_reworded_chunk per chunk ...
venv\Scripts\python.exe -m ingest.migrate_to_supabase
```

**Review before trusting the output.** `data/tmp/matching_review.json` collects every
event that could not be auto-accepted — ambiguous subjects, birth dates that are only
year-precision, implausible ages. Nothing in it was guessed at or silently dropped.
````

- [ ] **Step 4: Commit**

```bash
git add SUPABASE_SETUP.md
git commit -m "docs: add the matching expansion runbook"
```

---

## Verification checklist

After all tasks, confirm:

- [ ] `venv\Scripts\python.exe -m pytest -v` passes with no failures.
- [ ] No file under `src/app/` or `src/core/` imports `ahocorasick`.
- [ ] `data/tmp/matching_review.json` exists after a Stage 1 run and contains entries with a `stage` field.
- [ ] Rerunning `ingest.match_events` twice does not double the length of `data/events_with_age.json`.
- [ ] `git ls-tree --name-only HEAD src/ingest/` shows `enrichment.py` (proving the prerequisite merge happened).
