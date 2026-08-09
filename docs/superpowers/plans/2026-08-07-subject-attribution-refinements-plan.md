# Subject Attribution Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the matching pipeline auto-accept every real co-subject of a multi-person event, correct earlier wrong matches instead of just flagging them as conflicts, catch commemorative/"named after" references the age bound alone can't, and stop re-querying the LLM on events already confirmed to have no subject.

**Architecture:** Four independent-but-sequenced changes to the already-shipped matching-expansion pipeline. `classify_event` (Stage 1) gains a commemorative-phrase heuristic and a contract change — its `"matched"` status now carries every locally-known co-subject, not just one, retiring `"ambiguous"` entirely. `append_matched_events` gains a substring rule that distinguishes a truncated re-match of the same person (correct it) from a genuinely different person (keep both). `subject_extraction.py` gains a version-tagged cache so a confirmed-no-subject verdict isn't re-asked of the LLM every rerun. `subject_prompt.md` gets the same commemorative-reference instruction Stage 1 uses, for the cases only an LLM can judge.

**Tech Stack:** Python 3.11+, pytest. No new dependencies.

## Global Constraints

- **`classify_event`'s `"matched"` status now carries `{"matched": List[Dict], "implausible": List[str]}`**, not a bare dict. `"ambiguous"` is retired as a status entirely — a multi-candidate text is `"matched"` (with one record per candidate that passes the plausibility bound) unless the commemorative heuristic redirects it to `"possible_reference"` first.
- **Commemorative/named-after trigger phrases, checked case-insensitively as substrings of the raw event text**, exactly this set: `"named after"`, `"in honor of"`, `"in honour of"`, `"anniversary of"`, `"in memory of"`, `"dedicated to"`, `"commemorat"`. When any match, the event is never auto-accepted by Stage 1 regardless of candidate count (0, 1, or 2+) — it always becomes `("possible_reference", <candidates found, possibly empty>)`.
- **`append_matched_events`'s substring rule**: comparing normalized names via `core.matching.normalize_name`. One name a substring of another (either direction) → same person, longer name wins (replaces the shorter existing record, or rejects a shorter new candidate with `issue_type: "shorter_duplicate"`). No substring relationship → different people, both kept. This replaces the existing unconditional `"conflicting_subject"` rejection.
- **Only a genuine LLM `subject: null` response is cached as confirmed-no-subject** — never a missing/corrupt result file or a dropped record, since those are technical failures, not judgments. Cache key is the event's raw `text`; cache value is the `PROMPT_VERSION` in effect when confirmed.
- **`PROMPT_VERSION` starts at `1`** (Task 4) and is bumped to `2` (Task 6) exactly when `subject_prompt.md`'s content changes in a way that could plausibly change results. A cache entry only counts as still-valid when its stored version equals the *current* `PROMPT_VERSION`.
- **No changes to `src/ingest/enrichment.py`, `src/ingest/llm_utils.py`, `src/ingest/sources/wikidata.py`, or `src/ingest/resolve_wikidata.py`.** Nothing here retroactively touches the original 1,232 already-migrated events or live Supabase data. No UI changes.

---

### Task 1: `classify_event` — multi-match contract and commemorative heuristic

**Files:**
- Modify: `src/ingest/match_events.py:98-130` (the `classify_event` function)
- Test: `tests/test_match_events.py:1-69` (rewrite the `classify_event`-focused tests; leave `test_widened_lookup_*` and everything from line 105 on untouched — those belong to later tasks)

**Interfaces:**
- Consumes: `ingest.name_index.find_names_in_text`, `ingest.pipeline.calculate_age` (both unchanged, already imported).
- Produces: `classify_event(event, automaton, births_lookup) -> Tuple[str, object]` where status is one of `"matched"`, `"possible_reference"`, `"unmatched"`, `"implausible"`, `"unusable"`. For `"matched"`, payload is `{"matched": List[Dict], "implausible": List[str]}` — `"matched"` is always non-empty, `"implausible"` may be empty. For `"possible_reference"`, payload is `List[str]` (normalized candidate names found, possibly empty). For `"implausible"` (all candidates failed), payload is `List[str]`. Task 2 consumes this exact contract.

- [ ] **Step 1: Write the failing tests** (replace the whole top section of `tests/test_match_events.py`, from the top through line 69, with this)

```python
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
    assert payload["implausible"] == []
    assert len(payload["matched"]) == 1
    record = payload["matched"][0]
    assert record["name"] == "Albert Einstein"
    # 1879-03-14 to 1905-11-21, verified: (date(1905,11,21) - date(1879,3,14)).days
    assert record["age"] == 9748
    assert record["text"] == "Albert Einstein published a paper."


def test_two_known_names_both_become_separate_matched_records():
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(_event("Marie Curie wrote to Albert Einstein."), automaton, lookup)

    assert status == "matched"
    assert payload["implausible"] == []
    names = sorted(record["name"] for record in payload["matched"])
    assert names == ["Albert Einstein", "Marie Curie"]


def test_multi_candidate_event_can_partially_fail_the_plausibility_bound():
    # 1870 predates Einstein's 1879 birth (a negative, implausible age) while
    # Curie (born 1867) already existed and gets a plausible age.
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("Marie Curie wrote to Albert Einstein.", year=1870), automaton, lookup
    )

    assert status == "matched"
    assert [record["name"] for record in payload["matched"]] == ["Marie Curie"]
    assert payload["implausible"] == ["Albert Einstein"]


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
    assert payload == ["Albert Einstein"]


def test_non_numeric_year_is_unusable():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(
        {"year": "c. 1300", "month": 1, "day": 1, "text": "Albert Einstein appears."}, automaton, lookup
    )

    assert status == "unusable"


def test_named_after_phrase_routes_to_possible_reference_with_a_plausible_single_match():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("Einstein Elementary School, named after Albert Einstein, opens its doors."),
        automaton,
        lookup,
    )

    assert status == "possible_reference"
    assert payload == ["albert einstein"]


def test_named_after_phrase_routes_to_possible_reference_with_no_known_candidates():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("A new bridge, named after a local poet, opens to traffic."), automaton, lookup
    )

    assert status == "possible_reference"
    assert payload == []


def test_named_after_phrase_routes_to_possible_reference_with_multiple_known_candidates():
    lookup = _lookup(EINSTEIN, CURIE)
    automaton = build_name_index(lookup.keys())

    status, payload = classify_event(
        _event("The Einstein-Curie Prize, named after Albert Einstein and Marie Curie, is awarded."),
        automaton,
        lookup,
    )

    assert status == "possible_reference"
    assert sorted(payload) == ["albert einstein", "marie curie"]


def test_anniversary_phrase_also_routes_to_possible_reference():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(
        _event("On the 10th anniversary of Albert Einstein's famous paper, a conference is held."),
        automaton,
        lookup,
    )

    assert status == "possible_reference"


def test_ordinary_text_without_a_commemorative_phrase_is_unaffected():
    lookup = _lookup(EINSTEIN)
    automaton = build_name_index(lookup.keys())

    status, _ = classify_event(_event("Albert Einstein published a paper."), automaton, lookup)

    assert status == "matched"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: FAIL — `test_two_known_names_both_become_separate_matched_records` and the `possible_reference`/multi-candidate tests fail because `classify_event` doesn't have this behavior yet (old code returns `"ambiguous"` for 2+ names and has no commemorative check). `test_single_known_name_matches_and_computes_age` and `test_event_before_the_persons_birth_is_implausible` fail on the payload shape (`payload["name"]` vs. the old bare-dict shape).

- [ ] **Step 3: Replace `classify_event` in `src/ingest/match_events.py`**

```python
COMMEMORATIVE_PATTERNS = (
    "named after", "in honor of", "in honour of", "anniversary of",
    "in memory of", "dedicated to", "commemorat",
)


def _mentions_commemorative_reference(text: str) -> bool:
    """True if text suggests a person is only referenced, not acting.

    A school "named after" someone, or text describing an "anniversary of"
    someone's death, does not mean that person did anything on this date -
    the age bound alone can't catch this within a normal lifespan window.
    Checked case-insensitively against the raw text (these are literal
    English phrases, not names, so normalize_name's punctuation-stripping
    isn't relevant here).
    """
    lowered = text.lower()
    return any(pattern in lowered for pattern in COMMEMORATIVE_PATTERNS)


def classify_event(event: Dict, automaton, births_lookup: Dict[str, Dict]) -> Tuple[str, object]:
    """Classify one event against the known-person index.

    Returns (status, payload):
    - ("matched", {"matched": [...], "implausible": [...]})
        "matched": one record per candidate whose computed age is plausible
        (event dict + name + age) - always non-empty when status is
        "matched". A single-candidate text yields a one-element list; a
        multi-candidate text (several distinct, non-nested real people
        literally named in the text) yields one per person.
        "implausible": names of candidates from the same text that
        individually failed the bound - possible even when others in the
        same text passed.
    - ("possible_reference", names)  - the text matched a commemorative/
        named-after phrase; never auto-accepted regardless of candidate
        count. names is whatever known candidates were found (may be []).
    - ("unmatched", None)            - no known person named, and no
        commemorative phrase either.
    - ("implausible", names)         - every candidate found failed the
        bound (none passed).
    - ("unusable", None)             - the event has no numeric year.
    """
    year = event.get("year")
    if year is None or not str(year).isdigit():
        return "unusable", None

    names = find_names_in_text(automaton, event["text"])

    if _mentions_commemorative_reference(event["text"]):
        return "possible_reference", names

    if not names:
        return "unmatched", None

    matched: List[Dict] = []
    implausible: List[str] = []
    for name in names:
        birth = births_lookup.get(name)
        if birth is None:
            continue
        age = calculate_age(
            birth["year"], birth["month"], birth["day"],
            int(year), int(event["month"]), int(event["day"]),
        )
        if age is None or not (0 <= age <= MAX_AGE_DAYS):
            implausible.append(birth["name"])
        else:
            matched.append({**event, "name": birth["name"], "age": age})

    if matched:
        return "matched", {"matched": matched, "implausible": implausible}
    if implausible:
        return "implausible", implausible
    return "unmatched", None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: The 11 tests in this file's top section PASS. (The tests below line 105 — `test_widened_lookup_*` and everything after — are untouched by this step and should still pass too, since Task 1 doesn't change `load_widened_births_lookup`, `append_matched_events`, or `run_stage_one`. `test_run_stage_one_*` tests will actually FAIL at this point, since `run_stage_one` hasn't been updated for the new contract yet — that's Task 2. If you see those specific failures, that's expected; confirm the 11 `classify_event`-focused tests above pass and move on.)

- [ ] **Step 5: Commit**

```bash
git add src/ingest/match_events.py tests/test_match_events.py
git commit -m "feat: classify_event auto-matches every co-subject, adds commemorative-reference heuristic"
```

---

### Task 2: `run_stage_one` — consume the new `classify_event` contract

**Files:**
- Modify: `src/ingest/match_events.py:209-258` (the `run_stage_one` function; add one new module-level helper above it)
- Test: `tests/test_match_events.py:137-204` (the `run_stage_one`-focused tests — replace `test_run_stage_one_splits_events_and_writes_pending`, add two new tests; leave `test_run_stage_one_records_implausible_matches_for_review` and `test_rerunning_stage_one_does_not_duplicate_review_entries` as they are, they don't need changes)

**Interfaces:**
- Consumes: `classify_event` (Task 1's new contract).
- Produces: `run_stage_one(...) -> Dict[str, int]` with counts keyed `matched`, `possible_reference`, `unmatched`, `implausible`, `unusable`, `appended` — `"ambiguous"` is gone from this dict. Task 6's funnel-test update and Task 7 both depend on this exact key set.

- [ ] **Step 1: Write the failing tests** (in `tests/test_match_events.py`, replace `test_run_stage_one_splits_events_and_writes_pending` — lines 137-177 — with the following three tests; everything else in the file stays as-is)

```python
def test_run_stage_one_splits_events_and_writes_pending(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [
                {"year": 1905, "month": 11, "day": 21, "text": "Albert Einstein published a paper."},
                {"year": 1911, "month": 12, "day": 10, "text": "A committee met in Oslo."},
            ]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}]),
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

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert len(pending) == 1
    assert pending[0]["reason"] == "unmatched"

    matched = json.loads(matched_path.read_text(encoding="utf-8"))
    assert matched[0]["name"] == "Albert Einstein"


def test_run_stage_one_auto_matches_every_candidate_in_a_multi_person_event(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [{"year": 1905, "month": 1, "day": 1, "text": "Marie Curie wrote to Albert Einstein."}]
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

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=matched_path,
        pending_path=tmp_path / "subject_pending.json",
        review_path=tmp_path / "matching_review.json",
    )

    assert counts["matched"] == 1  # one EVENT classified matched...
    assert counts["appended"] == 2  # ...producing two separate person-records

    matched = json.loads(matched_path.read_text(encoding="utf-8"))
    names = sorted(record["name"] for record in matched)
    assert names == ["Albert Einstein", "Marie Curie"]


def test_run_stage_one_routes_named_after_text_to_possible_reference(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text(
        json.dumps(
            [{"year": 1950, "month": 1, "day": 1, "text": "A new school, named after Albert Einstein, opens."}]
        ),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text(
        json.dumps([{"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14}]),
        encoding="utf-8",
    )
    pending_path = tmp_path / "subject_pending.json"

    counts = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        pending_path=pending_path,
        review_path=tmp_path / "matching_review.json",
    )

    assert counts["possible_reference"] == 1
    assert counts["appended"] == 0

    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending[0]["reason"] == "possible_reference"
    assert pending[0]["candidates"] == ["albert einstein"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: FAIL — `run_stage_one` still expects the old `classify_event` contract (`counts[status] += 1` would `KeyError` on `"possible_reference"`, and `if status == "matched": matched.append(payload)` would append a dict-of-lists instead of an event record).

- [ ] **Step 3: Replace `run_stage_one` in `src/ingest/match_events.py`** (add the new helper just above it)

```python
def _implausible_review_entries(event: Dict, names: List[str]) -> List[Dict]:
    """One review entry per name whose computed age failed the plausibility bound."""
    return [
        {
            "stage": "stage_1",
            "issue_type": "implausible_age",
            "name": name,
            "text": event["text"],
            "detail": f"age for {name!r} outside 0..{MAX_AGE_DAYS} days",
        }
        for name in names
    ]


def run_stage_one(
    events_path: Path = DATA_DIR / "historical_events.json",
    births_path: Path = WIDENED_BIRTHS_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    pending_path: Path = SUBJECT_PENDING_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> Dict[str, int]:
    """Classify every scraped event, appending matches and queueing the rest for Stage 2.

    Writes three files: matched events (appended - possibly several per event
    when a text names multiple real people), the Stage 2 queue (unmatched +
    possible-reference events), and review entries for implausible ages.
    """
    events = load_json(events_path)
    births_lookup = load_widened_births_lookup(births_path)
    automaton = build_name_index(births_lookup.keys())

    counts = {"matched": 0, "possible_reference": 0, "unmatched": 0, "implausible": 0, "unusable": 0}
    matched: List[Dict] = []
    pending: List[Dict] = []
    review: List[Dict] = []

    for event in events:
        status, payload = classify_event(event, automaton, births_lookup)
        counts[status] += 1

        if status == "matched":
            matched.extend(payload["matched"])
            review.extend(_implausible_review_entries(event, payload["implausible"]))
        elif status == "possible_reference":
            entry = {**event, "reason": "possible_reference"}
            if payload:
                entry["candidates"] = payload
            pending.append(entry)
        elif status == "unmatched":
            pending.append({**event, "reason": "unmatched"})
        elif status == "implausible":
            review.extend(_implausible_review_entries(event, payload))

    counts["appended"] = append_matched_events(matched, matched_path, review_path)

    pending_path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(pending_path, pending)
    write_review_entries(dedup_against_file(review_path, review), review_path)

    return counts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: All tests in the file PASS (including the untouched `test_run_stage_one_records_implausible_matches_for_review` and `test_rerunning_stage_one_does_not_duplicate_review_entries`, and everything below line 105 which Task 1/2 don't otherwise touch).

- [ ] **Step 5: Commit**

```bash
git add src/ingest/match_events.py tests/test_match_events.py
git commit -m "feat: run_stage_one consumes the multi-match classify_event contract"
```

---

### Task 3: `append_matched_events` — substring correction rule

**Files:**
- Modify: `src/ingest/match_events.py:151-206` (the `append_matched_events` function)
- Test: `tests/test_match_events.py:233-252` (replace `test_same_text_under_a_different_name_is_reviewed_not_appended`; add three new tests. `test_append_matched_events_creates_the_file` and `test_append_matched_events_skips_duplicates_by_name_and_text` — lines 110-134 — are untouched, they still pass unchanged)

**Interfaces:**
- Consumes: `core.matching.normalize_name` (already imported in this file).
- Produces: `append_matched_events(new_events, path=..., review_path=...) -> int` — same signature as today, still returns the count of records actually added (a correction counts as 1 added, same as a fresh multi-subject addition).

- [ ] **Step 1: Write the failing tests** (in `tests/test_match_events.py`, replace `test_same_text_under_a_different_name_is_reviewed_not_appended` — the last function in the file, lines 233-252 — with these four tests)

```python
def test_append_matched_events_replaces_a_shorter_existing_match_with_a_fuller_name(tmp_path):
    path = tmp_path / "events_with_age.json"
    review_path = tmp_path / "matching_review.json"
    text = "The founder of Pakistan, Quaid-i-Azam Muhammad Ali Jinnah, joins a school."
    path.write_text(
        json.dumps([{"name": "Muhammad Ali", "text": text, "age": 43000}]), encoding="utf-8"
    )

    added = append_matched_events(
        [{"name": "Muhammad Ali Jinnah", "text": text, "age": 4000}], path, review_path=review_path
    )

    assert added == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored) == 1
    assert stored[0]["name"] == "Muhammad Ali Jinnah"
    assert stored[0]["age"] == 4000


def test_append_matched_events_rejects_a_shorter_candidate_when_the_fuller_name_is_already_recorded(tmp_path):
    path = tmp_path / "events_with_age.json"
    review_path = tmp_path / "matching_review.json"
    text = "The founder of Pakistan, Quaid-i-Azam Muhammad Ali Jinnah, joins a school."
    path.write_text(
        json.dumps([{"name": "Muhammad Ali Jinnah", "text": text, "age": 4000}]), encoding="utf-8"
    )

    added = append_matched_events(
        [{"name": "Muhammad Ali", "text": text, "age": 43000}], path, review_path=review_path
    )

    assert added == 0
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored) == 1
    assert stored[0]["name"] == "Muhammad Ali Jinnah"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review[0]["issue_type"] == "shorter_duplicate"
    assert review[0]["name"] == "Muhammad Ali"


def test_append_matched_events_keeps_two_unrelated_names_for_the_same_text(tmp_path):
    path = tmp_path / "events_with_age.json"
    text = "Churchill and Stalin met in Potsdam."
    path.write_text(
        json.dumps([{"name": "Winston Churchill", "text": text, "age": 1}]), encoding="utf-8"
    )

    added = append_matched_events(
        [{"name": "Joseph Stalin", "text": text, "age": 2}], path, review_path=tmp_path / "matching_review.json"
    )

    assert added == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    names = sorted(entry["name"] for entry in stored)
    assert names == ["Joseph Stalin", "Winston Churchill"]


def test_append_matched_events_only_replaces_the_matching_co_subject(tmp_path):
    # Two existing co-subjects for the same text; only the substring-related one is touched.
    path = tmp_path / "events_with_age.json"
    text = "Muhammad Ali Jinnah met Liaquat Ali Khan."
    path.write_text(
        json.dumps(
            [
                {"name": "Muhammad Ali", "text": text, "age": 1},
                {"name": "Liaquat Ali Khan", "text": text, "age": 2},
            ]
        ),
        encoding="utf-8",
    )

    added = append_matched_events(
        [{"name": "Muhammad Ali Jinnah", "text": text, "age": 3}], path, review_path=tmp_path / "matching_review.json"
    )

    assert added == 1
    stored = json.loads(path.read_text(encoding="utf-8"))
    names = sorted(entry["name"] for entry in stored)
    assert names == ["Liaquat Ali Khan", "Muhammad Ali Jinnah"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: FAIL — the current code unconditionally rejects any second name for an existing text as `"conflicting_subject"`, so none of the four new tests get the correction/multi-subject behavior they assert.

- [ ] **Step 3: Replace `append_matched_events` in `src/ingest/match_events.py`**

```python
def append_matched_events(
    new_events: List[Dict],
    path: Path = EVENTS_WITH_AGE_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
) -> int:
    """Append matched events to path, applying the substring correction rule.

    A text can legitimately carry more than one matched record (several real
    people named in the same event - see classify_event's multi-candidate
    case). When a new (name, text) candidate's text already has a *different*
    stored name, the two normalized names are compared against every existing
    name recorded for that text:
    - One a substring of the other -> the same person, mis-truncated in one
      of the two records. The longer (more complete) name wins: if the new
      candidate is longer it replaces that one shorter existing record;
      otherwise the new candidate is rejected and logged to review
      (issue_type "shorter_duplicate"), since a fuller name is already
      recorded.
    - No substring relationship with any existing name for that text -> a
      genuinely different person, and a legitimate additional co-subject.
      Both are kept.

    Returns how many records were actually added (a correction counts as 1,
    same as a fresh addition). Safe to call repeatedly - Stage 1, 2 and 3 all
    append to the same file across separate runs.
    """
    try:
        existing = load_json(path)
    except FileNotFoundError:
        existing = []

    seen = {_event_key(event) for event in existing}
    names_by_text: Dict[object, Set[str]] = {}
    for event in existing:
        names_by_text.setdefault(event.get("text"), set()).add(event.get("name"))

    added = 0
    review_entries: List[Dict] = []
    for event in new_events:
        if _event_key(event) in seen:
            continue

        text = event.get("text")
        new_name = event.get("name")
        norm_new = normalize_name(new_name)

        replaced_name = None
        rejected_reason = None
        for existing_name in names_by_text.get(text, set()):
            norm_existing = normalize_name(existing_name)
            if norm_new == norm_existing:
                continue
            if norm_new in norm_existing or norm_existing in norm_new:
                if len(norm_new) > len(norm_existing):
                    replaced_name = existing_name
                else:
                    rejected_reason = f"a fuller name is already recorded: {existing_name!r}"
                break

        if rejected_reason:
            review_entries.append(
                {
                    "stage": "append",
                    "issue_type": "shorter_duplicate",
                    "name": new_name,
                    "text": text,
                    "detail": rejected_reason,
                }
            )
            continue

        if replaced_name is not None:
            existing = [
                e for e in existing if not (e.get("text") == text and e.get("name") == replaced_name)
            ]
            seen.discard((replaced_name, text))
            names_by_text[text].discard(replaced_name)

        existing.append(event)
        seen.add(_event_key(event))
        names_by_text.setdefault(text, set()).add(new_name)
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    save_to_json(path, existing)
    write_review_entries(dedup_against_file(review_path, review_entries), review_path)
    return added
```

Also update `_event_key`'s docstring (immediately above `append_matched_events`) to remove the now-inaccurate claim about conflicts being sent to review unconditionally:

```python
def _event_key(event: Dict):
    """Natural key for a matched event here: (name, text).

    Deliberately not the same key as elsewhere in the pipeline, and the
    difference matters. llm_utils._event_key and llm_utils.get_pending_events
    key on `text` alone, while migrate_to_supabase.filter_new_entries keys on
    (name, text) like this function. So a text stored twice under two
    different names is a duplicate to llm_utils but two distinct records
    here - which is legitimate when they're genuinely different people (see
    append_matched_events's substring rule for how that's decided).
    """
    return (event.get("name"), event.get("text"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_match_events.py -v`
Expected: All tests in the file PASS.

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `venv\Scripts\python.exe -m pytest -v`
Expected: PASS. (`tests/test_subject_extraction.py` and `tests/test_resolve_wikidata.py` both call `append_matched_events` indirectly through `merge_subject_chunk`/`run_stage_three` with single, non-conflicting names in their fixtures, so they're unaffected by this change — but confirm.)

- [ ] **Step 6: Commit**

```bash
git add src/ingest/match_events.py tests/test_match_events.py
git commit -m "feat: append_matched_events corrects truncated matches instead of just flagging conflicts"
```

---

### Task 4: No-subject cache infrastructure

**Files:**
- Modify: `src/ingest/subject_extraction.py` (add imports, two new constants, two new functions — near the top, after the existing `PROMPT_TEMPLATE_PATH`/`SUBJECT_CHUNK_DIR`/`CHUNK_SIZE` constants)
- Test: `tests/test_subject_extraction.py` (append new tests at the end)

**Interfaces:**
- Produces: `subject_extraction.PROMPT_VERSION: int` (starts at `1`), `subject_extraction.NO_SUBJECT_CACHE_PATH: Path` (`DATA_DIR / "no_subject_cache.json"`), `subject_extraction.load_no_subject_cache(path=NO_SUBJECT_CACHE_PATH) -> Dict[str, int]`, `subject_extraction.save_no_subject_cache(cache, path=NO_SUBJECT_CACHE_PATH) -> None`. Task 5 wires these into `prepare_subject_chunks`/`merge_subject_chunk`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_subject_extraction.py`)

```python
from ingest.subject_extraction import load_no_subject_cache, save_no_subject_cache


def test_load_no_subject_cache_returns_empty_when_missing(tmp_path):
    assert load_no_subject_cache(tmp_path / "absent.json") == {}


def test_no_subject_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "cache.json"
    save_no_subject_cache({"A treaty was signed.": 1}, path)

    assert load_no_subject_cache(path) == {"A treaty was signed.": 1}


def test_load_no_subject_cache_survives_a_truncated_file(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert load_no_subject_cache(path) == {}


def test_save_no_subject_cache_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "cache.json"
    save_no_subject_cache({"x": 1}, path)

    assert not path.with_name(path.name + ".tmp").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_no_subject_cache'`

- [ ] **Step 3: Add the constants and functions to `src/ingest/subject_extraction.py`**

Add a new `import os` line next to the existing `import json` line near the top of the file (two separate `import` statements, not combined). Then, just below the existing `PROMPT_TEMPLATE_PATH = Path(__file__).parent / "subject_prompt.md"` / `SUBJECT_CHUNK_DIR` / `CHUNK_SIZE` block:

```python
NO_SUBJECT_CACHE_PATH = DATA_DIR / "no_subject_cache.json"
PROMPT_VERSION = 1


def load_no_subject_cache(path: Path = NO_SUBJECT_CACHE_PATH) -> Dict[str, int]:
    """text -> the PROMPT_VERSION in effect when it was confirmed to have no subject.

    A missing or corrupt cache file is treated as empty - same tolerance as
    ingest.sources.wikidata's cache, since Stage 2 runs into the same
    crash-mid-write risk during a long chunk-processing session.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_no_subject_cache(cache: Dict[str, int], path: Path = NO_SUBJECT_CACHE_PATH) -> None:
    """Write atomically (temp file + os.replace) so a crash mid-write can't corrupt the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: PASS (all tests in the file, including the 4 new ones).

- [ ] **Step 5: Commit**

```bash
git add src/ingest/subject_extraction.py tests/test_subject_extraction.py
git commit -m "feat: add version-tagged no-subject cache infrastructure"
```

---

### Task 5: Wire the cache into `prepare_subject_chunks` and `merge_subject_chunk`

**Files:**
- Modify: `src/ingest/subject_extraction.py` (the `prepare_subject_chunks` and `merge_subject_chunk` functions)
- Test: `tests/test_subject_extraction.py` (append new tests)

**Interfaces:**
- Consumes: `load_no_subject_cache`, `save_no_subject_cache`, `PROMPT_VERSION`, `NO_SUBJECT_CACHE_PATH` (Task 4).
- Produces: `prepare_subject_chunks(pending_path=..., chunk_size=..., chunk_dir=..., cache_path=NO_SUBJECT_CACHE_PATH) -> List[Path]` (new `cache_path` parameter) and `merge_subject_chunk(..., no_subject_cache_path=NO_SUBJECT_CACHE_PATH) -> Dict[str, int]` (new `no_subject_cache_path` parameter), same return shape as before.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_subject_extraction.py`)

```python
def test_prepare_subject_chunks_skips_a_text_already_confirmed_at_the_current_prompt_version(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text(
        json.dumps(
            [
                {"text": "Already checked.", "reason": "unmatched"},
                {"text": "Never checked.", "reason": "unmatched"},
            ]
        ),
        encoding="utf-8",
    )
    cache_path = tmp_path / "no_subject_cache.json"
    save_no_subject_cache({"Already checked.": PROMPT_VERSION}, cache_path)

    paths = prepare_subject_chunks(
        pending_path=pending_path, chunk_dir=tmp_path / "chunks", cache_path=cache_path
    )

    chunked = json.loads(paths[0].read_text(encoding="utf-8"))
    assert [entry["text"] for entry in chunked] == ["Never checked."]


def test_prepare_subject_chunks_includes_a_text_cached_at_a_stale_prompt_version(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text(
        json.dumps([{"text": "Checked under an older prompt.", "reason": "unmatched"}]),
        encoding="utf-8",
    )
    cache_path = tmp_path / "no_subject_cache.json"
    save_no_subject_cache({"Checked under an older prompt.": PROMPT_VERSION - 1}, cache_path)

    paths = prepare_subject_chunks(
        pending_path=pending_path, chunk_dir=tmp_path / "chunks", cache_path=cache_path
    )

    chunked = json.loads(paths[0].read_text(encoding="utf-8"))
    assert len(chunked) == 1


def test_merge_subject_chunk_caches_a_confirmed_no_subject_result(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(
        json.dumps([{"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed."}]),
        encoding="utf-8",
    )
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps([{"text": "A treaty was signed.", "subject": None}]), encoding="utf-8"
    )
    births_path = tmp_path / "births.json"
    births_path.write_text("[]", encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    cache = load_no_subject_cache(cache_path)
    assert cache["A treaty was signed."] == PROMPT_VERSION


def test_merge_subject_chunk_does_not_cache_a_rejected_suggestion(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(
        json.dumps([{"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed."}]),
        encoding="utf-8",
    )
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(
        json.dumps([{"text": "A treaty was signed.", "subject": "Someone Not In The Text"}]),
        encoding="utf-8",
    )
    births_path = tmp_path / "births.json"
    births_path.write_text("[]", encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    merge_subject_chunk(
        chunk_path,
        result_path,
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    assert load_no_subject_cache(cache_path) == {}


def test_merge_subject_chunk_does_not_cache_when_the_result_file_is_missing(tmp_path):
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps([EVENT]), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps([EINSTEIN]), encoding="utf-8")
    cache_path = tmp_path / "no_subject_cache.json"

    merge_subject_chunk(
        chunk_path,
        tmp_path / "does_not_exist.json",
        births_path=births_path,
        matched_path=tmp_path / "events_with_age.json",
        wikidata_pending_path=tmp_path / "wikidata_pending.json",
        review_path=tmp_path / "matching_review.json",
        no_subject_cache_path=cache_path,
    )

    assert load_no_subject_cache(cache_path) == {}
```

Note: the last test reuses `EVENT` and `EINSTEIN`, already defined near the top of `tests/test_subject_extraction.py` — don't redefine them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: FAIL — `TypeError: prepare_subject_chunks() got an unexpected keyword argument 'cache_path'` and similarly for `merge_subject_chunk`'s `no_subject_cache_path`.

- [ ] **Step 3: Update `prepare_subject_chunks` in `src/ingest/subject_extraction.py`**

```python
def prepare_subject_chunks(
    pending_path: Path = SUBJECT_PENDING_PATH,
    chunk_size: int = CHUNK_SIZE,
    chunk_dir: Path = SUBJECT_CHUNK_DIR,
    cache_path: Path = NO_SUBJECT_CACHE_PATH,
) -> List[Path]:
    """Split the Stage 2 queue into numbered chunk files for a subagent to process.

    Events already confirmed to have no subject under the current
    PROMPT_VERSION are skipped - Stage 1 requeues everything unmatched on
    every run, with no memory of what Stage 2 already checked, so this
    filtering has to happen here.
    """
    pending = load_json(pending_path)
    cache = load_no_subject_cache(cache_path)
    pending = [event for event in pending if cache.get(event.get("text")) != PROMPT_VERSION]

    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        path = chunk_dir / f"chunk_{index:04d}.json"
        save_to_json(path, pending[start : start + chunk_size])
        paths.append(path)
    return paths
```

- [ ] **Step 4: Update `merge_subject_chunk` in `src/ingest/subject_extraction.py`**

```python
def merge_subject_chunk(
    chunk_path,
    result_path,
    births_path: Path = WIDENED_BIRTHS_PATH,
    matched_path: Path = EVENTS_WITH_AGE_PATH,
    wikidata_pending_path: Path = WIKIDATA_PENDING_PATH,
    review_path: Path = MATCHING_REVIEW_PATH,
    no_subject_cache_path: Path = NO_SUBJECT_CACHE_PATH,
) -> Dict[str, int]:
    """Validate one chunk's subagent output and route each event to its next step.

    Records are matched back to the chunk by `text`, not list position, so a
    subagent that reorders or drops records is still handled. A missing or
    malformed result file, or a record the subagent dropped, leaves that
    event as "no_subject" for this run - but only a genuine `subject: null`
    response is cached (no_subject_cache_path) as a confirmed verdict, so a
    technical failure (missing file) doesn't get mistaken for an LLM having
    actually looked and found nobody.
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
    no_subject_texts: List[str] = []

    for event in chunk:
        result = results_by_text.get(event.get("text"))
        status, payload = route_subject(event, (result or {}).get("subject"), births_lookup)
        counts[status] += 1

        if status == "matched":
            matched.append(payload)
        elif status == "wikidata_candidate":
            wikidata_pending.append({**event, "subject": payload})
        else:
            if status == "no_subject" and result is not None:
                no_subject_texts.append(event.get("text"))
            review.append(
                {
                    "stage": "stage_2",
                    "issue_type": status,
                    "name": result.get("subject") if result else None,
                    "text": event.get("text"),
                    "detail": payload if status == "rejected" else "no subject identified in the text",
                }
            )

    counts["appended"] = append_matched_events(matched, matched_path, review_path)
    _append_json_list(wikidata_pending_path, wikidata_pending)
    write_review_entries(dedup_against_file(review_path, review), review_path)

    if no_subject_texts:
        cache = load_no_subject_cache(no_subject_cache_path)
        for text in no_subject_texts:
            cache[text] = PROMPT_VERSION
        save_no_subject_cache(cache, no_subject_cache_path)

    return counts
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: PASS, including the pre-existing tests from before this task — `test_merge_subject_chunk_survives_a_missing_result_file`'s assertions (`counts["no_subject"] == 1`, `counts["matched"] == 0`) are unaffected by this change and should still pass unmodified.

- [ ] **Step 6: Run the full suite**

Run: `venv\Scripts\python.exe -m pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ingest/subject_extraction.py tests/test_subject_extraction.py
git commit -m "feat: skip events already confirmed to have no subject on rerun"
```

---

### Task 6: Commemorative-reference prompt instructions and `PROMPT_VERSION` bump

**Files:**
- Modify: `src/ingest/subject_prompt.md` (full replacement)
- Modify: `src/ingest/subject_extraction.py:<PROMPT_VERSION line>` (bump the constant)
- Test: `tests/test_subject_extraction.py` (append new tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `subject_extraction.PROMPT_VERSION == 2`. No function signature changes.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_subject_extraction.py`)

```python
def test_build_prompt_explains_named_after_and_anniversary_references():
    prompt = build_prompt()
    assert "named after" in prompt.lower()
    assert "anniversary" in prompt.lower()


def test_build_prompt_asks_for_the_persons_full_name():
    prompt = build_prompt()
    assert "full name" in prompt.lower()


def test_build_prompt_documents_the_possible_reference_reason():
    prompt = build_prompt()
    assert "possible_reference" in prompt


def test_prompt_version_was_bumped_for_the_commemorative_instructions():
    from ingest.subject_extraction import PROMPT_VERSION

    assert PROMPT_VERSION == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: FAIL — `subject_prompt.md` doesn't yet mention "named after", "anniversary", "full name", or "possible_reference", and `PROMPT_VERSION` is still `1`.

- [ ] **Step 3: Replace `src/ingest/subject_prompt.md` in full**

```markdown
# Event subject-identification instructions

You will receive a JSON array of historical event records, each with:
- `text`: the raw event description (as scraped from Wikipedia's "on this day" data)
- `year`, `month`, `day`: the event's date
- `reason`: why this event needs you — `"unmatched"` (no known person was found in the
  text) or `"possible_reference"` (the text may only *reference* a person — e.g. something
  named after them, or an anniversary/memorial of them — rather than describe something
  they did)
- `candidates`: present only when a known person's name was found in the text — given as
  context, not as a suggestion to prefer over what you find yourself in `text`

For each record, return a JSON object with these fields:

- `text`: copied unchanged from the input. This is how the record is matched back, so it
  must be byte-identical to what you were given.
- `subject`: the name of the person who is the grammatical subject of `text` — the person
  the event is *about*, who performed or underwent the action. Rules:
  - Copy the name **verbatim as it appears in `text`**, and copy the person's **full name**
    as given, not a shortened piece of it. If the text says "Napoleon", return "Napoleon".
    If the text gives a longer form ("Quaid-i-Azam Muhammad Ali Jinnah"), return the full
    name as it appears — "Muhammad Ali Jinnah", not just "Muhammad Ali". Do not expand
    "Bach" into "Johann Sebastian Bach" or correct spelling — only use what's actually
    written in `text`.
  - **A person referenced, not acting, is not the subject.** If `text` describes something
    *named after* a person (a school, an award, a ship, a place), or describes an
    *anniversary* or *memorial* of a person's death or an earlier event, that person did not
    do anything on this date — return `null` for them, unless `text` separately, genuinely
    describes a different named person doing something on this date.
  - Return `null` if `text` names no person at all (a treaty, a battle between countries,
    an organization, a natural disaster), or if you cannot tell who the subject is. `null`
    is the correct, expected answer for many records — do not invent a person to avoid
    returning it.
  - Never return a name that does not literally appear in `text`.

Return a JSON array in the same order as the input, one object per input record. Return
every record you were given — don't skip any.
```

- [ ] **Step 4: Bump `PROMPT_VERSION` in `src/ingest/subject_extraction.py`**

Change `PROMPT_VERSION = 1` to `PROMPT_VERSION = 2`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest tests/test_subject_extraction.py -v`
Expected: PASS, including `test_build_prompt_describes_the_subject_field` (still passes — the phrase `"verbatim as it appears in \`text\`"` and `` `subject` `` are both still present in the new prompt text) and every test from Tasks 4-5 (the version-1-vs-2 stale-cache test still passes since it computes `PROMPT_VERSION - 1` relative to whatever the constant currently is, not a hardcoded `1`).

- [ ] **Step 6: Run the full suite**

Run: `venv\Scripts\python.exe -m pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ingest/subject_prompt.md src/ingest/subject_extraction.py tests/test_subject_extraction.py
git commit -m "feat: add commemorative-reference prompt instructions, bump PROMPT_VERSION to 2"
```

---

### Task 7: Extend the funnel seam test, full-suite verification

**Files:**
- Modify: `tests/test_funnel_integration.py` (full replacement of the fixtures and the one test function)

**Interfaces:**
- Consumes: `run_stage_one`, `prepare_subject_chunks`, `merge_subject_chunk`, `resolve_wikidata.run_stage_three` — all from Tasks 1-6, unchanged signatures from Stage 3's side (only `match_events`/`subject_extraction` changed).

- [ ] **Step 1: Update the fixtures and test** (replace the whole file from line 15 — the `EVENTS` constant — through the end)

```python
EVENTS = [
    # Stage 1: exactly one known person named.
    {"year": 1905, "month": 11, "day": 21, "text": "Albert Einstein published a paper."},
    # Stage 1: "John Smith" is contained in "John Smith Senior" - one person, not two.
    {"year": 1863, "month": 11, "day": 19, "text": "John Smith Senior addressed the assembly."},
    # Stage 1: two known, unrelated people -> both auto-matched as separate co-subjects.
    {"year": 1905, "month": 1, "day": 1, "text": "Marie Curie wrote to Albert Einstein."},
    # Stage 1: nobody known -> Stage 2 names someone we don't have -> Stage 3.
    {"year": 1905, "month": 1, "day": 1, "text": "Mileva Maric reviewed the draft."},
    # Stage 1: nobody known -> Stage 2 finds no subject -> review only.
    {"year": 1919, "month": 6, "day": 28, "text": "A treaty was signed at Versailles."},
]

BIRTHS = [
    {"name": "Albert Einstein", "year": 1879, "month": 3, "day": 14},
    {"name": "Marie Curie", "year": 1867, "month": 11, "day": 7},
    {"name": "John Smith", "year": 1800, "month": 1, "day": 3},
    {"name": "John Smith Senior", "year": 1820, "month": 5, "day": 9},
]

SUBAGENT_RESULTS = [
    {"text": "Mileva Maric reviewed the draft.", "subject": "Mileva Maric"},
    {"text": "A treaty was signed at Versailles.", "subject": None},
]


def test_events_flow_from_stage_one_through_stage_three(tmp_path, monkeypatch):
    events_path = tmp_path / "historical_events.json"
    events_path.write_text(json.dumps(EVENTS), encoding="utf-8")
    births_path = tmp_path / "births.json"
    births_path.write_text(json.dumps(BIRTHS), encoding="utf-8")

    matched_path = tmp_path / "events_with_age.json"
    pending_path = tmp_path / "subject_pending.json"
    review_path = tmp_path / "matching_review.json"
    wikidata_pending_path = tmp_path / "wikidata_pending.json"

    stage_one = run_stage_one(
        events_path=events_path,
        births_path=births_path,
        matched_path=matched_path,
        pending_path=pending_path,
        review_path=review_path,
    )
    # Einstein-alone, John Smith Senior, and the Curie+Einstein multi-subject event.
    assert stage_one["matched"] == 3
    assert stage_one["unmatched"] == 2
    assert stage_one["appended"] == 4  # Einstein x2 (different texts), John Smith Senior, Curie

    no_subject_cache_path = tmp_path / "no_subject_cache.json"
    chunks = prepare_subject_chunks(
        pending_path=pending_path,
        chunk_size=100,
        chunk_dir=tmp_path / "subject_chunks",
        cache_path=no_subject_cache_path,
    )
    assert len(chunks) == 1
    chunked_texts = {event["text"] for event in json.loads(chunks[0].read_text(encoding="utf-8"))}
    assert chunked_texts == {"Mileva Maric reviewed the draft.", "A treaty was signed at Versailles."}

    result_path = chunks[0].with_name("chunk_0000_result.json")
    result_path.write_text(json.dumps(SUBAGENT_RESULTS), encoding="utf-8")

    stage_two = merge_subject_chunk(
        chunks[0],
        result_path,
        births_path=births_path,
        matched_path=matched_path,
        wikidata_pending_path=wikidata_pending_path,
        review_path=review_path,
        no_subject_cache_path=no_subject_cache_path,
    )
    assert stage_two["matched"] == 0
    assert stage_two["wikidata_candidate"] == 1
    assert stage_two["no_subject"] == 1

    monkeypatch.setattr(
        wikidata,
        "lookup_birth_date",
        lambda name, event_year, cache: {
            "status": "resolved", "name": name, "year": 1875, "month": 12, "day": 19, "qid": "Q1"
        },
    )
    stage_three = resolve_wikidata.run_stage_three(
        pending_path=wikidata_pending_path,
        matched_path=matched_path,
        review_path=review_path,
        cache_path=tmp_path / "cache.json",
    )
    assert stage_three["resolved"] == 1

    stored = json.loads(matched_path.read_text(encoding="utf-8"))
    assert len(stored) == 5
    names = sorted(event["name"] for event in stored)
    assert names == ["Albert Einstein", "Albert Einstein", "John Smith Senior", "Marie Curie", "Mileva Maric"]
    for event in stored:
        assert set(event) >= {"year", "month", "day", "text", "name", "age"}
        assert isinstance(event["age"], int) and event["age"] > 0

    # The multi-subject event: both co-subjects share the same text, each with their own age.
    co_subject_text = "Marie Curie wrote to Albert Einstein."
    co_subject_records = [event for event in stored if event["text"] == co_subject_text]
    assert sorted(event["name"] for event in co_subject_records) == ["Albert Einstein", "Marie Curie"]

    # The one event nobody could be found for is in review, not in the output.
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["no_subject"]
    assert review[0]["text"] == "A treaty was signed at Versailles."
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_funnel_integration.py -v`
Expected: PASS. This task only updates the test to match behavior Tasks 1-6 already implemented and already verified in isolation — there's no new production code here, so there's no separate RED step. If it fails, the fixtures/assertions above don't match what Tasks 1-6 actually built; re-check against their exact contracts before assuming the test itself is wrong.

- [ ] **Step 3: Run the full suite**

Run: `venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no failures, no errors, pristine output.

- [ ] **Step 4: Commit**

```bash
git add tests/test_funnel_integration.py
git commit -m "test: extend the funnel seam test for multi-subject matching"
```

---

## Verification checklist

After all tasks, confirm:

- [ ] `venv\Scripts\python.exe -m pytest -v` passes with no failures.
- [ ] `python -c "from ingest.subject_extraction import PROMPT_VERSION; assert PROMPT_VERSION == 2"` succeeds.
- [ ] A fresh `data/no_subject_cache.json` does not exist in the repo (it's a runtime artifact under `data/`, already gitignored like `data/wikidata_persons_cache.json`) — confirm nothing in this plan accidentally commits one.
- [ ] Grep the diff for `"ambiguous"` outside of comments/docstrings referencing the old behavior — it should not appear as a live status value anywhere in `src/ingest/match_events.py` or `src/ingest/subject_extraction.py`.
