# Nobel Prize Laureate Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest all 990 Nobel Prize laureates (995 awards, 1901-2025) from `data/raw_data/nobel_prizes_1901-2025_cleaned.csv` into the `events`/`persons` tables, reusing the existing Wikidata-resolution and LLM-phrasing machinery, while guarding against duplicating events already scraped from Wikipedia.

**Architecture:** A pure parsing module (`ingest.sources.nobel`) turns CSV rows into canonical records. A resolution step (`ingest.resolve_nobel_wikidata`) fills in birth dates for the 243 laureates missing them, reusing `ingest.sources.wikidata` unchanged. `ingest.migrate_nobel_to_supabase` resolves each record's `person_id`, guards against re-adding an event Wikipedia's "on this day" corpus already has (same person, same exact date), and computes `age_days`. A dedicated phrasing pass (`ingest.nobel_llm_utils`, its own prompt) produces the display sentence via the same chunk/dispatch/merge shape the existing corpus already uses, with tags assigned deterministically rather than LLM-chosen. `ingest.backfill_event_enrichment`'s generic re-phrasing backfill is patched to exclude Nobel-sourced rows so a future prompt revision there can't sweep them up.

**Tech Stack:** Python 3.13/3.14 (repo venv), pytest, `supabase-py` (PostgREST), `requests` (Wikidata API).

## Global Constraints

- Run all commands from the repo root with the project venv: `./venv/Scripts/python.exe`.
- Test command: `./venv/Scripts/python.exe -m pytest -q`. Baseline before this plan: **309 passed**.
- `pyproject.toml` sets `pythonpath = ["src"]`, so tests import `core.*`, `ingest.*` directly.
- Source CSV: `data/raw_data/nobel_prizes_1901-2025_cleaned.csv`. First header (`award_year`) carries a BOM — read with `encoding="utf-8-sig"`, matching `core.io.load_json`'s existing convention.
- `date_awarded` is always `MM/DD/YYYY`. `birth_date` is **either** `YYYY-MM-DD` (295 rows) **or** `MM/DD/YYYY` (457 rows) depending on the row, or blank (243 rows) — confirmed against real laureates' known birthdates (e.g. Enrico Fermi, `9/29/1901` → September 29, 1901). Blank `birth_date` means missing entirely, not zero.
- **`(laureate_id, award_year)` is the unique key for a CSV row** — verified against the live file (0 duplicates). `laureate_id` alone is **not** row-unique: it identifies the *person* and repeats across a repeat winner's rows (John Bardeen, Frederick Sanger, and K. Barry Sharpless each won the **same category twice**, in different years — `(laureate_id, category)` would collide for all three).
- `NOBEL_CATEGORY_TAGS` (deterministic, never LLM-chosen): `Physics`→`science`, `Chemistry`→`science`, `Physiology or Medicine`→`health`, `Literature`→`arts`, `Peace`→`politics`, `Economic Sciences`→`economics`.
- `Economic Sciences` displays as `"the Nobel Memorial Prize in Economic Sciences"`; `Peace` displays as `"the Nobel Peace Prize"`; every other category displays as `"the Nobel Prize in {category}"`.
- `events.event_type` is `"achievement"` (existing value, no schema constraint). `events.source` is `"nobel_prize_dataset"` — exported as `NOBEL_SOURCE` from `ingest.sources.nobel`, the single source of truth other modules import.
- Age plausibility bound: `0 <= age_days <= MAX_AGE_DAYS` where `MAX_AGE_DAYS = ingest.match_events.MAX_AGE_DAYS` (`120 * 365`) — reused, not redefined.
- `ingest.pipeline.calculate_age(birth_year, birth_month, birth_day, event_year, event_month, event_day) -> int | None` computes age in days; returns `None` on an invalid date.
- Chunk size for LLM phrasing batches: `100`, matching `ingest.llm_utils.CHUNK_SIZE`.
- Commit messages use conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `test:`) and end with:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
- Spec: `docs/superpowers/specs/2026-08-28-nobel-prize-ingestion-design.md`.

## File Structure

| File | Responsibility |
|---|---|
| `src/ingest/sources/nobel.py` (create) | Pure CSV parsing, no network/DB. `NOBEL_SOURCE`, `NOBEL_CATEGORY_TAGS`, `category_display_name`, `build_event_text`, `load_nobel_records`, `split_by_birth_data`. |
| `src/ingest/resolve_nobel_wikidata.py` (create) | Resolves the 243 birth-date-missing records via `ingest.sources.wikidata`, reusing its cache. |
| `src/ingest/backfill_event_enrichment.py` (modify) | `pending_phrasing_events` excludes `source == NOBEL_SOURCE` rows so a future generic reword-prompt bump can't sweep up Nobel rows. |
| `src/ingest/nobel_reword_prompt.md` (create) | The Nobel-specific LLM prompt: `{name, event_phrase}` only — no tags, no subject correction. |
| `src/ingest/nobel_llm_utils.py` (create) | Chunk/dispatch/merge for the phrasing pass, mirroring `ingest.llm_utils`'s shape. Tags assigned from `NOBEL_CATEGORY_TAGS`, never from the LLM. |
| `src/ingest/migrate_nobel_to_supabase.py` (create) | Person resolution (upsert + near-duplicate-name detection), the duplicate-day guard, `wikipedia_url` backfill, age computation, and the final `events`/`event_tags` insert. Two phases in one module (mirrors `ingest.migrate_to_supabase`'s single-module shape). |
| `tests/test_nobel.py` (create) | Task 1 tests. |
| `tests/test_resolve_nobel_wikidata.py` (create) | Task 2 tests. |
| `tests/test_backfill_event_enrichment.py` (modify) | Task 3 tests (extends existing file). |
| `tests/test_nobel_llm_utils.py` (create) | Task 4 tests. |
| `tests/test_migrate_nobel_to_supabase.py` (create) | Tasks 5-6 tests. |

---

### Task 1: CSV parsing (`ingest.sources.nobel`)

**Files:**
- Create: `src/ingest/sources/nobel.py`
- Test: `tests/test_nobel.py`

**Interfaces:**
- Consumes: nothing (pure parsing).
- Produces:
  - `NOBEL_SOURCE: str = "nobel_prize_dataset"`
  - `NOBEL_CATEGORY_TAGS: Dict[str, str]`
  - `NOBEL_CSV_PATH: Path`
  - `category_display_name(category: str) -> str`
  - `build_event_text(record: Dict) -> str`
  - `load_nobel_records(csv_path: Path = NOBEL_CSV_PATH) -> List[Dict]` — each record: `{"laureate_id": str, "name": str, "category": str, "award_year": int, "award_month": int, "award_day": int, "motivation": str, "wikipedia_url": Optional[str], "birth_year": Optional[int], "birth_month": Optional[int], "birth_day": Optional[int]}`
  - `split_by_birth_data(records: List[Dict]) -> Tuple[List[Dict], List[Dict]]` — `(with_birth_date, missing_birth_date)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_nobel.py`:

```python
import csv
import json

from ingest.sources.nobel import (
    NOBEL_CATEGORY_TAGS,
    NOBEL_SOURCE,
    build_event_text,
    category_display_name,
    load_nobel_records,
    split_by_birth_data,
)

_HEADER = "award_year,date_awarded,laureate_id,known_name,category,motivation,birth_date,wikipedia_url"


def _write_csv(tmp_path, rows, bom=True):
    lines = [_HEADER] + rows
    content = "\n".join(lines)
    path = tmp_path / "nobel.csv"
    # The real export carries a BOM on the first header; utf-8-sig strips it.
    path.write_text(("﻿" if bom else "") + content, encoding="utf-8")
    return path


def test_load_nobel_records_parses_iso_birth_date(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            '1911,12/10/1911,6,Marie Curie,Chemistry,"in recognition of...",1867-11-07,https://en.wikipedia.org/wiki/Marie_Curie'
        ],
    )
    records = load_nobel_records(path)
    assert len(records) == 1
    record = records[0]
    assert record["laureate_id"] == "6"
    assert record["name"] == "Marie Curie"
    assert record["category"] == "Chemistry"
    assert (record["award_year"], record["award_month"], record["award_day"]) == (1911, 12, 10)
    assert (record["birth_year"], record["birth_month"], record["birth_day"]) == (1867, 11, 7)
    assert record["wikipedia_url"] == "https://en.wikipedia.org/wiki/Marie_Curie"


def test_load_nobel_records_parses_us_format_birth_date(tmp_path):
    # Real row: Enrico Fermi, born September 29 1901 - not the 9th of an invalid
    # 29th month. Confirms MM/DD/YYYY, matching date_awarded's confirmed format.
    path = _write_csv(
        tmp_path,
        ['1938,11/10/1938,66,Enrico Fermi,Physics,"for his work",9/29/1901,'],
    )
    record = load_nobel_records(path)[0]
    assert (record["birth_year"], record["birth_month"], record["birth_day"]) == (1901, 9, 29)


def test_load_nobel_records_treats_blank_birth_date_as_missing(tmp_path):
    path = _write_csv(
        tmp_path,
        ['1973,10/17/1973,394,Henry Kissinger,Peace,"for negotiating peace",,'],
    )
    record = load_nobel_records(path)[0]
    assert record["birth_year"] is None
    assert record["birth_month"] is None
    assert record["birth_day"] is None
    assert record["wikipedia_url"] is None


def test_load_nobel_records_strips_the_bom_from_the_first_header(tmp_path):
    path = _write_csv(
        tmp_path,
        ['1921,11/9/1922,26,Albert Einstein,Physics,"for his services",1879-03-14,'],
        bom=True,
    )
    # Would raise KeyError on "award_year" if the BOM leaked into the header name.
    record = load_nobel_records(path)[0]
    assert record["award_year"] == 1922


def test_split_by_birth_data_separates_thin_records():
    records = [
        {"name": "Has Birth", "birth_year": 1900, "birth_month": 1, "birth_day": 1},
        {"name": "No Birth", "birth_year": None, "birth_month": None, "birth_day": None},
    ]
    with_birth, missing = split_by_birth_data(records)
    assert [r["name"] for r in with_birth] == ["Has Birth"]
    assert [r["name"] for r in missing] == ["No Birth"]


def test_category_display_name_special_cases_economic_sciences():
    assert category_display_name("Economic Sciences") == "the Nobel Memorial Prize in Economic Sciences"
    assert category_display_name("Physics") == "the Nobel Prize in Physics"
    assert category_display_name("Peace") == "the Nobel Prize in Peace"


def test_nobel_category_tags_covers_every_real_category():
    # Every category actually present in the CSV must have a tag mapping -
    # a missing entry would KeyError deep in the merge/insert pipeline.
    assert set(NOBEL_CATEGORY_TAGS) == {
        "Physics", "Chemistry", "Physiology or Medicine", "Literature", "Peace", "Economic Sciences",
    }
    assert NOBEL_CATEGORY_TAGS["Peace"] == "politics"
    assert NOBEL_CATEGORY_TAGS["Physiology or Medicine"] == "health"


def test_build_event_text_uses_the_category_display_name():
    record = {"name": "Marie Curie", "category": "Chemistry", "motivation": "in recognition of her work"}
    assert build_event_text(record) == (
        'Marie Curie won the Nobel Prize in Chemistry: "in recognition of her work"'
    )


def test_build_event_text_special_cases_economic_sciences():
    record = {"name": "Milton Friedman", "category": "Economic Sciences", "motivation": "for his achievements"}
    assert build_event_text(record) == (
        'Milton Friedman won the Nobel Memorial Prize in Economic Sciences: "for his achievements"'
    )


def test_nobel_source_constant():
    assert NOBEL_SOURCE == "nobel_prize_dataset"


def test_load_nobel_records_against_the_real_csv_has_no_duplicate_row_keys():
    # Regression test for the (laureate_id, award_year) uniqueness this plan
    # relies on for keying LLM chunk records - verified against the live file
    # during design (John Bardeen, Frederick Sanger, and K. Barry Sharpless each
    # won the SAME category twice in different years, so (laureate_id, category)
    # would NOT be unique - only (laureate_id, award_year) is).
    from ingest.sources.nobel import NOBEL_CSV_PATH

    records = load_nobel_records(NOBEL_CSV_PATH)
    keys = [(r["laureate_id"], r["award_year"]) for r in records]
    assert len(keys) == len(set(keys))
    assert len(records) == 995
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_nobel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.sources.nobel'`

- [ ] **Step 3: Write the implementation**

Create `src/ingest/sources/nobel.py`:

```python
"""Parsing for the Nobel Prize laureate dataset - a structured source where the
subject is already named, unlike the scraped-text corpus ingest.match_events
resolves. See docs/superpowers/specs/2026-08-28-nobel-prize-ingestion-design.md.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR

NOBEL_CSV_PATH = DATA_DIR / "raw_data" / "nobel_prizes_1901-2025_cleaned.csv"

#: Provenance label written to events.source - the single source of truth other
#: modules (nobel_llm_utils, migrate_nobel_to_supabase, backfill_event_enrichment)
#: import, so the label can never drift between them.
NOBEL_SOURCE = "nobel_prize_dataset"

#: Deterministic category -> tag mapping. Never asked of the LLM: a Nobel
#: category is a closed, known fact, and letting a subagent re-derive the tag
#: would risk disagreeing with this table for no better information than it
#: already has (see the design spec's "Tags: deterministic, not LLM-chosen").
NOBEL_CATEGORY_TAGS: Dict[str, str] = {
    "Physics": "science",
    "Chemistry": "science",
    "Physiology or Medicine": "health",
    "Literature": "arts",
    "Peace": "politics",
    "Economic Sciences": "economics",
}

_ECONOMIC_SCIENCES_DISPLAY = "the Nobel Memorial Prize in Economic Sciences"


def category_display_name(category: str) -> str:
    """The prize name as it reads in a sentence.

    Economic Sciences is officially the Nobel Memorial Prize in Economic
    Sciences in Memory of Alfred Nobel, not one of the five prizes established
    in Nobel's 1895 will - the other five categories read as "the Nobel Prize
    in {category}".
    """
    if category == "Economic Sciences":
        return _ECONOMIC_SCIENCES_DISPLAY
    return f"the Nobel Prize in {category}"


def build_event_text(record: Dict) -> str:
    """The deterministic raw-fact sentence stored in events.text.

    Plays the same role the scraped Wikipedia sentence plays for the rest of
    the corpus: a stable source-of-truth string that check_facts_preserved
    checks the LLM-written event_phrase against.
    """
    return f'{record["name"]} won {category_display_name(record["category"])}: "{record["motivation"]}"'


def _parse_date_awarded(value: str) -> Tuple[int, int, int]:
    """date_awarded is always MM/DD/YYYY."""
    month, day, year = value.split("/")
    return int(year), int(month), int(day)


def _parse_birth_date(value: str) -> Optional[Tuple[int, int, int]]:
    """birth_date is YYYY-MM-DD for some rows and MM/DD/YYYY for others, or blank.

    Both formats coexist in the real export (295 ISO rows, 457 US-format rows).
    Confirmed against real laureates' known birthdates that the non-ISO rows
    are MM/DD/YYYY, matching date_awarded's format, not DD/MM/YYYY.
    """
    value = value.strip()
    if not value:
        return None
    if "-" in value:
        year, month, day = value.split("-")
        return int(year), int(month), int(day)
    month, day, year = value.split("/")
    return int(year), int(month), int(day)


def load_nobel_records(csv_path: Path = NOBEL_CSV_PATH) -> List[Dict]:
    """Load every row of the Nobel CSV into a canonical record dict.

    utf-8-sig strips the BOM the export leaves on the first header
    (award_year), matching core.io.load_json's existing convention for the
    same issue.
    """
    records: List[Dict] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            award_year, award_month, award_day = _parse_date_awarded(row["date_awarded"])
            birth = _parse_birth_date(row["birth_date"])
            records.append(
                {
                    "laureate_id": row["laureate_id"],
                    "name": row["known_name"],
                    "category": row["category"],
                    "award_year": award_year,
                    "award_month": award_month,
                    "award_day": award_day,
                    "motivation": row["motivation"],
                    "wikipedia_url": row["wikipedia_url"] or None,
                    "birth_year": birth[0] if birth else None,
                    "birth_month": birth[1] if birth else None,
                    "birth_day": birth[2] if birth else None,
                }
            )
    return records


def split_by_birth_data(records: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """(with_birth_date, missing_birth_date) - the latter needs Wikidata resolution."""
    with_birth_date = [r for r in records if r["birth_year"] is not None]
    missing_birth_date = [r for r in records if r["birth_year"] is None]
    return with_birth_date, missing_birth_date
```

Create `src/ingest/sources/__init__.py` if it does not already export this module (it already exists per the existing `sources/pantheon.py`, `sources/wikidata.py`, `sources/wikipedia.py` - no change needed there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_nobel.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/sources/nobel.py tests/test_nobel.py
git commit -m "$(cat <<'EOF'
feat: parse the Nobel Prize laureate CSV into canonical records

Handles the export's two birth_date formats (ISO and US, confirmed
against real laureates' known birthdates) and its BOM-prefixed first
header. NOBEL_CATEGORY_TAGS and category_display_name encode the
deterministic category-to-tag/display-name mapping the rest of the
pipeline reuses.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Wikidata resolution for the 243 thin records (`ingest.resolve_nobel_wikidata`)

**Files:**
- Create: `src/ingest/resolve_nobel_wikidata.py`
- Test: `tests/test_resolve_nobel_wikidata.py`

**Interfaces:**
- Consumes: `ingest.sources.wikidata.lookup_birth_date(name: str, event_year: Optional[int], cache: Dict) -> Dict` (existing, returns `{"status": ..., "year"/"month"/"day" on "resolved"}`), `ingest.enrichment.write_review_entries(entries: List[Dict], review_path: Path) -> None` (existing).
- Produces:
  - `REVIEW_PATH: Path`
  - `resolve_missing_birth_dates(records: List[Dict], cache: Dict, review_path: Path = REVIEW_PATH) -> List[Dict]` — returns only the successfully resolved records, each with `birth_year`/`birth_month`/`birth_day` filled in.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resolve_nobel_wikidata.py`:

```python
import json

from ingest import resolve_nobel_wikidata
from ingest.sources import wikidata


def _record(**overrides):
    base = {
        "laureate_id": "234", "name": "Hans Bethe", "category": "Physics",
        "award_year": 1967, "award_month": 10, "award_day": 30,
        "motivation": "for his contributions", "wikipedia_url": None,
        "birth_year": None, "birth_month": None, "birth_day": None,
    }
    return {**base, **overrides}


def test_resolved_lookup_fills_in_the_birth_date(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1906, "month": 7, "day": 2, "qid": "Q57181",
        },
    )
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates(
        [_record()], cache={}, review_path=tmp_path / "review.json"
    )
    assert len(resolved) == 1
    assert (resolved[0]["birth_year"], resolved[0]["birth_month"], resolved[0]["birth_day"]) == (1906, 7, 2)
    assert resolved[0]["name"] == "Hans Bethe"
    assert not (tmp_path / "review.json").exists()


def test_ambiguous_lookup_is_excluded_and_logged_to_review(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata, "lookup_birth_date", lambda name, event_year, cache: {"status": "ambiguous", "name": name}
    )
    review_path = tmp_path / "review.json"
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates([_record()], cache={}, review_path=review_path)

    assert resolved == []
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "ambiguous"
    assert review[0]["name"] == "Hans Bethe"


def test_not_found_lookup_is_excluded_and_logged_to_review(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata, "lookup_birth_date", lambda name, event_year, cache: {"status": "not_found", "name": name}
    )
    review_path = tmp_path / "review.json"
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates([_record()], cache={}, review_path=review_path)

    assert resolved == []
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "not_found"


def test_insufficient_precision_is_excluded_and_logged_to_review(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {"status": "insufficient_precision", "name": name},
    )
    review_path = tmp_path / "review.json"
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates([_record()], cache={}, review_path=review_path)

    assert resolved == []
    assert json.loads(review_path.read_text(encoding="utf-8"))[0]["issue_type"] == "insufficient_precision"


def test_passes_the_award_year_to_the_lookup_for_plausibility_filtering(monkeypatch, tmp_path):
    seen_years = []

    def fake_lookup(name, event_year, cache):
        seen_years.append(event_year)
        return {"status": "not_found", "name": name}

    monkeypatch.setattr(wikidata, "lookup_birth_date", fake_lookup)
    resolve_nobel_wikidata.resolve_missing_birth_dates(
        [_record(award_year=1967)], cache={}, review_path=tmp_path / "review.json"
    )
    assert seen_years == [1967]


def test_resolves_multiple_records_independently(monkeypatch, tmp_path):
    def fake_lookup(name, event_year, cache):
        if name == "Resolvable Person":
            return {"status": "resolved", "name": name, "year": 1900, "month": 1, "day": 1, "qid": "Q1"}
        return {"status": "not_found", "name": name}

    monkeypatch.setattr(wikidata, "lookup_birth_date", fake_lookup)
    records = [_record(name="Resolvable Person"), _record(name="Unresolvable Person")]
    resolved = resolve_nobel_wikidata.resolve_missing_birth_dates(
        records, cache={}, review_path=tmp_path / "review.json"
    )
    assert [r["name"] for r in resolved] == ["Resolvable Person"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_resolve_nobel_wikidata.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.resolve_nobel_wikidata'`

- [ ] **Step 3: Write the implementation**

Create `src/ingest/resolve_nobel_wikidata.py`:

```python
"""Resolve birth dates for Nobel laureates whose CSV row has none (243 of 995).

Run after ingest.sources.nobel.split_by_birth_data has produced the
missing_birth_date list:

    python -m ingest.resolve_nobel_wikidata

Safe to rerun: every lookup outcome is cached by name in the same on-disk
cache ingest.sources.wikidata already uses for the historical corpus's Stage
3, so a second run makes no network requests for names already attempted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from core.config import DATA_DIR
from core.io import save_to_json
from ingest.enrichment import write_review_entries
from ingest.sources import wikidata
from ingest.sources.nobel import NOBEL_CSV_PATH, load_nobel_records, split_by_birth_data

REVIEW_PATH = DATA_DIR / "tmp" / "nobel_wikidata_review.json"
OUTPUT_PATH = DATA_DIR / "tmp" / "nobel_resolved_thin.json"


def resolve_missing_birth_dates(
    records: List[Dict], cache: Dict, review_path: Path = REVIEW_PATH
) -> List[Dict]:
    """Resolve each record's birth date via Wikidata, returning only the resolved ones.

    Unresolved records (ambiguous/not_found/insufficient_precision) are written
    to review_path and excluded from the return value - not blocking the run,
    rerunnable later once the cache or the source data improves.
    """
    resolved: List[Dict] = []
    review_entries: List[Dict] = []

    for record in records:
        result = wikidata.lookup_birth_date(record["name"], record["award_year"], cache)
        status = result["status"]
        if status != "resolved":
            review_entries.append(
                {
                    "stage": "nobel_wikidata",
                    "issue_type": status,
                    "name": record["name"],
                    "detail": f"Wikidata lookup for {record['name']!r} (award_year={record['award_year']}) returned {status}",
                }
            )
            continue
        resolved.append(
            {**record, "birth_year": result["year"], "birth_month": result["month"], "birth_day": result["day"]}
        )

    write_review_entries(review_entries, review_path)
    return resolved


def run(
    csv_path: Path = NOBEL_CSV_PATH,
    output_path: Path = OUTPUT_PATH,
    cache_path: Path = wikidata.CACHE_PATH,
    review_path: Path = REVIEW_PATH,
) -> Dict[str, int]:
    """Load the CSV, resolve the missing-birth-date rows, write survivors to output_path."""
    records = load_nobel_records(csv_path)
    _, missing = split_by_birth_data(records)

    cache = wikidata.load_cache(cache_path)
    resolved = resolve_missing_birth_dates(missing, cache, review_path)
    wikidata.save_cache(cache, cache_path)

    save_to_json(output_path, resolved)
    return {"missing": len(missing), "resolved": len(resolved)}


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_resolve_nobel_wikidata.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/resolve_nobel_wikidata.py tests/test_resolve_nobel_wikidata.py
git commit -m "$(cat <<'EOF'
feat: resolve Nobel laureates missing a birth date via Wikidata

Reuses ingest.sources.wikidata's existing cache and lookup unchanged
for the 243 CSV rows with no birth_date. Unresolved names are logged
to review rather than blocking the rest of the batch.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Exclude Nobel rows from the generic reword backfill (`ingest.backfill_event_enrichment`)

**Files:**
- Modify: `src/ingest/backfill_event_enrichment.py:40-56,88-94`
- Test: `tests/test_backfill_event_enrichment.py` (extend)

**Interfaces:**
- Consumes: `ingest.sources.nobel.NOBEL_SOURCE` (from Task 1).
- Produces: `pending_phrasing_events` (existing signature unchanged) now also filters out `source == NOBEL_SOURCE` rows.

**Why this task is needed:** `backfill_event_enrichment.py`'s `mode="phrasing"` selects every event below `REWORD_PROMPT_VERSION` and re-words it with the *generic* `reword_prompt.md`, which asks the LLM to choose tags freely. Nobel rows are written under their own prompt and their own version counter in the same `reword_prompt_version` column (Task 4). Left unguarded, a future bump to `REWORD_PROMPT_VERSION` would sweep Nobel rows into the generic pass, rewording them with a prompt built for different input and silently overwriting the deterministic `NOBEL_CATEGORY_TAGS` mapping.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backfill_event_enrichment.py` (near the existing `test_pending_phrasing_events_selects_rows_below_the_current_version`):

```python
def test_pending_phrasing_events_excludes_nobel_sourced_rows():
    from ingest.sources.nobel import NOBEL_SOURCE

    events = [
        {"id": 1, "reword_prompt_version": 0, "source": "initial_migration"},
        {"id": 2, "reword_prompt_version": 0, "source": NOBEL_SOURCE},
        {"id": 3, "reword_prompt_version": 0},  # no source column value - not Nobel, still pending
    ]
    result = pending_phrasing_events(events, REWORD_PROMPT_VERSION)
    assert [event["id"] for event in result] == [1, 3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/Scripts/python.exe -m pytest tests/test_backfill_event_enrichment.py::test_pending_phrasing_events_excludes_nobel_sourced_rows -v`
Expected: FAIL — event id 2 is included in the result (no exclusion logic yet)

- [ ] **Step 3: Write the implementation**

In `src/ingest/backfill_event_enrichment.py`, add the import near the top (after the existing `from ingest.enrichment import (...)` block):

```python
from ingest.sources.nobel import NOBEL_SOURCE
```

Change the `_fetch_all_events` select string (around line 47) from:

```python
            .select("id, name, text, year, month, day, reword_prompt_version")
```

to:

```python
            .select("id, name, text, year, month, day, reword_prompt_version, source")
```

Change `pending_phrasing_events` (around line 88-94) from:

```python
def pending_phrasing_events(all_events: List[Dict], version: int) -> List[Dict]:
    """Events not yet written under the current reword prompt.

    A missing key counts as 0 (the column default), so rows predating the
    column are always pending.
    """
    return [event for event in all_events if (event.get("reword_prompt_version") or 0) < version]
```

to:

```python
def pending_phrasing_events(all_events: List[Dict], version: int) -> List[Dict]:
    """Events not yet written under the current reword prompt.

    A missing reword_prompt_version key counts as 0 (the column default), so
    rows predating the column are always pending. Nobel-sourced rows are
    excluded regardless of version: they are written under their own prompt
    (ingest.nobel_reword_prompt.md) and their own version counter sharing this
    same column, so sweeping them into this generic pass would re-word them
    with the wrong prompt and re-derive tags by free LLM choice instead of the
    deterministic NOBEL_CATEGORY_TAGS mapping.
    """
    return [
        event
        for event in all_events
        if event.get("source") != NOBEL_SOURCE and (event.get("reword_prompt_version") or 0) < version
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_backfill_event_enrichment.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones — confirms the change is backward compatible for rows with no `source` key)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/backfill_event_enrichment.py tests/test_backfill_event_enrichment.py
git commit -m "$(cat <<'EOF'
fix: exclude Nobel-sourced rows from the generic reword backfill

mode=\"phrasing\" would otherwise sweep Nobel rows into reword_prompt.md
on a future version bump, overwriting their event_phrase with the wrong
prompt and re-deriving tags by free LLM choice instead of the
deterministic NOBEL_CATEGORY_TAGS mapping.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Nobel phrasing prompt and chunk/merge pipeline (`ingest.nobel_llm_utils`)

**Files:**
- Create: `src/ingest/nobel_reword_prompt.md`
- Create: `src/ingest/nobel_llm_utils.py`
- Test: `tests/test_nobel_llm_utils.py`

**Interfaces:**
- Consumes: `ingest.sources.nobel.{NOBEL_CATEGORY_TAGS, build_event_text, category_display_name}` (Task 1), `ingest.enrichment.{check_phrase_format, check_facts_preserved, write_review_entries}` (existing), `core.io.{load_json, save_to_json}` (existing).
- Produces:
  - `NOBEL_REWORD_PROMPT_VERSION: int = 1`
  - `CHUNK_DIR: Path`, `DISPLAYABLE_PATH: Path`, `REVIEW_PATH: Path`
  - `build_nobel_prompt() -> str`
  - `prepare_nobel_chunks(records: List[Dict], chunk_size: int = 100) -> List[Path]`
  - `merge_nobel_chunk(chunk_path: Path, result_path: Path, displayable_path: Path = DISPLAYABLE_PATH, review_path: Path = REVIEW_PATH) -> int`

Later tasks rely on: a record written to `DISPLAYABLE_PATH` by `merge_nobel_chunk` carries every field `load_nobel_records`/`resolve_nobel_wikidata` produced (via `{**record, ...}` spreading) **plus** `person_id` and `age_days` (added by Task 5 *before* the record reaches `prepare_nobel_chunks` — see Task 5) **plus** `event_phrase`, `tags: List[str]`, and (on subagent-sourced output only) `reword_prompt_version`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_nobel_llm_utils.py`:

```python
import json

from ingest.nobel_llm_utils import (
    NOBEL_REWORD_PROMPT_VERSION,
    merge_nobel_chunk,
    prepare_nobel_chunks,
)


def _record(**overrides):
    base = {
        "laureate_id": "6", "name": "Marie Curie", "category": "Chemistry",
        "award_year": 1911, "award_month": 12, "award_day": 10,
        "motivation": "in recognition of her services", "wikipedia_url": None,
        "birth_year": 1867, "birth_month": 11, "birth_day": 7,
        "person_id": 42, "age_days": 16103,
    }
    return {**base, **overrides}


def test_prepare_nobel_chunks_adds_category_display(tmp_path, monkeypatch):
    import ingest.nobel_llm_utils as nobel_llm_utils

    monkeypatch.setattr(nobel_llm_utils, "CHUNK_DIR", tmp_path)
    paths = prepare_nobel_chunks([_record()], chunk_size=100)

    assert len(paths) == 1
    chunk = json.loads(paths[0].read_text(encoding="utf-8"))
    assert chunk[0]["category_display"] == "the Nobel Prize in Chemistry"
    assert chunk[0]["name"] == "Marie Curie"
    assert chunk[0]["person_id"] == 42  # carried through untouched


def test_prepare_nobel_chunks_splits_at_chunk_size(tmp_path, monkeypatch):
    import ingest.nobel_llm_utils as nobel_llm_utils

    monkeypatch.setattr(nobel_llm_utils, "CHUNK_DIR", tmp_path)
    records = [_record(laureate_id=str(i), award_year=1900 + i) for i in range(5)]
    paths = prepare_nobel_chunks(records, chunk_size=2)

    assert len(paths) == 3
    assert len(json.loads(paths[0].read_text(encoding="utf-8"))) == 2
    assert len(json.loads(paths[2].read_text(encoding="utf-8"))) == 1


def test_merge_nobel_chunk_uses_result_and_assigns_the_deterministic_tag(tmp_path):
    chunk = [_record()]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [{"laureate_id": "6", "name": "Marie Curie", "award_year": 1911, "event_phrase": "Marie Curie was when she won the Nobel Prize in Chemistry for her work on radioactivity."}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "nobel_displayable.json"
    merged_count = merge_nobel_chunk(
        chunk_path, result_path, displayable_path=displayable_path, review_path=tmp_path / "review.json"
    )

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "Marie Curie was when she won the Nobel Prize in Chemistry for her work on radioactivity."
    assert merged[0]["tags"] == ["science"]
    assert merged[0]["reword_prompt_version"] == NOBEL_REWORD_PROMPT_VERSION
    assert merged[0]["person_id"] == 42
    assert merged[0]["age_days"] == 16103


def test_merge_nobel_chunk_falls_back_on_missing_result_file(tmp_path):
    chunk = [_record()]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    displayable_path = tmp_path / "nobel_displayable.json"
    merged_count = merge_nobel_chunk(
        chunk_path,
        tmp_path / "does_not_exist.json",
        displayable_path=displayable_path,
        review_path=tmp_path / "review.json",
    )

    assert merged_count == 1
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "Marie Curie was when they won the Nobel Prize in Chemistry."
    assert merged[0]["tags"] == ["science"]
    # Deliberately unstamped so a later rerun re-queues it, matching
    # ingest.llm_utils._fallback_event_phrase's convention.
    assert "reword_prompt_version" not in merged[0]


def test_merge_nobel_chunk_gives_each_repeat_award_its_own_phrase(tmp_path):
    # John Bardeen won Physics twice (1956 and 1972) - (laureate_id, category)
    # would collide between his two awards, so the key must include award_year.
    chunk = [
        _record(laureate_id="66", name="John Bardeen", category="Physics", award_year=1956, person_id=1, age_days=100),
        _record(laureate_id="66", name="John Bardeen", category="Physics", award_year=1972, person_id=1, age_days=200),
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {"laureate_id": "66", "name": "John Bardeen", "award_year": 1956, "event_phrase": "his 1956 phrase."},
        {"laureate_id": "66", "name": "John Bardeen", "award_year": 1972, "event_phrase": "his 1972 phrase."},
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "nobel_displayable.json"
    merge_nobel_chunk(chunk_path, result_path, displayable_path=displayable_path, review_path=tmp_path / "review.json")

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_year = {m["award_year"]: m["event_phrase"] for m in merged}
    assert by_year == {1956: "his 1956 phrase.", 1972: "his 1972 phrase."}


def test_merge_nobel_chunk_flags_a_malformed_phrase(tmp_path):
    chunk = [_record()]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Old suffix-only shape, not name-onward.
    result = [{"laureate_id": "6", "name": "Marie Curie", "award_year": 1911, "event_phrase": "she won the prize"}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    merge_nobel_chunk(chunk_path, result_path, displayable_path=tmp_path / "nobel_displayable.json", review_path=review_path)

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["format"]


def test_merge_nobel_chunk_appends_to_existing_displayable_file(tmp_path):
    displayable_path = tmp_path / "nobel_displayable.json"
    displayable_path.write_text(
        json.dumps([{"laureate_id": "1", "name": "Existing Laureate", "award_year": 1950, "event_phrase": "already here", "tags": ["science"], "category": "Physics"}]),
        encoding="utf-8",
    )

    chunk = [_record(laureate_id="6", award_year=1911)]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    merge_nobel_chunk(
        chunk_path, tmp_path / "does_not_exist.json", displayable_path=displayable_path, review_path=tmp_path / "review.json"
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert len(merged) == 2
    assert {m["name"] for m in merged} == {"Existing Laureate", "Marie Curie"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_nobel_llm_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.nobel_llm_utils'`

- [ ] **Step 3: Write the implementation**

Create `src/ingest/nobel_reword_prompt.md`:

```markdown
# Nobel Prize event rewording instructions

You will receive a JSON array of Nobel Prize laureate records, each with:
- `laureate_id`, `award_year`: together identify this specific award (a repeat winner
  has one record per award, so these two fields together - not laureate_id alone -
  are what you must echo back unchanged for your output to be matched to the right
  record).
- `name`: the laureate's name.
- `category_display`: the prize's full display name, already correctly formatted
  (e.g. "the Nobel Prize in Physics", or "the Nobel Memorial Prize in Economic
  Sciences" for that one category) - use this exact string, don't reconstruct it
  from `category`.
- `motivation`: the official prize citation, e.g. "for his discovery of the neutron".

For each record, return a JSON object with these fields only:

- `laureate_id`: copied unchanged from the input.
- `award_year`: copied unchanged from the input.
- `name`: copied unchanged from the input.
- `event_phrase`: the sentence shown to the app user, written from the person onward.
  It always follows this template, with the `was when` hinge never varied:

  > **{person}** was when **{event}**.

  The app prepends a tensed opening in front of this ("You were the same age",
  "You're the same age", "You'll be the same age") - that part is computed by the
  app, not written by you. Begin your output directly with the person.

  - Default to `name` exactly as given. After first naming the person, use a
    pronoun rather than repeating the name - infer pronouns from the name only when
    genuinely unambiguous, otherwise use a short form of their name instead.
  - The event is always "won {category_display}" - reword the *justification*, not
    the fact of winning. Weave `motivation` in naturally rather than quoting it
    verbatim: "for his discovery of the neutron" becomes "...for discovering the
    neutron", not left in its citation phrasing.
  - Past tense throughout, matching "was when". End with a period.
  - One sentence. It renders as a bullet in a calendar dialog and as the body of a
    push notification - don't pad it with biographical detail `motivation` doesn't
    contain.
  - Preserve every concrete fact in `motivation`: what the achievement actually was,
    any named discovery/work/mechanism. Reword the structure, not the substance.

  Worked example - for `name` "Enrico Fermi", `category_display` "the Nobel Prize in
  Physics", `motivation` "for his demonstrations of the existence of new radioactive
  elements produced by neutron irradiation, and for his related discovery of nuclear
  reactions brought about by slow neutrons":

  > Enrico Fermi was when he won the Nobel Prize in Physics for demonstrating new
  > radioactive elements produced by neutron irradiation and discovering the nuclear
  > reactions caused by slow neutrons.

Return a JSON array in the same order as the input, one object per input record.
Return every record you were given - don't skip any, even if `event_phrase` ends up
close to a direct rewording of `motivation`.
```

Create `src/ingest/nobel_llm_utils.py`:

```python
"""LLM phrasing for Nobel Prize laureate records.

Nobel records are structured data with a known subject and no free-text
ambiguity, so this mirrors ingest.llm_utils's chunk/dispatch/merge shape but
skips what that module needs for messy scraped text: no suggested_subject
step, and tags come from ingest.sources.nobel.NOBEL_CATEGORY_TAGS rather than
being chosen by the LLM. See
docs/superpowers/specs/2026-08-28-nobel-prize-ingestion-design.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from ingest.enrichment import check_facts_preserved, check_phrase_format, write_review_entries
from ingest.sources.nobel import NOBEL_CATEGORY_TAGS, build_event_text, category_display_name

CHUNK_SIZE = 100
CHUNK_DIR = DATA_DIR / "tmp" / "nobel_reword_chunks"
DISPLAYABLE_PATH = DATA_DIR / "nobel_displayable.json"
REVIEW_PATH = DATA_DIR / "tmp" / "nobel_enrichment_review.json"
PROMPT_PATH = Path(__file__).parent / "nobel_reword_prompt.md"

#: Bumped by hand whenever nobel_reword_prompt.md changes in a way that could
#: change results. Shares the events.reword_prompt_version column with the
#: historical corpus's REWORD_PROMPT_VERSION - safe because
#: ingest.backfill_event_enrichment.pending_phrasing_events excludes
#: Nobel-sourced rows from that counter's selection entirely (Task 3).
NOBEL_REWORD_PROMPT_VERSION = 1


def build_nobel_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _record_key(record: Dict) -> Tuple[object, object]:
    """(laureate_id, award_year) - the unique key for one CSV row.

    laureate_id alone identifies the *person*, not the award: John Bardeen,
    Frederick Sanger, and K. Barry Sharpless each won the same category twice
    in different years, so laureate_id repeats across their rows. award_year
    disambiguates.
    """
    return (record.get("laureate_id"), record.get("award_year"))


def _fallback_event_phrase(record: Dict) -> str:
    """Deterministic phrase used when a subagent can't produce usable output.

    Deliberately not stamped with NOBEL_REWORD_PROMPT_VERSION by the caller,
    so a later rerun re-queues it - matches ingest.llm_utils's fallback
    convention exactly.
    """
    return f'{record["name"]} was when they won {category_display_name(record["category"])}.'


def prepare_nobel_chunks(records: List[Dict], chunk_size: int = CHUNK_SIZE) -> List[Path]:
    """Split pending Nobel records into numbered chunk files for a subagent to process.

    Each chunk record carries every field the input record had, plus
    category_display (computed here so the prompt never has to reconstruct
    the Economic Sciences special case itself).
    """
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(records), chunk_size)):
        chunk = [
            {**record, "category_display": category_display_name(record["category"])}
            for record in records[start : start + chunk_size]
        ]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths


def merge_nobel_chunk(
    chunk_path: Path,
    result_path: Path,
    displayable_path: Path = DISPLAYABLE_PATH,
    review_path: Path = REVIEW_PATH,
) -> int:
    """Merge one subagent's reworded chunk into displayable_path.

    Records are matched back to the chunk by (laureate_id, award_year), not
    list order, so a subagent that drops or reorders a record is still
    handled correctly. A record with no usable event_phrase gets the
    deterministic fallback rather than being dropped. Returns how many
    records were merged (always len(chunk) - every input record produces an
    output record, real or fallback).
    """
    chunk = load_json(chunk_path)

    reworded_by_key: Dict[Tuple, Dict] = {}
    try:
        reworded = load_json(result_path)
        reworded_by_key = {_record_key(r): r for r in reworded}
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    review_entries: List[Dict] = []
    merged: List[Dict] = []

    for record in chunk:
        key = _record_key(record)
        result = reworded_by_key.get(key)
        tag = NOBEL_CATEGORY_TAGS[record["category"]]

        if not result or not result.get("event_phrase"):
            merged.append({**record, "event_phrase": _fallback_event_phrase(record), "tags": [tag]})
            continue

        event_phrase = result["event_phrase"]
        merged_record = {
            **record,
            "event_phrase": event_phrase,
            "tags": [tag],
            "reword_prompt_version": NOBEL_REWORD_PROMPT_VERSION,
        }

        format_reason = check_phrase_format(event_phrase, record["name"])
        if format_reason:
            review_entries.append(
                {"name": record["name"], "category": record["category"], "issue_type": "format", "detail": format_reason}
            )

        missing = check_facts_preserved(build_event_text(record), event_phrase)
        if missing:
            review_entries.append(
                {
                    "name": record["name"],
                    "category": record["category"],
                    "issue_type": "facts",
                    "detail": f"missing from phrase: {', '.join(missing)}",
                }
            )

        merged.append(merged_record)

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

Run: `./venv/Scripts/python.exe -m pytest tests/test_nobel_llm_utils.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/nobel_reword_prompt.md src/ingest/nobel_llm_utils.py tests/test_nobel_llm_utils.py
git commit -m "$(cat <<'EOF'
feat: Nobel-specific LLM phrasing pass with deterministic tagging

Mirrors ingest.llm_utils's chunk/dispatch/merge shape with a smaller
prompt (no subject-correction, no LLM-chosen tags - those apply from
ingest.sources.nobel.NOBEL_CATEGORY_TAGS at merge time instead).
Records are keyed by (laureate_id, award_year), not laureate_id alone,
since three laureates won the same category twice in different years.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Person resolution and the duplicate-day guard (`ingest.migrate_nobel_to_supabase`, phase 1)

**Files:**
- Create: `src/ingest/migrate_nobel_to_supabase.py`
- Test: `tests/test_migrate_nobel_to_supabase.py`

**Interfaces:**
- Consumes: `core.matching.normalize_name` (existing), `core.db.get_client` (existing), `ingest.enrichment.write_review_entries` (existing), `ingest.pipeline.calculate_age` (existing), `ingest.match_events.MAX_AGE_DAYS` (existing), `ingest.sources.nobel.NOBEL_SOURCE` (Task 1), `core.io.save_to_json` (existing).
- Produces (this task):
  - `NOBEL_PENDING_PATH: Path`
  - `build_person_rows(records: List[Dict]) -> List[Dict]`
  - `find_normalized_name_collisions(records: List[Dict], existing_persons: List[Dict]) -> List[Dict]`
  - `wikipedia_url_updates(records: List[Dict], name_to_person_id: Dict[str, int], existing_wikipedia_by_id: Dict[int, Optional[str]]) -> List[Tuple[int, str]]`
  - `find_duplicate_day_records(records: List[Dict], name_to_person_id: Dict[str, int], existing_event_keys: Dict[Tuple, Dict]) -> Tuple[List[Dict], List[Dict]]`
  - `build_pending_records(records: List[Dict], name_to_person_id: Dict[str, int]) -> Tuple[List[Dict], List[Dict]]`
  - `fetch_all_persons(client) -> List[Dict]`
  - `fetch_existing_event_person_dates(client) -> Dict[Tuple[int, int, int, int], Dict]`
  - `prepare_pending_records(records: List[Dict]) -> Dict[str, int]` — the phase-1 orchestrator; writes to `NOBEL_PENDING_PATH`.
- Produces for Task 6: every record written to `NOBEL_PENDING_PATH` carries `person_id: int` and `age_days: int` alongside everything `load_nobel_records`/`resolve_nobel_wikidata` produced.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migrate_nobel_to_supabase.py`:

```python
import json
from unittest.mock import MagicMock, patch

from ingest.migrate_nobel_to_supabase import (
    build_pending_records,
    build_person_rows,
    fetch_all_persons,
    fetch_existing_event_person_dates,
    find_duplicate_day_records,
    find_normalized_name_collisions,
    prepare_pending_records,
    wikipedia_url_updates,
)


def _record(**overrides):
    base = {
        "laureate_id": "6", "name": "Marie Curie", "category": "Chemistry",
        "award_year": 1911, "award_month": 12, "award_day": 10,
        "motivation": "in recognition of her services", "wikipedia_url": "https://en.wikipedia.org/wiki/Marie_Curie",
        "birth_year": 1867, "birth_month": 11, "birth_day": 7,
    }
    return {**base, **overrides}


# --- build_person_rows ---


def test_build_person_rows_is_one_row_per_distinct_name_sorted():
    records = [_record(name="Marie Curie"), _record(name="Albert Einstein"), _record(name="Marie Curie")]
    assert build_person_rows(records) == [{"name": "Albert Einstein"}, {"name": "Marie Curie"}]


# --- find_normalized_name_collisions ---


def test_find_normalized_name_collisions_flags_the_real_j_j_thomson_case():
    # The one real near-duplicate found against the live persons table during
    # design: Nobel's "J.J. Thomson" vs. the existing "J. J. Thomson".
    records = [_record(name="J.J. Thomson")]
    existing_persons = [{"id": 1, "name": "J. J. Thomson", "wikipedia_url": None}]

    collisions = find_normalized_name_collisions(records, existing_persons)

    assert len(collisions) == 1
    assert collisions[0]["name"] == "J.J. Thomson"
    assert "J. J. Thomson" in collisions[0]["detail"]


def test_find_normalized_name_collisions_ignores_exact_matches():
    # An exact-string match (Marie Curie already exists) is meant to reuse the
    # existing row via upsert, not be flagged as a collision.
    records = [_record(name="Marie Curie")]
    existing_persons = [{"id": 1, "name": "Marie Curie", "wikipedia_url": None}]
    assert find_normalized_name_collisions(records, existing_persons) == []


def test_find_normalized_name_collisions_ignores_genuinely_new_names():
    records = [_record(name="Someone New")]
    existing_persons = [{"id": 1, "name": "Marie Curie", "wikipedia_url": None}]
    assert find_normalized_name_collisions(records, existing_persons) == []


# --- wikipedia_url_updates ---


def test_wikipedia_url_updates_fills_in_a_null_existing_value():
    records = [_record(name="Marie Curie", wikipedia_url="https://en.wikipedia.org/wiki/Marie_Curie")]
    name_to_person_id = {"Marie Curie": 7}
    existing_wikipedia_by_id = {7: None}

    updates = wikipedia_url_updates(records, name_to_person_id, existing_wikipedia_by_id)

    assert updates == [(7, "https://en.wikipedia.org/wiki/Marie_Curie")]


def test_wikipedia_url_updates_never_overwrites_an_already_verified_value():
    records = [_record(name="Marie Curie", wikipedia_url="https://en.wikipedia.org/wiki/Marie_Curie_wrong")]
    name_to_person_id = {"Marie Curie": 7}
    existing_wikipedia_by_id = {7: "https://en.wikipedia.org/wiki/Marie_Curie"}

    assert wikipedia_url_updates(records, name_to_person_id, existing_wikipedia_by_id) == []


def test_wikipedia_url_updates_skips_records_with_no_url_to_offer():
    records = [_record(name="Marie Curie", wikipedia_url=None)]
    updates = wikipedia_url_updates(records, {"Marie Curie": 7}, {7: None})
    assert updates == []


# --- find_duplicate_day_records ---


def test_find_duplicate_day_records_blocks_a_real_exact_match():
    # A real collision found against the live DB during design: Jean-Paul
    # Sartre's 1964-10-22 Nobel Prize in Literature is already in `events`
    # (scraped from Wikipedia's "on this day" corpus, as events.id=979).
    # person_id (501) is a synthetic id for this test, distinct from that real
    # event id, to avoid implying it's Sartre's actual persons.id in production.
    records = [_record(name="Jean-Paul Sartre", award_year=1964, award_month=10, award_day=22)]
    name_to_person_id = {"Jean-Paul Sartre": 501}
    existing_event_keys = {(501, 1964, 10, 22): {"id": 979, "text": "Jean-Paul Sartre is awarded..."}}

    keep, blocked = find_duplicate_day_records(records, name_to_person_id, existing_event_keys)

    assert keep == []
    assert [r["name"] for r in blocked] == ["Jean-Paul Sartre"]


def test_find_duplicate_day_records_keeps_a_different_day_same_person_same_year():
    # The accepted gap: Yasunari Kawabata's real Wikipedia-scraped event is one
    # day off from the Nobel dataset's date_awarded. Not blocked - a separate,
    # slightly redundant but individually truthful event is accepted.
    # person_id (502) and the existing event's id (957) are both synthetic /
    # illustrative here, kept distinct for the same reason as the test above.
    records = [_record(name="Yasunari Kawabata", award_year=1968, award_month=10, award_day=17)]
    name_to_person_id = {"Yasunari Kawabata": 502}
    existing_event_keys = {(502, 1968, 10, 16): {"id": 957, "text": "Yasunari Kawabata becomes..."}}

    keep, blocked = find_duplicate_day_records(records, name_to_person_id, existing_event_keys)

    assert [r["name"] for r in keep] == ["Yasunari Kawabata"]
    assert blocked == []


def test_find_duplicate_day_records_keeps_records_with_no_resolved_person_id():
    records = [_record(name="Nobody Upserted")]
    keep, blocked = find_duplicate_day_records(records, name_to_person_id={}, existing_event_keys={})
    assert [r["name"] for r in keep] == ["Nobody Upserted"]
    assert blocked == []


# --- build_pending_records ---


def test_build_pending_records_computes_age_days_and_person_id():
    records = [_record(name="Marie Curie", birth_year=1867, birth_month=11, birth_day=7, award_year=1911, award_month=12, award_day=10)]
    pending, implausible = build_pending_records(records, {"Marie Curie": 7})

    assert implausible == []
    assert pending[0]["person_id"] == 7
    assert pending[0]["age_days"] == 16103


def test_build_pending_records_excludes_an_implausible_age():
    # Birth date after the award date - calculate_age still returns a value
    # (a negative one), which must still be rejected by the bound.
    records = [_record(name="Time Traveler", birth_year=1950, birth_month=1, birth_day=1, award_year=1911, award_month=12, award_day=10)]
    pending, implausible = build_pending_records(records, {"Time Traveler": 1})

    assert pending == []
    assert [r["name"] for r in implausible] == ["Time Traveler"]


# --- fetch helpers (paginated Supabase reads) ---


def test_fetch_all_persons_paginates_past_the_page_size():
    mock_client = MagicMock()
    full_page = [{"id": i, "name": f"Person {i}", "wikipedia_url": None} for i in range(1000)]
    short_page = [{"id": 1000, "name": "Person 1000", "wikipedia_url": None}]
    mock_execute = mock_client.table.return_value.select.return_value.range.return_value.execute
    mock_execute.side_effect = [MagicMock(data=full_page), MagicMock(data=short_page)]

    result = fetch_all_persons(mock_client)

    assert len(result) == 1001
    range_calls = mock_client.table.return_value.select.return_value.range.call_args_list
    assert range_calls[0].args == (0, 999)
    assert range_calls[1].args == (1000, 1999)


def test_fetch_existing_event_person_dates_keys_by_person_id_and_date():
    mock_client = MagicMock()
    page = [{"person_id": 979, "year": 1964, "month": 10, "day": 22, "id": 979, "text": "Jean-Paul Sartre..."}]
    mock_client.table.return_value.select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=page)
    ]

    result = fetch_existing_event_person_dates(mock_client)

    assert result == {(979, 1964, 10, 22): {"id": 979, "text": "Jean-Paul Sartre..."}}


def test_fetch_existing_event_person_dates_skips_rows_with_no_person():
    mock_client = MagicMock()
    page = [{"person_id": None, "year": 2000, "month": 1, "day": 1, "id": 5, "text": "unrelated"}]
    mock_client.table.return_value.select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=page)
    ]
    assert fetch_existing_event_person_dates(mock_client) == {}


# --- prepare_pending_records (the phase-1 orchestrator) ---


def _make_mock_client(persons=None, upsert_data=None, event_dates_page=None):
    persons = persons if persons is not None else []
    table_mocks = {"persons": MagicMock(), "events": MagicMock()}
    table_mocks["persons"].select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=persons), MagicMock(data=[]),
    ]
    table_mocks["persons"].upsert.return_value.execute.return_value.data = upsert_data or []
    table_mocks["events"].select.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=event_dates_page or []), MagicMock(data=[]),
    ]

    client = MagicMock()
    client.table.side_effect = lambda name: table_mocks[name]
    client._table_mocks = table_mocks
    return client


def test_prepare_pending_records_writes_survivors_to_the_pending_file(tmp_path):
    records = [_record(name="Marie Curie")]
    mock_client = _make_mock_client(
        persons=[], upsert_data=[{"id": 7, "name": "Marie Curie", "wikipedia_url": None}]
    )
    output_path = tmp_path / "nobel_pending.json"

    with patch("ingest.migrate_nobel_to_supabase.get_client", return_value=mock_client):
        counts = prepare_pending_records(
            records,
            output_path=output_path,
            person_review_path=tmp_path / "person_review.json",
            duplicate_review_path=tmp_path / "duplicate_review.json",
            age_review_path=tmp_path / "age_review.json",
        )

    assert counts["pending"] == 1
    pending = json.loads(output_path.read_text(encoding="utf-8"))
    assert pending[0]["name"] == "Marie Curie"
    assert pending[0]["person_id"] == 7
    assert pending[0]["age_days"] == 16103
    mock_client._table_mocks["persons"].upsert.assert_called_once_with(
        [{"name": "Marie Curie"}], on_conflict="name"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_migrate_nobel_to_supabase.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest.migrate_nobel_to_supabase'`

- [ ] **Step 3: Write the implementation**

Create `src/ingest/migrate_nobel_to_supabase.py`:

```python
"""Person resolution, the duplicate-day guard, and the final Supabase write for
Nobel Prize laureate records. Two phases in one module, mirroring
ingest.migrate_to_supabase's shape:

    python -c "from ingest.migrate_nobel_to_supabase import prepare_pending_records; \
        from ingest.sources.nobel import load_nobel_records; \
        prepare_pending_records(load_nobel_records())"
    # ... resolve_nobel_wikidata, then the LLM phrasing pass populate
    # data/nobel_displayable.json in between ...
    python -c "from ingest.migrate_nobel_to_supabase import insert_nobel_events; \
        from core.io import load_json; from ingest.nobel_llm_utils import DISPLAYABLE_PATH; \
        insert_nobel_events(load_json(DISPLAYABLE_PATH))"

See docs/superpowers/specs/2026-08-28-nobel-prize-ingestion-design.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.db import get_client
from core.io import save_to_json
from core.matching import normalize_name
from ingest.enrichment import build_tag_rows, write_review_entries
from ingest.match_events import MAX_AGE_DAYS
from ingest.pipeline import calculate_age
from ingest.sources.nobel import NOBEL_SOURCE, build_event_text

EVENTS_PAGE_SIZE = 1000

NOBEL_PENDING_PATH = DATA_DIR / "tmp" / "nobel_pending.json"
NOBEL_PERSON_REVIEW_PATH = DATA_DIR / "tmp" / "nobel_person_review.json"
NOBEL_DUPLICATE_REVIEW_PATH = DATA_DIR / "tmp" / "nobel_duplicate_review.json"
NOBEL_AGE_REVIEW_PATH = DATA_DIR / "tmp" / "nobel_age_review.json"


# --- pure functions ---


def build_person_rows(records: List[Dict]) -> List[Dict]:
    """One persons row per distinct name, sorted for a deterministic insert order."""
    return [{"name": name} for name in sorted({record["name"] for record in records})]


def find_normalized_name_collisions(records: List[Dict], existing_persons: List[Dict]) -> List[Dict]:
    """Records whose name normalizes the same as an existing person's but isn't an
    exact string match - e.g. Nobel's "J.J. Thomson" vs. the existing "J. J.
    Thomson". Exact matches are meant to reuse the existing row via upsert;
    these near-duplicates are flagged for a one-line manual fix rather than
    auto-merged or silently duplicated (detect-don't-guess, matching this
    codebase's stated position on same-name collisions).
    """
    existing_by_norm: Dict[str, List[str]] = {}
    for person in existing_persons:
        existing_by_norm.setdefault(normalize_name(person["name"]), []).append(person["name"])

    collisions: List[Dict] = []
    for record in records:
        norm = normalize_name(record["name"])
        matches = existing_by_norm.get(norm, [])
        if matches and record["name"] not in matches:
            collisions.append(
                {
                    "issue_type": "normalized_name_collision",
                    "name": record["name"],
                    "detail": f"normalizes the same as existing person(s) {matches!r}",
                }
            )
    return collisions


def wikipedia_url_updates(
    records: List[Dict],
    name_to_person_id: Dict[str, int],
    existing_wikipedia_by_id: Dict[int, Optional[str]],
) -> List[Tuple[int, str]]:
    """(person_id, url) pairs to write - only where the person currently has no
    wikipedia_url, matching backfill_person_wikipedia.py's rule of never
    overwriting an already-verified value.
    """
    updates: List[Tuple[int, str]] = []
    seen_ids = set()
    for record in records:
        url = record.get("wikipedia_url")
        person_id = name_to_person_id.get(record["name"])
        if not url or person_id is None or person_id in seen_ids:
            continue
        if not existing_wikipedia_by_id.get(person_id):
            updates.append((person_id, url))
            seen_ids.add(person_id)
    return updates


def find_duplicate_day_records(
    records: List[Dict],
    name_to_person_id: Dict[str, int],
    existing_event_keys: Dict[Tuple[int, int, int, int], Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """Split records into (keep, blocked): one event per person per day.

    A record whose person_id + exact award date already has an events row
    (typically scraped from Wikipedia's "on this day" corpus) is blocked. A
    record with no resolved person_id can't be checked and is always kept.
    """
    keep: List[Dict] = []
    blocked: List[Dict] = []
    for record in records:
        person_id = name_to_person_id.get(record["name"])
        key = (person_id, record["award_year"], record["award_month"], record["award_day"])
        if person_id is not None and key in existing_event_keys:
            blocked.append(record)
        else:
            keep.append(record)
    return keep, blocked


def build_pending_records(
    records: List[Dict], name_to_person_id: Dict[str, int]
) -> Tuple[List[Dict], List[Dict]]:
    """Compute age_days and person_id for each record.

    Returns (pending, implausible). pending records carry person_id and
    age_days, ready for the LLM phrasing pass. implausible records failed the
    0 <= age_days <= MAX_AGE_DAYS bound and are excluded - expected to be rare
    (CSV-supplied birth dates are already sane), but matters for the
    Wikidata-resolved records where a same-name mismatch is possible.
    """
    pending: List[Dict] = []
    implausible: List[Dict] = []
    for record in records:
        age_days = calculate_age(
            record["birth_year"], record["birth_month"], record["birth_day"],
            record["award_year"], record["award_month"], record["award_day"],
        )
        if age_days is None or not (0 <= age_days <= MAX_AGE_DAYS):
            implausible.append(record)
            continue
        pending.append({**record, "person_id": name_to_person_id.get(record["name"]), "age_days": age_days})
    return pending, implausible


def _to_event_row(record: Dict) -> Dict:
    """A phrased, pending Nobel record -> an events insert row."""
    return {
        "name": record["name"],
        "person_id": record["person_id"],
        "text": build_event_text(record),
        "event_phrase": record["event_phrase"],
        "reword_prompt_version": record.get("reword_prompt_version", 0),
        "year": record["award_year"],
        "month": record["award_month"],
        "day": record["award_day"],
        "age_days": record["age_days"],
        "event_type": "achievement",
        "source": NOBEL_SOURCE,
    }


# --- paginated Supabase reads ---


def fetch_all_persons(client) -> List[Dict]:
    """Every persons row (id, name, wikipedia_url), paginated past the ~1000 row cap."""
    persons: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("persons")
            .select("id, name, wikipedia_url")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        persons.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return persons


def fetch_existing_event_person_dates(client) -> Dict[Tuple[int, int, int, int], Dict]:
    """(person_id, year, month, day) -> {"id", "text"} for every events row with a
    person_id, paginated past the ~1000 row cap. Rows with no person_id are
    skipped - the duplicate-day guard has nothing to compare them against.
    """
    keys: Dict[Tuple[int, int, int, int], Dict] = {}
    start = 0
    while True:
        page = (
            client.table("events")
            .select("id, person_id, year, month, day, text")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        for row in page:
            if row["person_id"] is None:
                continue
            keys[(row["person_id"], row["year"], row["month"], row["day"])] = {
                "id": row["id"], "text": row["text"],
            }
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return keys


# --- orchestrators (impure) ---


def prepare_pending_records(
    records: List[Dict],
    output_path: Path = NOBEL_PENDING_PATH,
    person_review_path: Path = NOBEL_PERSON_REVIEW_PATH,
    duplicate_review_path: Path = NOBEL_DUPLICATE_REVIEW_PATH,
    age_review_path: Path = NOBEL_AGE_REVIEW_PATH,
) -> Dict[str, int]:
    """Phase 1: resolve person_id, guard duplicates, compute age, write survivors.

    The last point before any LLM cost is spent (Task 4's phrasing pass reads
    output_path), so a bad guard or upsert never wastes a phrasing run.
    """
    client = get_client()

    existing_persons = fetch_all_persons(client)
    collisions = find_normalized_name_collisions(records, existing_persons)
    write_review_entries(collisions, person_review_path)
    colliding_names = {collision["name"] for collision in collisions}
    records = [record for record in records if record["name"] not in colliding_names]

    person_rows = build_person_rows(records)
    upserted = client.table("persons").upsert(person_rows, on_conflict="name").execute().data
    name_to_person_id = {person["name"]: person["id"] for person in upserted}

    existing_wikipedia_by_id = {person["id"]: person["wikipedia_url"] for person in existing_persons}
    for person_id, url in wikipedia_url_updates(records, name_to_person_id, existing_wikipedia_by_id):
        client.table("persons").update({"wikipedia_url": url}).eq("id", person_id).execute()

    existing_event_keys = fetch_existing_event_person_dates(client)
    keep, blocked = find_duplicate_day_records(records, name_to_person_id, existing_event_keys)
    write_review_entries(
        [
            {
                "issue_type": "duplicate_day",
                "name": record["name"],
                "detail": (
                    f"event already exists on {record['award_year']}-{record['award_month']:02d}-{record['award_day']:02d} "
                    f"(existing event id={existing_event_keys[(name_to_person_id.get(record['name']), record['award_year'], record['award_month'], record['award_day'])]['id']})"
                ),
            }
            for record in blocked
        ],
        duplicate_review_path,
    )

    pending, implausible = build_pending_records(keep, name_to_person_id)
    write_review_entries(
        [
            {
                "issue_type": "implausible_age",
                "name": record["name"],
                "detail": f"no plausible age for award_year={record['award_year']}",
            }
            for record in implausible
        ],
        age_review_path,
    )

    save_to_json(output_path, pending)
    return {
        "input": len(records) + len(collisions),
        "name_collisions": len(collisions),
        "duplicate_day_blocked": len(blocked),
        "implausible_age": len(implausible),
        "pending": len(pending),
    }


def insert_nobel_events(records: List[Dict], batch_size: int = 200) -> int:
    """Phase 2: insert events/event_tags rows for phrased Nobel records.

    Assumes records already passed through prepare_pending_records (person_id
    set, age_days computed, duplicates guarded) and the LLM phrasing pass
    (event_phrase and tags set) - i.e. records loaded from
    ingest.nobel_llm_utils.DISPLAYABLE_PATH.
    """
    client = get_client()
    tag_name_to_id = {tag["name"]: tag["id"] for tag in client.table("tags").select("id, name").execute().data}

    rows = [_to_event_row(record) for record in records]
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        batch_records = records[start : start + batch_size]
        inserted_events = client.table("events").insert(batch).execute().data

        tag_rows = []
        for inserted_event, record in zip(inserted_events, batch_records):
            tag_rows.extend(build_tag_rows(inserted_event["id"], record.get("tags") or [], tag_name_to_id))
        if tag_rows:
            client.table("event_tags").insert(tag_rows).execute()

        inserted += len(batch)
    return inserted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_migrate_nobel_to_supabase.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/migrate_nobel_to_supabase.py tests/test_migrate_nobel_to_supabase.py
git commit -m "$(cat <<'EOF'
feat: resolve Nobel persons and guard against duplicate-day events

Person upsert reuses existing rows by exact name (39 real matches
against the live persons table) and flags normalize_name-only
collisions for manual review (1 real case: "J.J. Thomson" vs the
existing "J. J. Thomson") rather than auto-merging. The duplicate-day
guard blocks re-adding an event already scraped from Wikipedia's "on
this day" corpus for the same person on the same exact date (6 real
collisions found against the live DB during design).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Verify the phase-1 → phrasing → phase-2 handoff end to end (integration test)

This task adds one integration-style test that exercises the real handoff between Tasks 4 and 5's file formats — `prepare_pending_records` writes `NOBEL_PENDING_PATH`, which is exactly what `prepare_nobel_chunks` (Task 4) must be able to consume, and `merge_nobel_chunk`'s output is exactly what `insert_nobel_events` (Task 5) must be able to consume. No new production code is expected; if this test fails, it means Tasks 4 and 5 disagree about record shape and that mismatch must be fixed before moving on.

**Files:**
- Test: `tests/test_migrate_nobel_to_supabase.py` (extend)

**Interfaces:**
- Consumes: `ingest.nobel_llm_utils.{prepare_nobel_chunks, merge_nobel_chunk}` (Task 4), `ingest.migrate_nobel_to_supabase.{prepare_pending_records, _to_event_row}` (Task 5).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrate_nobel_to_supabase.py`:

```python
def test_pending_record_shape_is_consumable_by_the_phrasing_and_insert_stages(tmp_path):
    """Integration check across the Task 4 / Task 5 file boundary.

    prepare_pending_records's output must have every field prepare_nobel_chunks
    needs (category, for category_display and the tag lookup) and every field
    _to_event_row needs after phrasing (person_id, age_days, award_year/month/day,
    motivation, category, name) - this test fails loudly if either module's
    expectations drift from what the other produces.
    """
    from ingest.migrate_nobel_to_supabase import _to_event_row
    from ingest.nobel_llm_utils import merge_nobel_chunk, prepare_nobel_chunks

    records = [_record(name="Marie Curie")]
    mock_client = _make_mock_client(
        persons=[], upsert_data=[{"id": 7, "name": "Marie Curie", "wikipedia_url": None}]
    )
    pending_path = tmp_path / "nobel_pending.json"

    with patch("ingest.migrate_nobel_to_supabase.get_client", return_value=mock_client):
        prepare_pending_records(
            records,
            output_path=pending_path,
            person_review_path=tmp_path / "person_review.json",
            duplicate_review_path=tmp_path / "duplicate_review.json",
            age_review_path=tmp_path / "age_review.json",
        )

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    chunk_dir = tmp_path / "chunks"
    with patch("ingest.nobel_llm_utils.CHUNK_DIR", chunk_dir):
        chunk_paths = prepare_nobel_chunks(pending, chunk_size=100)

    displayable_path = tmp_path / "nobel_displayable.json"
    merge_nobel_chunk(
        chunk_paths[0],
        tmp_path / "no_result_file.json",  # forces the fallback phrase - no subagent needed for this check
        displayable_path=displayable_path,
        review_path=tmp_path / "phrasing_review.json",
    )

    phrased = json.loads(displayable_path.read_text(encoding="utf-8"))
    row = _to_event_row(phrased[0])

    assert row["name"] == "Marie Curie"
    assert row["person_id"] == 7
    assert row["age_days"] == 16103
    assert row["year"] == 1911
    assert row["event_phrase"] == "Marie Curie was when they won the Nobel Prize in Chemistry."
    assert row["source"] == "nobel_prize_dataset"
```

- [ ] **Step 2: Run the test**

Run: `./venv/Scripts/python.exe -m pytest tests/test_migrate_nobel_to_supabase.py::test_pending_record_shape_is_consumable_by_the_phrasing_and_insert_stages -v`
Expected: PASS immediately, since Tasks 4 and 5 were already designed against the same record shape. If it fails, fix whichever of `prepare_pending_records`, `prepare_nobel_chunks`, `merge_nobel_chunk`, or `_to_event_row` dropped or renamed a field, then rerun.

- [ ] **Step 3: Run the full test suite**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: PASS, with the count at **309 + all new tests added in Tasks 1-6** (baseline 309 plus 11 + 6 + 1 + 7 + 16 + 1 = 42 new tests → 351 total; treat this as an estimate, not a hard assertion — a differing final count is fine as long as nothing regresses).

- [ ] **Step 4: Commit**

```bash
git add tests/test_migrate_nobel_to_supabase.py
git commit -m "$(cat <<'EOF'
test: verify the Nobel pending-record shape across the phrasing handoff

Locks in that prepare_pending_records' output is exactly what
prepare_nobel_chunks and merge_nobel_chunk expect, and that their
output is exactly what _to_event_row expects - the three modules were
written against the same shape independently and this is the one test
that would catch them drifting apart.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Run the ingestion end to end against live data (not a coding task)

**This task is executed directly by the orchestrating session, not delegated to subagent-driven-development** — it dispatches the actual Haiku phrasing subagents (via the `Agent` tool) and performs the real Supabase writes, both of which need the top-level session's own tool access and judgment on review-file contents, not a fresh task-scoped subagent.

Runbook, in order (matches the spec's Rollout order section):

1. **Parse.** `records = ingest.sources.nobel.load_nobel_records()` — expect 995 records.
2. **Resolve thin records.** `ingest.resolve_nobel_wikidata.run()` — expect up to 243 resolved, with a review file for the rest. Merge the resolved records back into the full 995-record pool (replacing the original thin records with their resolved counterparts, keyed by `(laureate_id, award_year)`).
3. **Phase 1.** `ingest.migrate_nobel_to_supabase.prepare_pending_records(records)` against the **live** Supabase project (real `.env` credentials, already present in the repo per `core.db.get_client`). Inspect the returned counts and every review file (`nobel_person_review.json`, `nobel_duplicate_review.json`, `nobel_age_review.json`) before proceeding — expect roughly 1 person collision, roughly 6 duplicate-day blocks, and a small handful of implausible-age rows at most (all numbers confirmed against the live DB during design; a wildly different count here is the "serious issue" the plan's owner should see before continuing, not something to route around).
4. **Phrasing.** `ingest.nobel_llm_utils.prepare_nobel_chunks(pending_records)` (pending records loaded from `NOBEL_PENDING_PATH`) — expect ~9-10 chunk files at `CHUNK_SIZE=100`. Dispatch one Haiku subagent per chunk file (via the `Agent` tool, `subagent_type` matching whatever this repo's prior batches used — see the memory of the original 1232-event batch for the exact dispatch convention), instructing each to read `ingest.nobel_llm_utils.build_nobel_prompt()` for its instructions and write its JSON array result to `<chunk>_result.json` next to the chunk file. Then call `merge_nobel_chunk(chunk_path, result_path)` per chunk.
5. **Review the phrasing pass.** Read `nobel_enrichment_review.json` for format/fact-check flags before inserting — these are advisory (never block a write) but worth a skim for anything systematic (e.g. every Peace Prize phrase missing a fact would indicate a prompt problem worth fixing before the write, not after).
6. **Phase 2.** `ingest.migrate_nobel_to_supabase.insert_nobel_events(load_json(DISPLAYABLE_PATH))` against the live Supabase project. This is the actual production write — report the final inserted count back to the user afterward along with a summary of every review file's contents, rather than asking permission beforehand (already authorized for this task).
7. **Spot-check in the app.** Launch the app (`./venv/Scripts/streamlit.exe run src/app/ui.py`, per this repo's existing dev workflow) and open the calendar around a known laureate's birthday (e.g. Marie Curie) to confirm a Nobel event renders correctly end to end — real event_phrase, correct tag/category, correct Wikipedia link.

Stop and flag to the user (rather than proceeding) only if: the duplicate-day guard or person-collision counts are wildly larger than the ~6 and ~1 found during design (suggesting a parsing or matching bug, not just normal data variance), or the phrasing review file shows a systematic failure pattern across many records rather than isolated cases.
