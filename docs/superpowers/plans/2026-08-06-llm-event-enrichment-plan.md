# LLM Event Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the event-reword pipeline to also assign tags, check/correct the matched subject, and follow a versioned prompt, across both the local-JSON new-event path and a new one-off Supabase backfill for the ~1232 existing events.

**Architecture:** A new shared module (`src/ingest/enrichment.py`) holds the tag taxonomy, a versioned prompt template, and validation logic (tag whitelist check, subject-correction resolution against the known births list). Two entry points import from it: the existing `src/ingest/llm_utils.py` chunk/merge flow (new events, local JSON) and a new `src/ingest/backfill_event_enrichment.py` (existing Supabase events). `src/ingest/migrate_to_supabase.py` is extended so tags/corrections produced by the local-JSON path actually reach Supabase instead of being dropped at migration.

**Tech Stack:** Python, pytest, Supabase (`supabase-py`), no new dependencies.

## Global Constraints

- Tag taxonomy is exactly these 20 values, defined once as `enrichment.TAG_TAXONOMY`: `military`, `politics`, `science`, `technology`, `exploration`, `space`, `arts`, `music`, `film`, `sports`, `religion`, `royalty`, `economics`, `law`, `disaster`, `health`, `social`, `education`, `philosophy`, `engineering`. No other tag names are ever written to the `tags`/`event_tags` tables or the local JSON.
- Every successfully-enriched event gets 1–3 tags. If a subagent returns more than 3 valid tags, keep only the first 3 in the order returned. If none of the returned tags are valid, the event gets 0 tags (not a fabricated one) plus a review-report entry.
- There is no code-level check for phrasing quality (grammar/"reads nicely") — only the existing structural fallback for a missing/blank `event_phrase`. Do not add heuristics that try to grade prose quality.
- A subject correction is only ever applied when **both** hold: the suggested name is literally present in the event's `text` (`core.matching.name_matches_text`), and it resolves to an entry in `data/top_1000_births.json` (matched via `core.matching.normalize_name`) with a computable, plausible age (`0 <= age_days <= 120 * 365`, same bound `ingest.pipeline.match_births_to_events` already uses). If either check fails, the event's subject is left untouched.
- Every validation failure (invalid tags, rejected subject correction, missing `event_phrase`) is recorded as an entry in an enrichment review report — never silently dropped.
- The reword prompt lives in `src/ingest/reword_prompt.md` (versioned, diffable) — not crafted live per batch run.

---

### Task 1: Tag taxonomy and tag validation

**Files:**
- Create: `src/ingest/enrichment.py`
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Produces: `enrichment.TAG_TAXONOMY: List[str]` (the 20 tags from Global Constraints, in the order listed there). `enrichment.validate_tags(raw_tags: List[str]) -> Tuple[List[str], Optional[str]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_enrichment.py
from ingest.enrichment import validate_tags


def test_validate_tags_keeps_valid_tags_case_insensitive():
    valid, reason = validate_tags(["Science", "MILITARY"])
    assert valid == ["science", "military"]
    assert reason is None


def test_validate_tags_drops_unknown_tags():
    valid, reason = validate_tags(["science", "not-a-real-tag"])
    assert valid == ["science"]
    assert reason is None


def test_validate_tags_caps_at_three_keeping_first_three():
    valid, reason = validate_tags(["science", "military", "law", "arts"])
    assert valid == ["science", "military", "law"]
    assert reason is None


def test_validate_tags_returns_reason_when_none_valid():
    valid, reason = validate_tags(["not-a-real-tag", "also-fake"])
    assert valid == []
    assert reason == "no valid tags in ['not-a-real-tag', 'also-fake']"


def test_validate_tags_handles_empty_input():
    valid, reason = validate_tags([])
    assert valid == []
    assert reason == "no valid tags in []"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_enrichment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.enrichment'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ingest/enrichment.py
"""Shared LLM-event-enrichment logic: tag taxonomy, prompt building, and
validation of what a reword subagent returns (tags, subject corrections).

Used by both ingest.llm_utils (new events, local JSON) and
ingest.backfill_event_enrichment (existing Supabase events), so the two
entry points can never disagree about what a valid tag or subject
correction looks like.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

TAG_TAXONOMY = [
    "military", "politics", "science", "technology", "exploration", "space", "arts", "music",
    "film", "sports", "religion", "royalty", "economics", "law", "disaster", "health", "social",
    "education", "philosophy", "engineering",
]


def validate_tags(raw_tags: List[str]) -> Tuple[List[str], Optional[str]]:
    """Filter LLM-returned tags against TAG_TAXONOMY.

    Case-insensitive, de-duplicated, capped at 3, keeping the first 3 valid
    tags in the order given. Returns (valid_tags, rejection_reason) - reason
    is None unless no tag in raw_tags survived filtering.
    """
    valid: List[str] = []
    seen = set()
    for tag in raw_tags or []:
        normalized = str(tag).strip().lower()
        if normalized in TAG_TAXONOMY and normalized not in seen:
            valid.append(normalized)
            seen.add(normalized)
        if len(valid) == 3:
            break

    if not valid:
        return [], f"no valid tags in {raw_tags!r}"
    return valid, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_enrichment.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/enrichment.py tests/test_enrichment.py
git commit -m "feat: add tag taxonomy and tag validation for event enrichment"
```

---

### Task 2: Births lookup and subject-correction resolution

**Files:**
- Modify: `src/ingest/enrichment.py`
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: `core.matching.name_matches_text(name: str, text: str) -> bool`, `core.matching.normalize_name(text: str) -> str`, `ingest.pipeline.calculate_age(birth_year, birth_month, birth_day, event_year, event_month, event_day) -> int | None`, `core.io.load_json`, `core.config.DATA_DIR`.
- Produces: `enrichment.load_births_lookup(path: Path = DATA_DIR / "top_1000_births.json") -> Dict[str, Dict]` (keys are `normalize_name(name)`, values are `{"name": str, "year": int, "month": int, "day": int}`). `enrichment.resolve_subject(event: Dict, suggested_name: Optional[str], births_lookup: Dict[str, Dict]) -> Tuple[Optional[Dict], Optional[str]]` — `event` must have `"text"`, `"year"`, `"month"`, `"day"` keys; on success the first tuple element is `{"name": str, "age_days": int}`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_enrichment.py
from ingest.enrichment import resolve_subject


def _births_lookup():
    return {
        "john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30},
    }


def test_resolve_subject_returns_none_none_when_no_suggestion():
    event = {"text": "George Washington hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, None, _births_lookup())
    assert correction is None
    assert reason is None


def test_resolve_subject_rejects_name_not_in_text():
    event = {"text": "George Washington hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, "John Adams", _births_lookup())
    assert correction is None
    assert reason == "suggested subject 'John Adams' not found in event text"


def test_resolve_subject_rejects_name_not_in_births_lookup():
    event = {"text": "George Washington and John Doe hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, "John Doe", _births_lookup())
    assert correction is None
    assert reason == "suggested subject 'John Doe' not in known births list"


def test_resolve_subject_returns_correction_when_valid():
    event = {"text": "George Washington and John Adams hoisted the flag", "year": 1776, "month": 1, "day": 1}
    correction, reason = resolve_subject(event, "John Adams", _births_lookup())
    assert reason is None
    assert correction == {"name": "John Adams", "age_days": correction["age_days"]}
    assert correction["age_days"] > 0


def test_load_births_lookup_indexes_by_normalized_name(tmp_path):
    from ingest.enrichment import load_births_lookup

    births_path = tmp_path / "births.json"
    births_path.write_text(
        '[{"name": "Ada Lovelace", "year": "1815", "month": "12", "day": "10"}]', encoding="utf-8"
    )
    lookup = load_births_lookup(births_path)
    assert lookup == {"ada lovelace": {"name": "Ada Lovelace", "year": 1815, "month": 12, "day": 10}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_enrichment.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_subject'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/ingest/enrichment.py, after the imports (extend the existing import block)
from pathlib import Path
from typing import Dict

from core.config import DATA_DIR
from core.io import load_json
from core.matching import name_matches_text, normalize_name
from ingest.pipeline import calculate_age

# ... (TAG_TAXONOMY and validate_tags stay as they are) ...


def load_births_lookup(path: Path = DATA_DIR / "top_1000_births.json") -> Dict[str, Dict]:
    """Load births data, indexed by normalize_name(name) for subject-correction lookups."""
    lookup: Dict[str, Dict] = {}
    for birth in load_json(path):
        try:
            name = birth["name"]
            lookup[normalize_name(name)] = {
                "name": name,
                "year": int(birth["year"]),
                "month": int(birth["month"]),
                "day": int(birth["day"]),
            }
        except (KeyError, ValueError, TypeError):
            continue
    return lookup


def resolve_subject(
    event: Dict,
    suggested_name: Optional[str],
    births_lookup: Dict[str, Dict],
) -> Tuple[Optional[Dict], Optional[str]]:
    """Validate an LLM-suggested subject correction for one event.

    `event` must have "text", "year", "month", "day" keys.

    Returns (correction, rejection_reason):
    - (None, None) if suggested_name is falsy (no correction requested).
    - (None, reason) if a correction was suggested but failed validation.
    - (correction, None) if valid, where correction is {"name": ..., "age_days": ...}.
    """
    if not suggested_name:
        return None, None

    if not name_matches_text(suggested_name, event["text"]):
        return None, f"suggested subject {suggested_name!r} not found in event text"

    birth = births_lookup.get(normalize_name(suggested_name))
    if birth is None:
        return None, f"suggested subject {suggested_name!r} not in known births list"

    age_days = calculate_age(
        birth["year"], birth["month"], birth["day"],
        int(event["year"]), int(event["month"]), int(event["day"]),
    )
    if age_days is None or not (0 <= age_days <= 120 * 365):
        return None, f"could not compute a valid age for {suggested_name!r}"

    return {"name": birth["name"], "age_days": age_days}, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_enrichment.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/enrichment.py tests/test_enrichment.py
git commit -m "feat: add births lookup and subject-correction validation"
```

---

### Task 3: Versioned prompt template

**Files:**
- Create: `src/ingest/reword_prompt.md`
- Modify: `src/ingest/enrichment.py`
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: `enrichment.TAG_TAXONOMY` (Task 1).
- Produces: `enrichment.build_prompt() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_enrichment.py
from ingest.enrichment import TAG_TAXONOMY, build_prompt


def test_build_prompt_substitutes_tag_list():
    prompt = build_prompt()
    assert "{tags}" not in prompt
    for tag in TAG_TAXONOMY:
        assert tag in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_enrichment.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_prompt'`

- [ ] **Step 3: Create the prompt template and implementation**

```markdown
<!-- src/ingest/reword_prompt.md -->
# Event rewording instructions

You will receive a JSON array of historical event records, each with:
- `name`: the person currently matched to this event
- `text`: the raw event description (as scraped from Wikipedia's "on this day" data)
- `year`, `month`, `day`: the event's date
- `age`: the person's age in days at the time of the event

For each record, return a JSON object with these fields:

- `name`: copied unchanged from the input.
- `text`: copied unchanged from the input.
- `event_phrase`: a rewording of `text` that reads as a natural continuation of the sentence
  "The same age that {name} was when ___." Rules:
  - Use a pronoun (he/she/they) instead of repeating `name`.
  - Keep it in past tense, matching "was when".
  - Preserve the original fact exactly — reword for flow, don't add or invent detail that
    isn't in `text`.
  - Strip any leftover Wikipedia-style artifacts from `text` (trailing citation brackets,
    "(b. ...)"/"(d. ...)" asides, etc.) that would read strangely mid-sentence.
  - Don't capitalize the first word unless it's a proper noun — it continues the sentence
    started by "The same age that {name} was when ".
- `tags`: an array of 1 to 3 tags from this fixed list only — do not invent new tags:
  {tags}
  Pick the tags that best describe what the event is about (e.g. a battle is `military`, a
  scientific paper is `science`, a court ruling is `law`).
- `suggested_subject`: usually `null`. Only set this if `text` mentions a *different* named
  person who is more clearly the grammatical subject of the sentence than `name` is (this
  happens when a description mentions multiple people and the wrong one got matched). If set,
  it must be a name copied verbatim as it appears in `text` — don't guess a full name that
  isn't actually written there.

Return a JSON array in the same order as the input, one object per input record. Return every
record you were given — don't skip any, even if `event_phrase` ends up being close to a direct
rewording of `text`.
```

```python
# add to src/ingest/enrichment.py, after the imports
PROMPT_TEMPLATE_PATH = Path(__file__).parent / "reword_prompt.md"


def build_prompt() -> str:
    """Read reword_prompt.md and substitute the {tags} placeholder with TAG_TAXONOMY."""
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("{tags}", ", ".join(TAG_TAXONOMY))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_enrichment.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/enrichment.py src/ingest/reword_prompt.md tests/test_enrichment.py
git commit -m "feat: add versioned reword prompt template"
```

---

### Task 4: Review report writer and tag-row builder

**Files:**
- Modify: `src/ingest/enrichment.py`
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: `core.io.load_json`, `core.io.save_to_json`.
- Produces: `enrichment.write_review_entries(entries: List[Dict], review_path: Path) -> None`. `enrichment.build_tag_rows(event_id: int, tags: List[str], tag_name_to_id: Dict[str, int]) -> List[Dict]` (each row `{"event_id": int, "tag_id": int}`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_enrichment.py
import json

from ingest.enrichment import build_tag_rows, write_review_entries


def test_write_review_entries_creates_file(tmp_path):
    review_path = tmp_path / "sub" / "review.json"
    write_review_entries([{"issue_type": "tags", "detail": "x"}], review_path)
    assert json.loads(review_path.read_text(encoding="utf-8")) == [{"issue_type": "tags", "detail": "x"}]


def test_write_review_entries_appends_to_existing(tmp_path):
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps([{"issue_type": "tags", "detail": "first"}]), encoding="utf-8")
    write_review_entries([{"issue_type": "subject", "detail": "second"}], review_path)
    result = json.loads(review_path.read_text(encoding="utf-8"))
    assert result == [
        {"issue_type": "tags", "detail": "first"},
        {"issue_type": "subject", "detail": "second"},
    ]


def test_write_review_entries_noop_on_empty_list(tmp_path):
    review_path = tmp_path / "review.json"
    write_review_entries([], review_path)
    assert not review_path.exists()


def test_build_tag_rows_maps_names_to_ids():
    rows = build_tag_rows(42, ["science", "military"], {"science": 5, "military": 9})
    assert rows == [{"event_id": 42, "tag_id": 5}, {"event_id": 42, "tag_id": 9}]


def test_build_tag_rows_skips_unknown_tag_names():
    rows = build_tag_rows(42, ["science", "not-seeded-yet"], {"science": 5})
    assert rows == [{"event_id": 42, "tag_id": 5}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_enrichment.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_review_entries'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/ingest/enrichment.py, after the imports (extend existing import block with save_to_json)
from core.io import load_json, save_to_json


def write_review_entries(entries: List[Dict], review_path: Path) -> None:
    """Append entries to the enrichment review report, creating it if missing."""
    if not entries:
        return
    try:
        existing = load_json(review_path)
    except FileNotFoundError:
        existing = []
    existing.extend(entries)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(review_path, existing)


def build_tag_rows(event_id: int, tags: List[str], tag_name_to_id: Dict[str, int]) -> List[Dict]:
    """Build event_tags insert rows for one event's validated tag names."""
    return [{"event_id": event_id, "tag_id": tag_name_to_id[tag]} for tag in tags if tag in tag_name_to_id]
```

Note: `Dict` needs to be imported from `typing` alongside the other typing imports already present in the file from Task 2.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_enrichment.py -v`
Expected: PASS (16 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/enrichment.py tests/test_enrichment.py
git commit -m "feat: add review-report writer and tag-row builder"
```

---

### Task 5: Seed tag taxonomy in Supabase

**Files:**
- Modify: `SUPABASE_SETUP.md`

**Interfaces:**
- Consumes: `enrichment.TAG_TAXONOMY` (Task 1) — the SQL below is the taxonomy's values written out as an INSERT statement; both must be kept in sync if the taxonomy ever changes.

- [ ] **Step 1: Insert a new numbered section after "3. Persons and event detail fields" and renumber subsequent sections**

Insert this new section (the existing `tags`/`event_tags` tables were already created in step 1 of this doc — this only seeds rows):

```markdown
## 4. LLM event enrichment (tags and subject corrections)

Run this in the SQL editor to seed the fixed tag taxonomy into the `tags` table (already created
in step 1) — the backfill script below assigns tags by name, so the rows need to exist first.
`on conflict (name) do nothing` makes this safe to run more than once.

```sql
insert into tags (name, color) values
    ('military', '#6B4226'),
    ('politics', '#1E3A8A'),
    ('science', '#0F766E'),
    ('technology', '#2563EB'),
    ('exploration', '#B45309'),
    ('space', '#312E81'),
    ('arts', '#A21CAF'),
    ('music', '#7C3AED'),
    ('film', '#BE123C'),
    ('sports', '#EA580C'),
    ('religion', '#A16207'),
    ('royalty', '#86198F'),
    ('economics', '#15803D'),
    ('law', '#334155'),
    ('disaster', '#B91C1C'),
    ('health', '#0D9488'),
    ('social', '#C2410C'),
    ('education', '#1D4ED8'),
    ('philosophy', '#4338CA'),
    ('engineering', '#57534E')
on conflict (name) do nothing;
```

Then run the one-off backfill (same environment/credentials as the earlier migrations):

```bash
python -c "from ingest.backfill_event_enrichment import prepare_chunks; print(prepare_chunks())"
```

This writes chunk files under `data/tmp/enrichment_chunks/`. Dispatch a Claude Haiku subagent per
chunk file, using `ingest.enrichment.build_prompt()` for instructions, and save each subagent's
JSON response next to its chunk as `<chunk>_result.json`. Then merge each chunk:

```bash
python -c "from ingest.backfill_event_enrichment import merge_chunk; merge_chunk('data/tmp/enrichment_chunks/chunk_0000.json', 'data/tmp/enrichment_chunks/chunk_0000_result.json')"
```

This re-checks each event's `event_phrase` wording, assigns 1-3 tags from the list above, and
flags cases where the matched name looks like the wrong subject. Tag assignments are written to
`event_tags`; subject corrections are only applied when the suggested alternate is both mentioned
in the event text and a known person with a computable birth date — anything that doesn't clear
that bar is written to `data/tmp/enrichment_review.json` for a manual look instead of being
guessed. The script is resumable: `prepare_chunks()` only includes events that don't already have
`event_tags` rows, so re-running the whole process after fixing something picks up where it left
off.
```

Renumber the existing sections that follow: `## 4. Configure secrets` → `## 5. Configure secrets`, `## 5. Install ntfy` → `## 6. Install ntfy`, `## 6. Test end-to-end` → `## 7. Test end-to-end`.

- [ ] **Step 2: Commit**

```bash
git add SUPABASE_SETUP.md
git commit -m "docs: document tag-seeding SQL and the event-enrichment backfill"
```

---

### Task 6: Wire tag/subject validation into the local-JSON reword merge

**Files:**
- Modify: `src/ingest/llm_utils.py`
- Modify: `tests/test_llm_utils.py`

**Interfaces:**
- Consumes: `enrichment.load_births_lookup`, `enrichment.resolve_subject`, `enrichment.validate_tags`, `enrichment.write_review_entries` (all from Tasks 2 and 4).
- Produces: `merge_reworded_chunk(chunk_path, result_path, displayable_path=..., births_path=DATA_DIR / "top_1000_births.json", review_path=DATA_DIR / "tmp" / "enrichment_review.json") -> int`. Records written to `displayable_path` now always include a `"tags": List[str]` key, and `"name"`/`"age"` are updated in place when a subject correction was accepted.

- [ ] **Step 1: Write the failing tests (replace the full contents of `tests/test_llm_utils.py`)**

```python
# tests/test_llm_utils.py
import json

from ingest.llm_utils import merge_reworded_chunk


def _empty_births_path(tmp_path):
    path = tmp_path / "births.json"
    path.write_text("[]", encoding="utf-8")
    return path


def test_merge_reworded_chunk_uses_result_and_falls_back(tmp_path):
    chunk = [
        {"name": "George Washington", "text": "hoisted the flag", "year": "1776", "month": 1, "day": 1, "age": 100},
        {"name": "Anton Chekhov", "text": "wrote a play", "year": "1904", "month": 1, "day": 17, "age": 200},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Only the first record comes back reworded; the second is missing entirely.
    result = [
        {**chunk[0], "event_phrase": "he hoisted the flag over Prospect Hill", "tags": ["military"]},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    assert merged_count == 2
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_name = {event["name"]: event for event in merged}

    assert by_name["George Washington"]["event_phrase"] == "he hoisted the flag over Prospect Hill"
    assert by_name["George Washington"]["tags"] == ["military"]
    assert by_name["Anton Chekhov"]["event_phrase"] == "wrote a play"
    assert by_name["Anton Chekhov"]["tags"] == []


def test_merge_reworded_chunk_falls_back_on_missing_result_file(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result_path = tmp_path / "does_not_exist.json"
    displayable_path = tmp_path / "displayable_events.json"

    merged_count = merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "published notes"
    assert merged[0]["tags"] == []


def test_merge_reworded_chunk_appends_to_existing_file(tmp_path):
    displayable_path = tmp_path / "displayable_events.json"
    displayable_path.write_text(
        json.dumps([{"name": "Existing Person", "text": "did something", "event_phrase": "already here", "tags": []}]),
        encoding="utf-8",
    )

    chunk = [{"name": "New Person", "text": "did something else", "year": "2000", "month": 1, "day": 1, "age": 10}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")
    result_path = tmp_path / "does_not_exist.json"

    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert len(merged) == 2
    assert {event["name"] for event in merged} == {"Existing Person", "New Person"}


def test_merge_reworded_chunk_applies_valid_subject_correction(tmp_path):
    chunk = [
        {
            "name": "George Washington",
            "text": "George Washington and John Adams hoisted the flag",
            "year": "1776",
            "month": 1,
            "day": 1,
            "age": 100,
        },
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {**chunk[0], "event_phrase": "he hoisted the flag", "tags": ["military"], "suggested_subject": "John Adams"},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "John Adams", "year": 1735, "month": 10, "day": 30}]), encoding="utf-8"
    )

    displayable_path = tmp_path / "displayable_events.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=births_path,
        review_path=tmp_path / "review.json",
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["name"] == "John Adams"
    assert merged[0]["age"] > 0


def test_merge_reworded_chunk_writes_review_entry_for_invalid_tags(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [{**chunk[0], "event_phrase": "she published her notes", "tags": ["not-a-real-tag"]}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=tmp_path / "displayable_events.json",
        births_path=_empty_births_path(tmp_path),
        review_path=review_path,
    )

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review == [
        {"name": "Ada Lovelace", "text": "published notes", "issue_type": "tags", "detail": "no valid tags in ['not-a-real-tag']"}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_utils.py -v`
Expected: FAIL — `merge_reworded_chunk()` doesn't yet accept `births_path`/`review_path`, and no `tags` key is written.

- [ ] **Step 3: Rewrite `merge_reworded_chunk` (replace the full contents of `src/ingest/llm_utils.py`)**

```python
"""LLM helpers used during data preparation.

Rewording (adding event_phrase, tags, and subject corrections to matched events)
is done by spawning Claude Haiku subagents from within a Claude Code session -
see prepare_reword_chunks and merge_reworded_chunk. This module has no
network/API-calling code itself; it only prepares chunk files for a subagent
to process (using ingest.enrichment.build_prompt for instructions) and merges
the result back in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from ingest.enrichment import load_births_lookup, resolve_subject, validate_tags, write_review_entries

CHUNK_SIZE = 100
CHUNK_DIR = DATA_DIR / "tmp" / "reword_chunks"
REVIEW_PATH = DATA_DIR / "tmp" / "enrichment_review.json"


def _event_key(event: Dict) -> Tuple[object, object]:
    """Natural key for an event: (name, text) - stable across pipeline reruns."""
    return (event.get("name"), event.get("text"))


def _fallback_event_phrase(event: Dict) -> str:
    """Deterministic event_phrase suffix used when a subagent can't produce usable output.

    Returns only the fragment that goes after "The same age that {name} was when " —
    that static prefix is built at display time by core.matching.full_sentence, not stored.
    """
    text = event.get("text", "") or ""
    return text[:1].lower() + text[1:] if text else text


def get_pending_events(
    events_path=DATA_DIR / "events_with_age.json",
    displayable_path=DATA_DIR / "displayable_events.json",
) -> Tuple[List[Dict], List[Dict]]:
    """Return (already_processed, pending) events, split by whether event_phrase exists."""
    all_events = load_json(events_path)
    try:
        processed = load_json(displayable_path)
    except FileNotFoundError:
        processed = []

    processed_keys = {_event_key(event) for event in processed}
    pending = [event for event in all_events if _event_key(event) not in processed_keys]
    return processed, pending


def prepare_reword_chunks(chunk_size: int = CHUNK_SIZE, max_events: int | None = None) -> List[Path]:
    """Split pending events into numbered chunk files for a subagent to process.

    Returns the chunk file paths. Each is a JSON array of event records still
    missing event_phrase. Dispatch instructions for the subagent should come
    from ingest.enrichment.build_prompt(), not be crafted ad hoc.
    """
    _, pending = get_pending_events()
    if max_events is not None:
        pending = pending[:max_events]

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        chunk = pending[start : start + chunk_size]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths


def merge_reworded_chunk(
    chunk_path,
    result_path,
    displayable_path=DATA_DIR / "displayable_events.json",
    births_path=DATA_DIR / "top_1000_births.json",
    review_path=REVIEW_PATH,
) -> int:
    """Merge a subagent's reworded chunk into displayable_path (default data/displayable_events.json).

    Records are matched back to the original chunk by (name, text), not by
    list order/position, so a subagent that drops or reorders a record is
    still handled correctly. Any record that doesn't come back with a usable
    event_phrase (missing result file, invalid JSON, or a blank field) gets
    the deterministic fallback template instead, with no tags and no subject
    correction attempted.

    Records that do come back get their tags validated against
    ingest.enrichment.TAG_TAXONOMY and any suggested_subject validated against
    the known births list (ingest.enrichment.resolve_subject) - anything that
    fails either check is recorded in review_path instead of applied. Returns
    how many records were merged.
    """
    chunk = load_json(chunk_path)

    reworded_by_key: Dict[Tuple[object, object], Dict] = {}
    try:
        reworded = load_json(result_path)
        reworded_by_key = {_event_key(event): event for event in reworded}
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    births_lookup = load_births_lookup(births_path)
    review_entries: List[Dict] = []
    merged: List[Dict] = []

    for event in chunk:
        result = reworded_by_key.get(_event_key(event))
        if not result or not result.get("event_phrase"):
            merged.append({**event, "event_phrase": _fallback_event_phrase(event), "tags": []})
            continue

        merged_event = {**event, "event_phrase": result["event_phrase"]}

        tags, tag_reason = validate_tags(result.get("tags") or [])
        merged_event["tags"] = tags
        if tag_reason:
            review_entries.append(
                {"name": event.get("name"), "text": event.get("text"), "issue_type": "tags", "detail": tag_reason}
            )

        correction, subject_reason = resolve_subject(event, result.get("suggested_subject"), births_lookup)
        if correction:
            merged_event["name"] = correction["name"]
            merged_event["age"] = correction["age_days"]
        if subject_reason:
            review_entries.append(
                {"name": event.get("name"), "text": event.get("text"), "issue_type": "subject", "detail": subject_reason}
            )

        merged.append(merged_event)

    write_review_entries(review_entries, review_path)

    try:
        existing = load_json(displayable_path)
    except FileNotFoundError:
        existing = []
    existing.extend(merged)
    save_to_json(displayable_path, existing)

    return len(merged)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_utils.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/llm_utils.py tests/test_llm_utils.py
git commit -m "feat: validate tags and subject corrections in the reword merge step"
```

---

### Task 7: Persist person_id and tags when migrating new events to Supabase

**Files:**
- Modify: `src/ingest/migrate_to_supabase.py`
- Create: `tests/test_migrate_to_supabase.py`

**Interfaces:**
- Consumes: `ingest.backfill_persons_and_phrases.build_person_rows(names: List[str]) -> List[Dict]` (existing), `ingest.enrichment.build_tag_rows` (Task 4).
- Produces: `_to_event_row(entry: Dict, name_to_person_id: Dict[str, int]) -> Dict` now includes a `"person_id"` key.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrate_to_supabase.py
from ingest.migrate_to_supabase import _to_event_row


def test_to_event_row_includes_person_id_and_age():
    entry = {
        "name": "Ada Lovelace",
        "text": "published notes",
        "event_phrase": "she published her notes",
        "year": "1843",
        "month": 1,
        "day": 1,
        "age": 12345,
    }
    row = _to_event_row(entry, {"Ada Lovelace": 7})
    assert row == {
        "name": "Ada Lovelace",
        "person_id": 7,
        "text": "published notes",
        "event_phrase": "she published her notes",
        "year": 1843,
        "month": 1,
        "day": 1,
        "age_days": 12345,
        "event_type": "achievement",
        "source": "initial_migration",
    }


def test_to_event_row_person_id_none_when_name_not_upserted_yet():
    entry = {
        "name": "Unknown Person",
        "text": "did something",
        "event_phrase": "did something",
        "year": "2000",
        "month": 1,
        "day": 1,
        "age": 1,
    }
    row = _to_event_row(entry, {})
    assert row["person_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_migrate_to_supabase.py -v`
Expected: FAIL — current `_to_event_row` takes one argument and returns no `"person_id"` key.

- [ ] **Step 3: Rewrite `migrate_to_supabase.py` (replace the full contents)**

```python
"""One-off script: load data/displayable_events.json into the Supabase events table.

Run by hand once the Supabase tables exist (see SUPABASE_SETUP.md):

    python -m ingest.migrate_to_supabase
"""

from __future__ import annotations

from typing import Dict, List

from core.config import DATA_DIR
from core.db import get_client
from core.io import load_json
from ingest.backfill_persons_and_phrases import build_person_rows
from ingest.enrichment import build_tag_rows

BATCH_SIZE = 200


def _to_event_row(entry: Dict, name_to_person_id: Dict[str, int]) -> Dict:
    return {
        "name": entry["name"],
        "person_id": name_to_person_id.get(entry["name"]),
        "text": entry["text"],
        "event_phrase": entry.get("event_phrase") or entry["display_text"],
        "year": int(entry["year"]),
        "month": int(entry["month"]),
        "day": int(entry["day"]),
        "age_days": int(entry["age"]),
        "event_type": "achievement",
        "source": "initial_migration",
    }


def main() -> None:
    entries: List[Dict] = load_json(DATA_DIR / "displayable_events.json")

    client = get_client()

    person_rows = build_person_rows([entry["name"] for entry in entries])
    persons = client.table("persons").upsert(person_rows, on_conflict="name").execute().data
    name_to_person_id = {person["name"]: person["id"] for person in persons}

    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}

    rows = [_to_event_row(entry, name_to_person_id) for entry in entries]

    inserted = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        batch_entries = entries[start : start + BATCH_SIZE]
        inserted_events = client.table("events").insert(batch).execute().data

        tag_rows: List[Dict] = []
        for inserted_event, entry in zip(inserted_events, batch_entries):
            tag_rows.extend(build_tag_rows(inserted_event["id"], entry.get("tags") or [], tag_name_to_id))
        if tag_rows:
            client.table("event_tags").insert(tag_rows).execute()

        inserted += len(batch)
        print(f"Inserted {inserted}/{len(rows)}")

    print(f"Done. Inserted {inserted} events.")


if __name__ == "__main__":  # pragma: no cover - manual helper
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_migrate_to_supabase.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/migrate_to_supabase.py tests/test_migrate_to_supabase.py
git commit -m "feat: persist person_id and tags when migrating new events to Supabase"
```

---

### Task 8: One-off Supabase backfill for existing events

**Files:**
- Create: `src/ingest/backfill_event_enrichment.py`
- Create: `tests/test_backfill_event_enrichment.py`

**Interfaces:**
- Consumes: `enrichment.load_births_lookup`, `enrichment.resolve_subject`, `enrichment.validate_tags`, `enrichment.write_review_entries`, `enrichment.build_tag_rows` (Tasks 1, 2, 4), `core.db.get_client`, `core.io.load_json`/`save_to_json`, `core.config.DATA_DIR`.
- Produces: `pending_events(all_events: List[Dict], tagged_event_ids: set) -> List[Dict]`. `resolve_event_update(event: Dict, result: Optional[Dict], births_lookup: Dict[str, Dict]) -> Tuple[Optional[Dict], List[str], List[Dict]]` — pure decision logic (no Supabase calls), returns `(events_table_update_or_None, valid_tags, review_entries)`. `prepare_chunks(chunk_size: int = 100) -> List[Path]`. `merge_chunk(chunk_path, result_path, review_path=REVIEW_PATH) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backfill_event_enrichment.py
from ingest.backfill_event_enrichment import pending_events, resolve_event_update


def test_pending_events_excludes_already_tagged():
    events = [{"id": 1}, {"id": 2}, {"id": 3}]
    result = pending_events(events, tagged_event_ids={2})
    assert result == [{"id": 1}, {"id": 3}]


def test_resolve_event_update_flags_missing_event_phrase():
    event = {"id": 1, "text": "did a thing", "year": 2000, "month": 1, "day": 1}
    update, tags, review_entries = resolve_event_update(event, result=None, births_lookup={})
    assert update is None
    assert tags == []
    assert review_entries == [
        {"event_id": 1, "issue_type": "reword", "detail": "no usable event_phrase returned"}
    ]


def test_resolve_event_update_applies_valid_tags_and_subject_correction():
    event = {"id": 2, "text": "George Washington and John Adams hoisted the flag", "year": 1776, "month": 1, "day": 1}
    result = {"event_phrase": "he hoisted the flag", "tags": ["military"], "suggested_subject": "John Adams"}
    births_lookup = {"john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30}}

    update, tags, review_entries = resolve_event_update(event, result, births_lookup)

    assert update["event_phrase"] == "he hoisted the flag"
    assert update["name"] == "John Adams"
    assert update["age_days"] > 0
    assert tags == ["military"]
    assert review_entries == []


def test_resolve_event_update_flags_invalid_tags_but_still_writes_phrase():
    event = {"id": 3, "text": "did a thing", "year": 2000, "month": 1, "day": 1}
    result = {"event_phrase": "he did a thing", "tags": ["not-a-real-tag"], "suggested_subject": None}

    update, tags, review_entries = resolve_event_update(event, result, births_lookup={})

    assert update == {"event_phrase": "he did a thing"}
    assert tags == []
    assert len(review_entries) == 1
    assert review_entries[0]["issue_type"] == "tags"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backfill_event_enrichment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.backfill_event_enrichment'`

- [ ] **Step 3: Write the implementation**

```python
# src/ingest/backfill_event_enrichment.py
"""One-off enrichment backfill: re-process every Supabase event through the shared
reword/tag/subject-check prompt (ingest.enrichment), for events that don't already
have tags.

Two-phase, like ingest.llm_utils, because a Claude Code subagent has to run in
between the two calls - see SUPABASE_SETUP.md section 4 for the full sequence:

    python -c "from ingest.backfill_event_enrichment import prepare_chunks; prepare_chunks()"
    # ... dispatch a Haiku subagent per chunk file, using ingest.enrichment.build_prompt() ...
    python -c "from ingest.backfill_event_enrichment import merge_chunk; merge_chunk('data/tmp/enrichment_chunks/chunk_0000.json', 'data/tmp/enrichment_chunks/chunk_0000_result.json')"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.config import DATA_DIR
from core.db import get_client
from core.io import load_json, save_to_json
from ingest.enrichment import build_tag_rows, load_births_lookup, resolve_subject, validate_tags, write_review_entries

CHUNK_SIZE = 100
CHUNK_DIR = DATA_DIR / "tmp" / "enrichment_chunks"
REVIEW_PATH = DATA_DIR / "tmp" / "enrichment_review.json"

EVENTS_PAGE_SIZE = 1000


def _fetch_all_events(client) -> List[Dict]:
    """Page through every event's id/name/text/year/month/day (Supabase caps a page at ~1000 rows)."""
    events: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("id, name, text, year, month, day")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        events.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return events


def _fetch_tagged_event_ids(client) -> Set[int]:
    """Every event_id that already has at least one row in event_tags."""
    response = client.table("event_tags").select("event_id").execute()
    return {row["event_id"] for row in response.data}


def pending_events(all_events: List[Dict], tagged_event_ids: Set[int]) -> List[Dict]:
    """Events with no event_tags rows yet - safe to call repeatedly (resumable backfill)."""
    return [event for event in all_events if event["id"] not in tagged_event_ids]


def prepare_chunks(chunk_size: int = CHUNK_SIZE) -> List[Path]:
    """Fetch pending events from Supabase and split them into numbered chunk files."""
    client = get_client()
    pending = pending_events(_fetch_all_events(client), _fetch_tagged_event_ids(client))

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        chunk = pending[start : start + chunk_size]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths


def resolve_event_update(
    event: Dict,
    result: Optional[Dict],
    births_lookup: Dict[str, Dict],
) -> Tuple[Optional[Dict], List[str], List[Dict]]:
    """Pure decision logic for one event: what to write, and what to flag for review.

    Returns (events_update_or_none, valid_tags, review_entries). events_update_or_none
    is None when no usable event_phrase came back (nothing to write for this event).
    """
    review_entries: List[Dict] = []

    if not result or not result.get("event_phrase"):
        review_entries.append(
            {"event_id": event["id"], "issue_type": "reword", "detail": "no usable event_phrase returned"}
        )
        return None, [], review_entries

    tags, tag_reason = validate_tags(result.get("tags") or [])
    if tag_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "tags", "detail": tag_reason})

    correction, subject_reason = resolve_subject(event, result.get("suggested_subject"), births_lookup)
    if subject_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "subject", "detail": subject_reason})

    update: Dict = {"event_phrase": result["event_phrase"]}
    if correction:
        update["name"] = correction["name"]
        update["age_days"] = correction["age_days"]

    return update, tags, review_entries


def merge_chunk(chunk_path, result_path, review_path: Path = REVIEW_PATH) -> int:
    """Validate one chunk's subagent output and write event/event_tags updates to Supabase.

    Returns how many events in the chunk were processed (written or flagged for review).
    """
    chunk = load_json(chunk_path)
    try:
        reworded = load_json(result_path)
    except (FileNotFoundError, json.JSONDecodeError):
        reworded = []
    reworded_by_id = {event["id"]: event for event in reworded if "id" in event}

    client = get_client()
    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}
    births_lookup = load_births_lookup()

    all_review_entries: List[Dict] = []
    for event in chunk:
        result = reworded_by_id.get(event["id"])
        update, tags, review_entries = resolve_event_update(event, result, births_lookup)
        all_review_entries.extend(review_entries)

        if update is None:
            continue

        client.table("events").update(update).eq("id", event["id"]).execute()

        tag_rows = build_tag_rows(event["id"], tags, tag_name_to_id)
        if tag_rows:
            client.table("event_tags").insert(tag_rows).execute()

    write_review_entries(all_review_entries, review_path)
    return len(chunk)


if __name__ == "__main__":  # pragma: no cover - manual, requires a subagent in between
    paths = prepare_chunks()
    print(f"Wrote {len(paths)} chunk file(s) to {CHUNK_DIR}.")
    print("Dispatch a Haiku subagent per chunk (prompt: ingest.enrichment.build_prompt()),")
    print("write each result to <chunk>_result.json, then call merge_chunk() per chunk.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_event_enrichment.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/backfill_event_enrichment.py tests/test_backfill_event_enrichment.py
git commit -m "feat: add one-off Supabase backfill for tags and subject corrections"
```

---

### Task 9: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: All tests pass, including the pre-existing `test_age.py`, `test_backfill_persons_and_phrases.py`, `test_db.py`, `test_matching.py` alongside every test added in Tasks 1-8.

- [ ] **Step 2: Confirm no stray files**

Run: `git status`
Expected: Clean working tree (everything from Tasks 1-8 already committed). If anything is untracked
(e.g. a leftover `data/tmp/enrichment_chunks/` from manually exercising the scripts), remove it —
those are runtime artifacts, not part of the feature.
