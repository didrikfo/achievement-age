# Full-Sentence `event_phrase` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the entire display sentence into `event_phrase` so the reword subagent can place titles next to the name and rephrase freely, and add a version-tracked reprocessing pass over the existing corpus.

**Architecture:** `event_phrase` changes meaning from "the fragment after *…was when* " to "the complete display sentence". `core.matching.full_sentence` becomes a normalizer that prefixes legacy suffix-only rows, so both formats display correctly during the open-ended manual migration window. Two advisory guardrail checks land in `ingest/enrichment.py` and feed the existing review report. A new `events.reword_prompt_version` column, paired with an `enrichment.REWORD_PROMPT_VERSION` constant, makes the reprocessing pass resumable and re-runnable for future prompt revisions.

**Tech Stack:** Python 3.13, pytest, Supabase (PostgREST via `supabase-py`), Streamlit. Rewording is done by Claude Haiku subagents dispatched from a Claude Code session — no API-calling code lives in this repo.

**Spec:** `docs/superpowers/specs/2026-08-08-event-phrase-full-sentence-design.md`

## Global Constraints

- Run all commands from the repo root with the venv interpreter: `./venv/Scripts/python.exe` (Windows/Git Bash). `python` alone is not on PATH.
- Run tests with `./venv/Scripts/python.exe -m pytest`.
- The fixed sentence template is exactly `The same age that {person} was when {event}` — the `was when` hinge is never varied.
- `events.name` always holds the **bare** name, never a title. Titles appear only inside `event_phrase`.
- Both guardrail checks are **advisory**: they append to the review report and never block a write or alter a value.
- Subject corrections are **recorded but not applied** in the phrasing backfill mode. They continue to be applied in `llm_utils` and in the existing `mode="tags"` path.
- `REWORD_PROMPT_VERSION` is `1`. Pre-existing rows stay at the column default `0`.
- Existing behavior of `backfill_event_enrichment` under `mode="tags"` must not change.

---

### Task 1: `full_sentence` becomes a normalizer

**Files:**
- Modify: `src/core/matching.py:23-25`
- Test: `tests/test_matching.py:53-55`

**Interfaces:**
- Produces: `matching.LEGACY_PHRASE_PREFIX: str` (`"The same age that "`), and `matching.full_sentence(event: Dict) -> str` — unchanged signature, new behavior. Every later task assumes a stored full sentence passes through untouched.

- [ ] **Step 1: Replace the existing `full_sentence` test**

In `tests/test_matching.py`, replace `test_full_sentence_combines_name_and_phrase` (lines 53-55) with these three tests:

```python
def test_full_sentence_prefixes_a_legacy_suffix_only_phrase():
    event = {"name": "George Washington", "event_phrase": "he hoisted the flag"}
    assert full_sentence(event) == "The same age that George Washington was when he hoisted the flag"


def test_full_sentence_passes_through_a_stored_full_sentence():
    phrase = "The same age that Sir Richard Owen was when a dinner party was held inside an iguanodon."
    event = {"name": "Richard Owen", "event_phrase": phrase}
    assert full_sentence(event) == phrase


def test_full_sentence_passes_through_regardless_of_case_or_leading_space():
    # A subagent that lowercased the opening, or left the phrase indented, still
    # produced a full sentence - prefixing it again would duplicate the opening.
    event = {"name": "Ada Lovelace", "event_phrase": "  the same age that Ada Lovelace was when she published her notes."}
    assert full_sentence(event) == "  the same age that Ada Lovelace was when she published her notes."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_matching.py -k full_sentence -v`
Expected: FAIL — the two pass-through tests get a doubled opening (`"The same age that Richard Owen was when The same age that Sir Richard Owen was when …"`).

- [ ] **Step 3: Rewrite `full_sentence`**

In `src/core/matching.py`, replace lines 23-25:

```python
LEGACY_PHRASE_PREFIX = "The same age that "


def full_sentence(event: Dict) -> str:
    """Return the event's display sentence, rebuilding the opening for legacy rows.

    event_phrase now stores the complete sentence - the reword subagent writes
    it end to end so it can put a title next to the name ("Sir Richard Owen").
    Rows written before that change store only the fragment after "...was when ",
    so anything that doesn't already open the sentence gets the old static
    prefix rebuilt around it.

    Kept as a normalizer rather than a plain field read because the reprocessing
    backfill is run manually: both formats coexist in the database for as long
    as that takes, and a subagent that ignores the template would otherwise
    render with no opening at all.
    """
    phrase = event["event_phrase"]
    if phrase.lstrip().lower().startswith(LEGACY_PHRASE_PREFIX.lower()):
        return phrase
    return f"{LEGACY_PHRASE_PREFIX}{event['name']} was when {phrase}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_matching.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/core/matching.py tests/test_matching.py && git commit -m "feat: make full_sentence a normalizer for legacy suffix-only phrases"
```

---

### Task 2: Fallback produces a full sentence

**Files:**
- Modify: `src/ingest/llm_utils.py:43-50`
- Test: `tests/test_llm_utils.py:44`, `tests/test_llm_utils.py:66`

**Interfaces:**
- Consumes: nothing from Task 1 (deliberately independent — `_fallback_event_phrase` builds the opening itself rather than importing, so `llm_utils` keeps no dependency on `core.matching`).
- Produces: `llm_utils._fallback_event_phrase(event: Dict) -> str` returning a complete sentence.

- [ ] **Step 1: Update the two fallback assertions**

In `tests/test_llm_utils.py` line 44, change:

```python
    assert by_name["Anton Chekhov"]["event_phrase"] == "wrote a play"
```

to:

```python
    assert by_name["Anton Chekhov"]["event_phrase"] == "The same age that Anton Chekhov was when wrote a play"
```

And line 66, change:

```python
    assert merged[0]["event_phrase"] == "published notes"
```

to:

```python
    assert merged[0]["event_phrase"] == "The same age that Ada Lovelace was when published notes"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_llm_utils.py -v`
Expected: FAIL on both — actual values are still the bare suffixes.

- [ ] **Step 3: Rewrite `_fallback_event_phrase`**

In `src/ingest/llm_utils.py`, replace lines 43-50:

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_llm_utils.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ingest/llm_utils.py tests/test_llm_utils.py && git commit -m "feat: fallback event_phrase produces the full display sentence"
```

---

### Task 3: Advisory guardrail checks

**Files:**
- Modify: `src/ingest/enrichment.py` (add imports + two functions after `validate_tags`, around line 55)
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: `core.matching.normalize_name` (already imported in `enrichment.py` at line 17).
- Produces:
  - `enrichment.PHRASE_OPENING: str` = `"The same age that "`
  - `enrichment.PHRASE_HINGE: str` = `" was when "`
  - `enrichment.check_phrase_format(event_phrase: str, name: str) -> Optional[str]` — returns a rejection reason, or `None` when well-formed.
  - `enrichment.check_facts_preserved(text: str, event_phrase: str) -> List[str]` — returns tokens present in `text` but absent from `event_phrase`, order-preserving and de-duplicated. Empty list means nothing missing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_enrichment.py`:

```python
from ingest.enrichment import check_facts_preserved, check_phrase_format


def test_check_phrase_format_accepts_a_well_formed_sentence():
    phrase = "The same age that Jerry Rawlings was when a coup d'état removed the PNP government."
    assert check_phrase_format(phrase, "Jerry Rawlings") is None


def test_check_phrase_format_accepts_a_title_in_front_of_the_name():
    # The whole point of the rewrite: titles move next to the name, and the bare
    # name in the DB still has to be recognisable inside the opening.
    phrase = "The same age that Flight lieutenant Jerry Rawlings was when a coup d'état removed the PNP government."
    assert check_phrase_format(phrase, "Jerry Rawlings") is None


def test_check_phrase_format_rejects_a_legacy_suffix_only_phrase():
    reason = check_phrase_format("he hoisted the flag.", "George Washington")
    assert reason is not None
    assert "start" in reason


def test_check_phrase_format_rejects_a_missing_hinge():
    reason = check_phrase_format("The same age that George Washington hoisted the flag.", "George Washington")
    assert reason is not None
    assert "was when" in reason


def test_check_phrase_format_rejects_a_substituted_person():
    phrase = "The same age that Benjamin Franklin was when he hoisted the flag."
    reason = check_phrase_format(phrase, "George Washington")
    assert reason is not None
    assert "George Washington" in reason


def test_check_phrase_format_rejects_a_missing_terminal_period():
    phrase = "The same age that George Washington was when he hoisted the flag"
    reason = check_phrase_format(phrase, "George Washington")
    assert reason is not None
    assert "punctuation" in reason


def test_check_facts_preserved_returns_empty_when_everything_survives():
    text = "Fulgencio Batista, dictator of Cuba, is overthrown by Fidel Castro's forces."
    phrase = "The same age that Fidel Castro was when his forces overthrew Fulgencio Batista, dictator of Cuba."
    assert check_facts_preserved(text, phrase) == []


def test_check_facts_preserved_flags_a_dropped_topic_prefix():
    # The single largest source of lost content in the current corpus.
    text = "Cuban Revolution: Fulgencio Batista, dictator of Cuba, is overthrown by Fidel Castro's forces."
    phrase = "The same age that Fidel Castro was when his forces overthrew Fulgencio Batista, dictator of Cuba."
    assert check_facts_preserved(text, phrase) == ["Revolution"]


def test_check_facts_preserved_flags_a_dropped_title():
    text = "A dinner party is held inside a model created by Benjamin Waterhouse Hawkins and Sir Richard Owen."
    phrase = "The same age that Richard Owen was when a dinner party was held inside a model he created with Benjamin Waterhouse Hawkins."
    assert check_facts_preserved(text, phrase) == ["Sir"]


def test_check_facts_preserved_flags_a_dropped_numeral():
    text = "He sold Paradise Lost to a printer for 10 pounds."
    phrase = "The same age that John Milton was when he sold Paradise Lost to a printer."
    assert check_facts_preserved(text, phrase) == ["10"]


def test_check_facts_preserved_ignores_parenthesised_asides():
    # The prompt tells the subagent to strip these, so their disappearance is correct.
    text = "Mary Shelley (b. 1797) publishes Frankenstein."
    phrase = "The same age that Mary Shelley was when she published Frankenstein."
    assert check_facts_preserved(text, phrase) == []


def test_check_facts_preserved_ignores_the_sentence_initial_word():
    # "Soldiers" is capitalised only because it starts the sentence.
    text = "Soldiers of the 6th Pennsylvania Regiment rebelled."
    phrase = "The same age that Anthony Wayne was when soldiers of the 6th Pennsylvania Regiment rebelled."
    assert check_facts_preserved(text, phrase) == []


def test_check_facts_preserved_deduplicates_repeated_tokens():
    # "The" leads the sentence and is exempt; Prussia appears twice but is
    # reported once.
    text = "The army of Prussia fought Austria, and Prussia won."
    phrase = "The same age that Frederick the Great was when he won."
    assert check_facts_preserved(text, phrase) == ["Prussia", "Austria"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_enrichment.py -v`
Expected: FAIL at import — `ImportError: cannot import name 'check_facts_preserved' from 'ingest.enrichment'`.

- [ ] **Step 3: Implement both checks**

The file has no `re` import yet. In `src/ingest/enrichment.py`, replace lines 12-13:

```python
from pathlib import Path
from typing import Dict, List, Optional, Tuple
```

with:

```python
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
```

Then insert after `validate_tags` (after line 54, before `load_births_lookup`):

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


def check_facts_preserved(text: str, event_phrase: str) -> List[str]:
    """Tokens present in `text` but missing from `event_phrase`. Advisory heuristic.

    Deliberately over-sensitive - it flags roughly one record in six, and its
    output is a triage queue rather than a defect count. It exists because the
    two commonest ways a reword loses content are dropping a Wikipedia topic
    prefix ("Cuban Revolution:") and dropping a title ("Sir", "General"), both
    of which show up cleanly as a proper noun that vanished.

    Unlike an earlier sketch, the subject's own name is NOT exempt: under the
    full-sentence format the name and any title belong inside the phrase, so
    including them is what catches the dropped-title case.
    """
    source = _PARENTHESISED.sub(" ", text or "")
    phrase = event_phrase or ""
    phrase_lower = phrase.lower()

    words = source.split()
    first_word = words[0].strip(",.:;") if words else ""

    missing: List[str] = []
    seen = set()
    for token in _PROPER_NOUN.findall(source):
        if token == first_word or token in seen:
            continue
        if token.lower() not in phrase_lower:
            missing.append(token)
            seen.add(token)
    for token in _NUMERAL.findall(source):
        if token in seen:
            continue
        if token not in phrase:
            missing.append(token)
            seen.add(token)
    return missing
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_enrichment.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ingest/enrichment.py tests/test_enrichment.py && git commit -m "feat: add advisory phrase-format and fact-preservation checks"
```

---

### Task 4: New reword prompt and `REWORD_PROMPT_VERSION`

**Files:**
- Modify: `src/ingest/reword_prompt.md:17-26` (the `event_phrase` bullet)
- Modify: `src/ingest/enrichment.py` (add the constant near `TAG_TAXONOMY`)
- Test: `tests/test_enrichment.py`

**Interfaces:**
- Produces: `enrichment.REWORD_PROMPT_VERSION: int` = `1`. Tasks 5, 6 and 7 all import it.
- `enrichment.build_prompt()` keeps its existing signature and `{tags}` substitution — only the template text changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_enrichment.py`:

```python
def test_reword_prompt_version_is_one():
    from ingest.enrichment import REWORD_PROMPT_VERSION

    assert REWORD_PROMPT_VERSION == 1


def test_build_prompt_asks_for_a_full_sentence_with_titles():
    prompt = build_prompt()
    # The template, the freedom to restructure, title placement, and the
    # topic-prefix rule are the four things this rewrite exists to convey.
    assert "The same age that" in prompt
    assert "was when" in prompt
    assert "title, rank, honorific" in prompt
    assert "Topic:" in prompt
    assert "Preserving the source's sentence structure is not a goal" in prompt
    # And the old suffix-only instruction must be gone.
    assert "Don't capitalize the first word" not in prompt


def test_build_prompt_still_substitutes_the_tag_taxonomy():
    prompt = build_prompt()
    assert "{tags}" not in prompt
    for tag in TAG_TAXONOMY:
        assert tag in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_enrichment.py -k "prompt" -v`
Expected: FAIL — `ImportError` for `REWORD_PROMPT_VERSION`, and the prompt-content assertions fail against the current template.

- [ ] **Step 3: Add the version constant**

In `src/ingest/enrichment.py`, immediately after the `TAG_TAXONOMY` list (after line 26), add:

```python
#: Bumped by hand whenever reword_prompt.md changes in a way that could change
#: results. Rows in Supabase carry the version they were written under
#: (events.reword_prompt_version, default 0 for everything predating this), so a
#: prompt revision makes the affected rows re-queueable instead of one-shot.
REWORD_PROMPT_VERSION = 1
```

- [ ] **Step 4: Rewrite the `event_phrase` section of the prompt**

In `src/ingest/reword_prompt.md`, replace lines 17-26 (the whole `- event_phrase:` bullet and its sub-bullets) with:

```markdown
- `event_phrase`: the **complete display sentence** shown to the app user. It always follows this
  template, with the `was when` hinge never varied:

  > The same age that **{person}** was when **{event}**.

  **Naming the person**
  - Default to `name` exactly as given.
  - If `text` attaches a title, rank, honorific, or established epithet to that person, include it
    here — "Sir Richard Owen", "Flight lieutenant Jerry Rawlings". Never invent one that isn't in
    `text`, and never substitute a different person.
  - After the opening, use a pronoun rather than repeating the name. If `text` doesn't establish the
    person's pronouns, use a short form of their name instead — don't guess.

  **Rewording**
  - Reword for how it *reads*, not to stay close to the original wording. Restructure clauses,
    reorder them, and split into more than one sentence whenever the original syntax is convoluted.
    Preserving the source's sentence structure is not a goal; preserving its meaning is.
  - If `text` doesn't already have `name` as its grammatical subject, restructure so they are — "he
    led the Prussian forces at the Battle of Kolín" rather than a clause-by-clause translation that
    leaves them stranded in a prepositional phrase.
  - Past tense throughout, matching "was when".
  - End with a period.
  - Keep it to roughly one to three sentences. It renders as a bullet in a calendar dialog and as the
    body of a push notification.

  **Fidelity**
  - Preserve every concrete fact: other people's names, places, organizations, numbers, and outcomes.
    Reword the structure, not the substance — don't add or invent detail that isn't in `text`.
  - If `text` opens with a framing prefix before a colon (`World War II:`, `Cuban Revolution:`), weave
    that context into the sentence rather than dropping it.
  - Strip Wikipedia-style artifacts from `text` (trailing citation brackets, "(b. ...)"/"(d. ...)"
    asides) that would read strangely in a finished sentence.

  Worked example — for `name` "Richard Owen" and `text` "A dinner party is held inside a life-size
  model of an iguanodon created by Benjamin Waterhouse Hawkins and Sir Richard Owen in south London,
  England.":

  > The same age that Sir Richard Owen was when a dinner party was held inside a life-size model of an
  > iguanodon, which he had created with Benjamin Waterhouse Hawkins, in south London, England.

  Note the title moved up next to the name, and the model's creation was recast so he is the one doing
  it. `name` in your output stays the bare "Richard Owen" — titles belong only in `event_phrase`.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_enrichment.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ingest/reword_prompt.md src/ingest/enrichment.py tests/test_enrichment.py && git commit -m "feat: rewrite reword prompt for full-sentence output, add REWORD_PROMPT_VERSION"
```

---

### Task 5: Wire the checks and version stamp into `llm_utils`

**Files:**
- Modify: `src/ingest/llm_utils.py:19` (import), `src/ingest/llm_utils.py:146-172` (the merge loop)
- Test: `tests/test_llm_utils.py:137-158`

**Interfaces:**
- Consumes: `enrichment.check_phrase_format`, `enrichment.check_facts_preserved` (Task 3), `enrichment.REWORD_PROMPT_VERSION` (Task 4).
- Produces: merged records carry `reword_prompt_version` when they came from a subagent. Review entries gain two new `issue_type` values, `"format"` and `"facts"`.

- [ ] **Step 1: Update the review-entry test and add two new ones**

In `tests/test_llm_utils.py`, `test_merge_reworded_chunk_writes_review_entry_for_invalid_tags` currently asserts the review file's exact contents while feeding a legacy-format phrase — which now also trips the format check. Change its `result` (line 142) to a well-formed sentence so the test keeps isolating the tags failure:

```python
    result = [
        {
            **chunk[0],
            "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
            "tags": ["not-a-real-tag"],
        }
    ]
```

Then append these two tests to the file:

```python
def test_merge_reworded_chunk_flags_a_malformed_phrase(tmp_path):
    chunk = [{"name": "Ada Lovelace", "text": "published notes", "year": "1843", "month": 1, "day": 1, "age": 50}]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # A subagent that produced the old suffix-only shape instead of a full sentence.
    result = [{**chunk[0], "event_phrase": "she published her notes", "tags": ["science"]}]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    displayable_path = tmp_path / "displayable_events.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=review_path,
    )

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["format"]
    # Advisory only - the phrase is still written through unchanged.
    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    assert merged[0]["event_phrase"] == "she published her notes"


def test_merge_reworded_chunk_stamps_the_prompt_version_only_on_subagent_output(tmp_path):
    from ingest.enrichment import REWORD_PROMPT_VERSION

    chunk = [
        {"name": "Ada Lovelace", "text": "Ada Lovelace published notes.", "year": "1843", "month": 1, "day": 1, "age": 50},
        {"name": "Anton Chekhov", "text": "wrote a play", "year": "1904", "month": 1, "day": 17, "age": 200},
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    # Only the first comes back; the second falls back and must stay re-queueable.
    result = [
        {
            **chunk[0],
            "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
            "tags": ["science"],
        }
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    displayable_path = tmp_path / "displayable_events.json"
    merge_reworded_chunk(
        chunk_path,
        result_path,
        displayable_path=displayable_path,
        births_path=_empty_births_path(tmp_path),
        review_path=tmp_path / "review.json",
    )

    merged = json.loads(displayable_path.read_text(encoding="utf-8"))
    by_name = {event["name"]: event for event in merged}
    assert by_name["Ada Lovelace"]["reword_prompt_version"] == REWORD_PROMPT_VERSION
    assert "reword_prompt_version" not in by_name["Anton Chekhov"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_llm_utils.py -v`
Expected: FAIL — no `"format"` review entry is produced, and `reword_prompt_version` is never written.

- [ ] **Step 3: Extend the import**

In `src/ingest/llm_utils.py`, replace line 19:

```python
from ingest.enrichment import load_births_lookup, resolve_subject, validate_tags, write_review_entries
```

with:

```python
from ingest.enrichment import (
    REWORD_PROMPT_VERSION,
    check_facts_preserved,
    check_phrase_format,
    load_births_lookup,
    resolve_subject,
    validate_tags,
    write_review_entries,
)
```

- [ ] **Step 4: Add the checks to the merge loop**

In `src/ingest/llm_utils.py`, replace the body of the `for event in chunk:` loop (lines 146-172) with:

```python
    for event in chunk:
        result = reworded_by_key.get(_event_key(event))
        if not result or not result.get("event_phrase"):
            # Deliberately unstamped: a fallback record isn't subagent output, so
            # the phrasing backfill should re-queue it later.
            merged.append({**event, "event_phrase": _fallback_event_phrase(event), "tags": []})
            continue

        merged_event = {
            **event,
            "event_phrase": result["event_phrase"],
            "reword_prompt_version": REWORD_PROMPT_VERSION,
        }

        tags, tag_reason = validate_tags(result.get("tags") or [])
        merged_event["tags"] = tags
        if tag_reason:
            review_entries.append(
                {"name": event.get("name"), "text": event.get("text"), "issue_type": "tags", "detail": tag_reason}
            )

        correction, subject_reason = resolve_subject(event, result.get("suggested_subject"), births_lookup)
        if correction:
            if correction["name"] != event.get("name"):
                merged_event[ORIGINAL_NAME_FIELD] = event.get("name")
            merged_event["name"] = correction["name"]
            merged_event["age"] = correction["age_days"]
        if subject_reason:
            review_entries.append(
                {"name": event.get("name"), "text": event.get("text"), "issue_type": "subject", "detail": subject_reason}
            )

        # Format-checked against the post-correction name: when a subject
        # correction is applied, the phrase names the corrected person, so
        # checking the pre-correction name would report a false mismatch.
        format_reason = check_phrase_format(merged_event["event_phrase"], merged_event.get("name") or "")
        if format_reason:
            review_entries.append(
                {"name": merged_event.get("name"), "text": event.get("text"), "issue_type": "format", "detail": format_reason}
            )

        missing = check_facts_preserved(event.get("text", ""), merged_event["event_phrase"])
        if missing:
            review_entries.append(
                {
                    "name": merged_event.get("name"),
                    "text": event.get("text"),
                    "issue_type": "facts",
                    "detail": f"missing from phrase: {', '.join(missing)}",
                }
            )

        merged.append(merged_event)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_llm_utils.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ingest/llm_utils.py tests/test_llm_utils.py && git commit -m "feat: run guardrail checks and stamp prompt version in the reword merge"
```

---

### Task 6: Schema column, version passthrough, and migration preflight

**Files:**
- Modify: `src/ingest/migrate_to_supabase.py:57-69` (`_to_event_row`), `src/ingest/migrate_to_supabase.py:72-107` (`main`)
- Modify: `SUPABASE_SETUP.md` (new section 9, after the section 8 block that ends the file's runbook content)
- Test: `tests/test_migrate_to_supabase.py`

**Interfaces:**
- Consumes: `enrichment.REWORD_PROMPT_VERSION` (Task 4) — imported only for documentation symmetry; `_to_event_row` reads the value off the entry and defaults to `0`.
- Produces: `migrate_to_supabase.report_unmatched_legacy_entries(entries: List[Dict], existing_keys: Set[Tuple[str, str]]) -> List[Dict]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_migrate_to_supabase.py`:

```python
from ingest.migrate_to_supabase import report_unmatched_legacy_entries


def test_to_event_row_defaults_the_prompt_version_to_zero():
    entry = {
        "name": "Ada Lovelace",
        "text": "published notes",
        "event_phrase": "she published notes",
        "year": "1843",
        "month": 1,
        "day": 1,
        "age": 50,
    }
    assert _to_event_row(entry, {})["reword_prompt_version"] == 0


def test_to_event_row_carries_a_stamped_prompt_version_through():
    entry = {
        "name": "Ada Lovelace",
        "text": "published notes",
        "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
        "reword_prompt_version": 1,
        "year": "1843",
        "month": 1,
        "day": 1,
        "age": 50,
    }
    assert _to_event_row(entry, {})["reword_prompt_version"] == 1


def test_report_unmatched_legacy_entries_is_empty_when_every_legacy_row_matches():
    entries = [
        {"name": "Ada Lovelace", "text": "published notes", "display_text": "The same age that..."},
        {"name": "New Person", "text": "did a thing", "event_phrase": "they did a thing"},
    ]
    existing = {("Ada Lovelace", "published notes")}
    assert report_unmatched_legacy_entries(entries, existing) == []


def test_report_unmatched_legacy_entries_flags_a_legacy_row_missing_from_supabase():
    # Happens if a Supabase-side subject correction renamed the row: the local
    # copy no longer key-matches, so migrating would insert a duplicate.
    entries = [{"name": "Mary Wollstonecraft", "text": "published Frankenstein", "display_text": "The same age that..."}]
    assert report_unmatched_legacy_entries(entries, set()) == entries


def test_report_unmatched_legacy_entries_ignores_new_style_records():
    # Records with event_phrase have never been migrated - being absent is expected.
    entries = [{"name": "New Person", "text": "did a thing", "event_phrase": "they did a thing"}]
    assert report_unmatched_legacy_entries(entries, set()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_migrate_to_supabase.py -v`
Expected: FAIL at import — `ImportError: cannot import name 'report_unmatched_legacy_entries'`.

- [ ] **Step 3: Add the version passthrough and the preflight function**

In `src/ingest/migrate_to_supabase.py`, add to the returned dict in `_to_event_row` (after the `"event_phrase"` line, line 62):

```python
        "reword_prompt_version": entry.get("reword_prompt_version", 0),
```

Then add this function directly after `filter_new_entries` (after line 54):

```python
def report_unmatched_legacy_entries(
    entries: List[Dict], existing_keys: Set[Tuple[str, str]]
) -> List[Dict]:
    """Legacy display_text records that aren't already in Supabase.

    Every record still carrying display_text (rather than event_phrase) was
    migrated long ago, so all of them should key-match an existing row. Any that
    don't mean the Supabase-side name has since changed - an applied subject
    correction is the likely cause - and migrating would insert a duplicate
    rather than skip it. Expected result: empty.
    """
    return [
        entry
        for entry in entries
        if entry.get("display_text")
        and not entry.get("event_phrase")
        and (entry["name"], entry["text"]) not in existing_keys
    ]
```

- [ ] **Step 4: Abort the migration when the preflight finds anything**

In `src/ingest/migrate_to_supabase.py`, in `main()`, replace lines 77-82:

```python
    already_migrated = fetch_existing_event_keys(client)
    entries = filter_new_entries(entries, already_migrated)
    if not entries:
        print("Nothing new to migrate.")
        return
    print(f"{len(entries)} new event(s) to migrate.")
```

with:

```python
    already_migrated = fetch_existing_event_keys(client)

    unmatched = report_unmatched_legacy_entries(entries, already_migrated)
    if unmatched:
        print(f"ABORTED: {len(unmatched)} already-migrated record(s) no longer match a Supabase row.")
        print("Migrating now would insert duplicates. Reconcile these by hand first:")
        for entry in unmatched[:10]:
            print(f"  - {entry['name']!r}: {entry['text'][:80]}")
        return

    entries = filter_new_entries(entries, already_migrated)
    if not entries:
        print("Nothing new to migrate.")
        return
    print(f"{len(entries)} new event(s) to migrate.")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_migrate_to_supabase.py -v`
Expected: PASS.

- [ ] **Step 6: Document the schema change**

Append a new section to the end of `SUPABASE_SETUP.md`. (The block below is fenced with four backticks so its inner ```sql/```bash fences survive — write only the inner content to the file.)

````markdown
## 9. Full-sentence event phrases

`event_phrase` now stores the **complete** display sentence ("The same age that Sir Richard Owen was
when …"), not just the fragment after "…was when ". The reword subagent writes the whole sentence so
it can place a title next to the name; `events.name` still holds the bare name.

`core.matching.full_sentence` prefixes anything that doesn't already open with "The same age that",
so rows in the older suffix-only format keep displaying correctly until the backfill below has run.

Run this in the SQL editor first — it tracks which rows have been written under which version of
`src/ingest/reword_prompt.md`, so the backfill is resumable and future prompt revisions are
re-runnable:

```sql
alter table events add column if not exists reword_prompt_version integer not null default 0;
```

Then migrate any events still sitting in `data/displayable_events.json`:

```bash
./venv/Scripts/python.exe -m ingest.migrate_to_supabase
```

This prints a preflight check before inserting anything. It must report no unmatched records — if it
aborts, a previously-migrated event's name has changed in Supabase and needs reconciling by hand,
because migrating would insert duplicates rather than skip them.
````

- [ ] **Step 7: Commit**

```bash
git add src/ingest/migrate_to_supabase.py tests/test_migrate_to_supabase.py SUPABASE_SETUP.md && git commit -m "feat: carry reword_prompt_version through migration, add duplicate preflight"
```

---

### Task 7: Phrasing-only backfill mode

**Files:**
- Modify: `src/ingest/backfill_event_enrichment.py` (imports, `_fetch_all_events`, `prepare_chunks`, `resolve_event_update`, `merge_chunk`)
- Modify: `SUPABASE_SETUP.md` (extend section 9 from Task 6)
- Test: `tests/test_backfill_event_enrichment.py`

**Interfaces:**
- Consumes: `enrichment.REWORD_PROMPT_VERSION` (Task 4), `enrichment.check_phrase_format`, `enrichment.check_facts_preserved` (Task 3).
- Produces:
  - `backfill_event_enrichment.pending_phrasing_events(all_events: List[Dict], version: int) -> List[Dict]`
  - `prepare_chunks(chunk_size: int = CHUNK_SIZE, mode: str = "tags") -> List[Path]`
  - `resolve_event_update(event, result, births_lookup, mode: str = "tags") -> Tuple[Optional[Dict], List[str], List[Dict]]`
  - `merge_chunk(chunk_path, result_path, review_path=REVIEW_PATH, mode: str = "tags") -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backfill_event_enrichment.py`:

```python
from ingest.backfill_event_enrichment import pending_phrasing_events
from ingest.enrichment import REWORD_PROMPT_VERSION


def test_pending_phrasing_events_selects_rows_below_the_current_version():
    events = [
        {"id": 1, "reword_prompt_version": 0},
        {"id": 2, "reword_prompt_version": REWORD_PROMPT_VERSION},
        {"id": 3},  # column default never read back - treat a missing value as 0
    ]
    result = pending_phrasing_events(events, REWORD_PROMPT_VERSION)
    assert [event["id"] for event in result] == [1, 3]


def test_resolve_event_update_phrasing_mode_writes_only_phrase_and_version():
    event = {"id": 4, "name": "Ada Lovelace", "text": "Ada Lovelace published notes.", "year": 1843, "month": 1, "day": 1}
    result = {
        "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
        "tags": ["science"],
        "suggested_subject": None,
    }

    update, tags, review_entries = resolve_event_update(event, result, births_lookup={}, mode="phrasing")

    assert update == {
        "event_phrase": "The same age that Ada Lovelace was when she published her notes.",
        "reword_prompt_version": REWORD_PROMPT_VERSION,
    }
    # Tags are already assigned for these rows; the phrasing pass must not touch them.
    assert tags == []
    assert review_entries == []


def test_resolve_event_update_phrasing_mode_records_but_does_not_apply_a_subject_correction():
    event = {
        "id": 5,
        "name": "George Washington",
        "text": "George Washington and John Adams hoisted the flag",
        "year": 1776,
        "month": 1,
        "day": 1,
    }
    result = {
        # Names both people, so the fact check stays quiet and this test isolates
        # the subject behaviour.
        "event_phrase": "The same age that George Washington was when he and John Adams hoisted the flag.",
        "suggested_subject": "John Adams",
    }
    births_lookup = {"john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30}}

    update, _tags, review_entries = resolve_event_update(event, result, births_lookup, mode="phrasing")

    assert "name" not in update
    assert "age_days" not in update
    assert [entry["issue_type"] for entry in review_entries] == ["subject"]
    assert "John Adams" in review_entries[0]["detail"]


def test_resolve_event_update_phrasing_mode_flags_a_malformed_phrase():
    event = {"id": 6, "name": "Ada Lovelace", "text": "published notes", "year": 1843, "month": 1, "day": 1}
    result = {"event_phrase": "she published her notes"}

    update, _tags, review_entries = resolve_event_update(event, result, births_lookup={}, mode="phrasing")

    assert update["event_phrase"] == "she published her notes"
    assert [entry["issue_type"] for entry in review_entries] == ["format"]


def test_merge_chunk_phrasing_mode_skips_tags_and_persons(tmp_path):
    chunk = [
        {
            "id": 20,
            "name": "George Washington",
            "text": "George Washington and John Adams hoisted the flag",
            "year": 1776,
            "month": 1,
            "day": 1,
        }
    ]
    chunk_path = tmp_path / "chunk_0000.json"
    chunk_path.write_text(json.dumps(chunk), encoding="utf-8")

    result = [
        {
            "id": 20,
            # Names both people, so the fact check stays quiet and the review
            # assertion below isolates the subject entry.
            "event_phrase": "The same age that George Washington was when he and John Adams hoisted the flag.",
            "tags": ["military"],
            "suggested_subject": "John Adams",
        }
    ]
    result_path = tmp_path / "chunk_0000_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    review_path = tmp_path / "review.json"
    births_lookup = {"john adams": {"name": "John Adams", "year": 1735, "month": 10, "day": 30}}
    mock_client = _make_mock_client(tags=[{"id": 2, "name": "military"}])

    with patch("ingest.backfill_event_enrichment.get_client", return_value=mock_client), patch(
        "ingest.backfill_event_enrichment.load_births_lookup", return_value=births_lookup
    ):
        merge_chunk(chunk_path, result_path, review_path=review_path, mode="phrasing")

    mock_client._table_mocks["events"].update.assert_called_once_with(
        {
            "event_phrase": "The same age that George Washington was when he and John Adams hoisted the flag.",
            "reword_prompt_version": REWORD_PROMPT_VERSION,
        }
    )
    mock_client._table_mocks["event_tags"].insert.assert_not_called()
    mock_client._table_mocks["persons"].upsert.assert_not_called()

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert [entry["issue_type"] for entry in review] == ["subject"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_backfill_event_enrichment.py -v`
Expected: FAIL at import — `ImportError: cannot import name 'pending_phrasing_events'`.

- [ ] **Step 3: Extend the imports and the event fetch**

In `src/ingest/backfill_event_enrichment.py`, replace line 22:

```python
from ingest.enrichment import build_tag_rows, load_births_lookup, resolve_subject, validate_tags, write_review_entries
```

with:

```python
from ingest.enrichment import (
    REWORD_PROMPT_VERSION,
    build_tag_rows,
    check_facts_preserved,
    check_phrase_format,
    load_births_lookup,
    resolve_subject,
    validate_tags,
    write_review_entries,
)
```

Then in `_fetch_all_events`, change the select on line 38 to include the new column:

```python
            .select("id, name, text, year, month, day, reword_prompt_version")
```

- [ ] **Step 4: Add the phrasing-mode pending selector**

In `src/ingest/backfill_event_enrichment.py`, add directly after `pending_events` (after line 76):

```python
def pending_phrasing_events(all_events: List[Dict], version: int) -> List[Dict]:
    """Events not yet written under the current reword prompt.

    A missing key counts as 0 (the column default), so rows predating the
    column are always pending.
    """
    return [event for event in all_events if (event.get("reword_prompt_version") or 0) < version]
```

- [ ] **Step 5: Add the mode parameter to `prepare_chunks`**

In `src/ingest/backfill_event_enrichment.py`, replace `prepare_chunks` (lines 79-91):

```python
def prepare_chunks(chunk_size: int = CHUNK_SIZE, mode: str = "tags") -> List[Path]:
    """Fetch pending events from Supabase and split them into numbered chunk files.

    mode="tags" (default) selects events with no event_tags rows yet - the
    original enrichment backfill. mode="phrasing" selects events not yet written
    under the current reword prompt, for a re-phrasing pass over rows that
    already have tags.
    """
    if mode not in ("tags", "phrasing"):
        raise ValueError(f"unknown mode {mode!r}, expected 'tags' or 'phrasing'")

    client = get_client()
    all_events = _fetch_all_events(client)
    if mode == "phrasing":
        pending = pending_phrasing_events(all_events, REWORD_PROMPT_VERSION)
    else:
        pending = pending_events(all_events, _fetch_tagged_event_ids(client))

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        chunk = pending[start : start + chunk_size]
        path = CHUNK_DIR / f"chunk_{index:04d}.json"
        save_to_json(path, chunk)
        paths.append(path)
    return paths
```

- [ ] **Step 6: Add the phrasing branch to `resolve_event_update`**

In `src/ingest/backfill_event_enrichment.py`, replace `resolve_event_update` (lines 94-125):

```python
def resolve_event_update(
    event: Dict,
    result: Optional[Dict],
    births_lookup: Dict[str, Dict],
    mode: str = "tags",
) -> Tuple[Optional[Dict], List[str], List[Dict]]:
    """Pure decision logic for one event: what to write, and what to flag for review.

    Returns (events_update_or_none, valid_tags, review_entries). events_update_or_none
    is None when no usable event_phrase came back (nothing to write for this event).

    mode="phrasing" writes only event_phrase and reword_prompt_version. It assigns
    no tags (these rows already have them) and does not apply subject corrections -
    doing so would pull age recomputation and persons upserts into what should be a
    single-purpose, easily-reversible pass. Suggested subjects are still recorded
    for review so the errors surface for a separate decision.
    """
    review_entries: List[Dict] = []

    if not result or not result.get("event_phrase"):
        review_entries.append(
            {"event_id": event["id"], "issue_type": "reword", "detail": "no usable event_phrase returned"}
        )
        return None, [], review_entries

    event_phrase = result["event_phrase"]

    if mode == "phrasing":
        update: Dict = {"event_phrase": event_phrase, "reword_prompt_version": REWORD_PROMPT_VERSION}
        suggested = result.get("suggested_subject")
        if suggested:
            review_entries.append(
                {
                    "event_id": event["id"],
                    "issue_type": "subject",
                    "detail": f"suggested subject {suggested!r} recorded but not applied (phrasing pass)",
                }
            )
        review_entries.extend(_phrase_review_entries(event, event_phrase, event.get("name") or ""))
        return update, [], review_entries

    tags, tag_reason = validate_tags(result.get("tags") or [])
    if tag_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "tags", "detail": tag_reason})

    correction, subject_reason = resolve_subject(event, result.get("suggested_subject"), births_lookup)
    if subject_reason:
        review_entries.append({"event_id": event["id"], "issue_type": "subject", "detail": subject_reason})

    update = {"event_phrase": event_phrase}
    if correction:
        update["name"] = correction["name"]
        update["age_days"] = correction["age_days"]

    review_entries.extend(
        _phrase_review_entries(event, event_phrase, (correction or {}).get("name") or event.get("name") or "")
    )

    return update, tags, review_entries
```

And add this helper directly above `resolve_event_update`:

```python
def _phrase_review_entries(event: Dict, event_phrase: str, name: str) -> List[Dict]:
    """Advisory format/fact review entries for one phrase. Never blocks the write.

    `name` is the post-correction name where a correction was applied, so the
    format check doesn't report a false mismatch against the old subject.
    """
    entries: List[Dict] = []

    format_reason = check_phrase_format(event_phrase, name)
    if format_reason:
        entries.append({"event_id": event["id"], "issue_type": "format", "detail": format_reason})

    missing = check_facts_preserved(event.get("text", ""), event_phrase)
    if missing:
        entries.append(
            {
                "event_id": event["id"],
                "issue_type": "facts",
                "detail": f"missing from phrase: {', '.join(missing)}",
            }
        )
    return entries
```

- [ ] **Step 7: Thread the mode through `merge_chunk`**

In `src/ingest/backfill_event_enrichment.py`, change the `merge_chunk` signature (line 144) to:

```python
def merge_chunk(chunk_path, result_path, review_path: Path = REVIEW_PATH, mode: str = "tags") -> int:
```

Change the `resolve_event_update` call inside the loop (line 165) to pass the mode:

```python
            update, tags, review_entries = resolve_event_update(event, result, births_lookup, mode=mode)
```

Everything else in the loop already behaves correctly under `mode="phrasing"`: the persons upsert is
guarded by `if "name" in update` (never set in phrasing mode), and `build_tag_rows` returns `[]` for
the empty tag list, so the `event_tags` insert is skipped by the existing `if tag_rows:` guard.

- [ ] **Step 8: Run the full test suite**

Run: `./venv/Scripts/python.exe -m pytest -v`
Expected: PASS — all tests, including every pre-existing `mode="tags"` test in `tests/test_backfill_event_enrichment.py`, which must be unaffected.

- [ ] **Step 9: Document the backfill run sequence**

Append to the section 9 block added to `SUPABASE_SETUP.md` in Task 6. (Four-backtick fence again — write only the inner content to the file.)

````markdown
Finally, re-phrase every event that predates the current prompt:

```bash
./venv/Scripts/python.exe -c "from ingest.backfill_event_enrichment import prepare_chunks; print(len(prepare_chunks(mode='phrasing')))"
```

Dispatch a Claude Haiku subagent per chunk file under `data/tmp/enrichment_chunks/`, using
`ingest.enrichment.build_prompt()` for instructions, and save each response next to its chunk as
`<chunk>_result.json`. Then merge each one:

```bash
./venv/Scripts/python.exe -c "from ingest.backfill_event_enrichment import merge_chunk; merge_chunk('data/tmp/enrichment_chunks/chunk_0000.json', 'data/tmp/enrichment_chunks/chunk_0000_result.json', mode='phrasing')"
```

This pass writes **only** `event_phrase` and `reword_prompt_version`. It does not touch tags, and it
records suggested subject corrections in `data/tmp/enrichment_review.json` without applying them —
subject errors are a separate piece of work. It's resumable: `prepare_chunks(mode='phrasing')` only
picks up rows still below the current `REWORD_PROMPT_VERSION`.

Afterwards, read `data/tmp/enrichment_review.json`. Expect a large number of `facts` entries — the
fact check is deliberately over-sensitive and flagged roughly one record in six when prototyped, so
it's a triage queue rather than a defect count. `format` entries are rarer and mean the subagent
ignored the sentence template.
````

- [ ] **Step 10: Commit**

```bash
git add src/ingest/backfill_event_enrichment.py tests/test_backfill_event_enrichment.py SUPABASE_SETUP.md && git commit -m "feat: add phrasing-only backfill mode with version-tracked resumption"
```

---

## Final verification

- [ ] **Full suite passes**

Run: `./venv/Scripts/python.exe -m pytest -v`
Expected: PASS, no skips beyond any that already existed on `dev`.

- [ ] **Prompt quality check before committing to a full run**

Per the spec's testing section, verify the prompt itself before reprocessing 3341 rows. Run
`prepare_chunks(mode='phrasing')`, dispatch **one** chunk to a subagent, and read its output against
the three cases from the spec's context table (Frederick the Great / Kolín, Jerry Rawlings / PNDC,
Richard Owen / iguanodon). Confirm titles moved next to the name, the person is the grammatical
subject, and no facts were dropped. Only proceed to the remaining chunks once that reads well.

- [ ] **Live display check**

With the Streamlit app running against the real project, open a calendar day with a match and confirm
the dialog shows exactly one "The same age that…" opening — no doubling on reprocessed rows, and no
missing opening on rows still awaiting the backfill.
