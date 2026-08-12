# Mathematical Anniversary Days Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark days when your age in days is itself an interesting number — a power of two, a Fibonacci number, a prime — with a triangle on the calendar and an optional push notification, opt-in per sequence.

**Architecture:** Every anniversary is computed live from `age_days` by pure predicates in a new `core/sequences.py`; nothing is stored and no table changes. Anniversary matches travel in their own list, never merged into the event match list, because the two record shapes have nothing in common. The per-subscriber preference is a new `subscriptions.included_sequences` column stored as **inclusions** (unlike the event filters' exclusions), so `default '{}'` makes the whole feature off for every existing subscriber with no data migration.

**Tech Stack:** Python 3.11+, Streamlit 1.45.1, Supabase (PostgREST via `supabase-py`), pytest.

## Global Constraints

- Run all commands from the repo root with the project venv: `./venv/Scripts/python.exe`.
- Test command: `./venv/Scripts/python.exe -m pytest -q`. Baseline before this plan: **237 passed**.
- `pyproject.toml` sets `pythonpath = ["src"]`, so tests import `core.*`, `app.*`, `ingest.*` directly.
- Sequence display names are exactly, in this order: `Powers of 2`, `Powers of 10`, `Triangle numbers`, `Fibonacci numbers`, `Primes`, `Perfect squares`, `Cubes`, `Catalan numbers`. These strings are **persisted verbatim** into `subscriptions.included_sequences` and matched by exact string — renaming one silently orphans every subscriber who selected it, exactly like the `TAG_CATEGORIES` hazard documented at `src/core/config.py:27-31`.
- The default-on four are the **first four** in that order. Primes must never be among them.
- Every sequence is the enumeration of its **positive terms**, so day 0 and negative days match nothing and day 1 matches seven of eight. There is no special-casing of degenerate days anywhere.
- The preference column stores **inclusions**, not exclusions. Empty means the feature is off. Read it as `subscription.get("included_sequences") or []` so a row from before the migration degrades to "off", which is also the desired default.
- Anniversaries are **not** filtered by `filter_events` — the event category/tag filter and the sequence filter are independent.
- The event category/tag machinery (`TAG_TAXONOMY`, `TAG_CATEGORIES`, `filter_events`, `primary_category`, `excluded_tags`, `excluded_categories`) is **not modified by this plan**. Its partition test must keep passing untouched.
- **Streamlit expanders may not be nested** (`StreamlitAPIException`). A `st.popover` inside an expander is allowed.
- **Streamlit garbage-collects the state of widgets it did not render on a run.** The sequence multiselect is hidden behind a checkbox, so it must NOT take a `key=` argument — it is held in a plain session variable and passed as `default=`.
- Do **not** launch the app with the Browser pane's `preview_start {name: ...}` mode if working in a git worktree — it always starts from the main checkout. Launch Streamlit manually instead: `./venv/Scripts/streamlit.exe run src/app/ui.py --server.port 8517` (background), then `preview_start {url: "http://localhost:8517"}`.
- Commit messages use conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `refactor:`) and end with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- Spec: `docs/superpowers/specs/2026-08-12-mathematical-anniversaries-design.md`.

## File Structure

| File | Responsibility |
|---|---|
| `src/core/config.py` (modify) | Adds `SEQUENCE_TAXONOMY` and `DEFAULT_SEQUENCES` alongside the existing persisted taxonomies. Names only — no logic. |
| `src/core/sequences.py` (create) | The whole sequence domain: one membership-and-description function per sequence, the public `sequences_for` / `anniversary_matches` / `anniversary_sentence`, and the subscription reader. Pure; no Supabase, no Streamlit. |
| `src/core/matching.py` | **No change.** Event matching and sequence membership share no vocabulary. |
| `src/core/db.py` (modify) | `create_subscription` / `update_subscription_filters` carry `included_sequences`. |
| `scripts/send_daily_notifications.py` (modify) | A second per-subscriber step and a sibling `_send_anniversary_notification`. `MATCHERS` is unchanged. |
| `src/app/ui.py` (modify) | Sequence checkbox + multiselect in the filter expander, per-day anniversary lookup, marker container keys, two-section dialog. |
| `src/app/styles.py` (modify) | Triangle marker CSS; narrows the existing circle rule so it stops applying to every match button. |
| `SUPABASE_SETUP.md` (modify) | New section 13 (`included_sequences` SQL). |
| `README.md`, `IDEAS.md` (modify) | Feature description; backlog entry rewritten to what shipped. |

---

### Task 1: Sequence taxonomy, predicates, and copy

The heart of the feature. Everything else is wiring.

**Files:**
- Modify: `src/core/config.py:15-47`
- Create: `src/core/sequences.py`
- Test: `tests/test_sequences.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `core.config.SEQUENCE_TAXONOMY: List[str]` — the 8 display names, in order.
  - `core.config.DEFAULT_SEQUENCES: List[str]` — the first 4.
  - `core.sequences.sequences_for(age_days: int) -> List[Tuple[str, str]]` — `(name, description)` for every sequence `age_days` belongs to, in `SEQUENCE_TAXONOMY` order.
  - `core.sequences.anniversary_matches(age_days: int, included_sequences: Collection[str]) -> List[Dict]` — one dict per included match, each `{"sequence": str, "age_days": int, "description": str}`.
  - `core.sequences.anniversary_sentence(match: Dict) -> str`
  - `core.sequences.included_sequences_for_subscription(subscription: Dict) -> List[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sequences.py`:

```python
from core.config import DEFAULT_SEQUENCES, SEQUENCE_TAXONOMY
from core.sequences import (
    _ordinal,
    _SEQUENCES,
    _superscript,
    anniversary_matches,
    anniversary_sentence,
    included_sequences_for_subscription,
    sequences_for,
)


def _names(age_days):
    return [name for name, _ in sequences_for(age_days)]


def _description(age_days, sequence):
    return dict(sequences_for(age_days))[sequence]


# --- taxonomy wiring ---


def test_every_taxonomy_name_has_exactly_one_implementation():
    assert list(_SEQUENCES) == SEQUENCE_TAXONOMY


def test_the_default_set_is_the_sparse_legible_four():
    assert DEFAULT_SEQUENCES == [
        "Powers of 2",
        "Powers of 10",
        "Triangle numbers",
        "Fibonacci numbers",
    ]


def test_primes_are_never_on_by_default():
    # ~1 in 9 days is prime near a 90-year lifespan, so defaulting these on
    # would turn a handful-of-times-a-year feature into a weekly push.
    assert "Primes" not in DEFAULT_SEQUENCES
    assert set(DEFAULT_SEQUENCES).issubset(SEQUENCE_TAXONOMY)


def test_sequences_for_returns_taxonomy_order_not_discovery_order():
    # 1 hits almost everything; the result must still read in taxonomy order.
    found = _names(1)
    assert found == [name for name in SEQUENCE_TAXONOMY if name in set(found)]


# --- per-sequence membership ---


def test_powers_of_two():
    assert "Powers of 2" in _names(2048)
    assert "Powers of 2" in _names(32768)
    assert "Powers of 2" not in _names(2047)
    assert "Powers of 2" not in _names(32767)
    assert _description(2048, "Powers of 2") == "a power of two, 2¹¹"


def test_powers_of_ten():
    assert "Powers of 10" in _names(10000)
    assert "Powers of 10" not in _names(20000)
    assert "Powers of 10" not in _names(10001)
    assert _description(10000, "Powers of 10") == "a power of ten, 10⁴"


def test_triangle_numbers():
    assert "Triangle numbers" in _names(5050)
    assert "Triangle numbers" not in _names(5051)
    assert _description(5050, "Triangle numbers") == "the 100th triangular number"


def test_fibonacci_numbers():
    assert "Fibonacci numbers" in _names(4181)
    assert "Fibonacci numbers" in _names(28657)
    assert "Fibonacci numbers" not in _names(4180)
    assert _description(4181, "Fibonacci numbers") == "the 19th Fibonacci number"


def test_primes():
    assert "Primes" in _names(10007)
    assert "Primes" in _names(2)
    assert "Primes" not in _names(10008)
    assert "Primes" not in _names(32767)  # 7 * 31 * 151
    assert _description(10007, "Primes") == "a prime number"


def test_perfect_squares():
    assert "Perfect squares" in _names(10000)
    assert "Perfect squares" not in _names(10001)
    assert _description(10000, "Perfect squares") == "a perfect square, 100²"


def test_cubes():
    assert "Cubes" in _names(8000)
    assert "Cubes" not in _names(8001)
    # Guards the float cube root, which drifts at this magnitude.
    assert "Cubes" in _names(32768)
    assert _description(8000, "Cubes") == "a perfect cube, 20³"


def test_catalan_numbers():
    assert "Catalan numbers" in _names(4862)
    assert "Catalan numbers" not in _names(4863)
    # Enumerated 1-based over distinct terms (1, 2, 5, 14, ...), skipping the
    # conventional C0 = 1 which duplicates C1.
    assert _description(4862, "Catalan numbers") == "the 9th Catalan number"
    assert _description(2, "Catalan numbers") == "the 2nd Catalan number"


# --- the degenerate days, left exactly as they fall out ---


def test_day_zero_matches_nothing():
    # Your birthday. No sequence's positive enumeration contains 0, so this
    # needs no special case - and must not acquire one.
    assert sequences_for(0) == []


def test_negative_days_match_nothing():
    assert sequences_for(-1) == []
    assert sequences_for(-10000) == []


def test_day_one_matches_seven_of_eight():
    # 1 genuinely is the first power of two, power of ten, triangular number,
    # Fibonacci number, square, cube and Catalan number. Only prime is out.
    found = _names(1)
    assert set(found) == set(SEQUENCE_TAXONOMY) - {"Primes"}


# --- coincidences between sequences, deliberately not deduplicated ---


def test_day_144_is_both_a_square_and_a_fibonacci_number():
    # The largest number that is both, by Cohn's theorem. Both must be reported.
    found = _names(144)
    assert "Perfect squares" in found
    assert "Fibonacci numbers" in found


def test_days_21_and_55_are_both_fibonacci_and_triangular():
    for age_days in (21, 55):
        found = _names(age_days)
        assert "Fibonacci numbers" in found
        assert "Triangle numbers" in found


# --- anniversary_matches ---


def test_anniversary_matches_keeps_only_included_sequences():
    matches = anniversary_matches(144, ["Fibonacci numbers"])
    assert [match["sequence"] for match in matches] == ["Fibonacci numbers"]
    assert matches[0]["age_days"] == 144


def test_anniversary_matches_is_empty_when_nothing_is_included():
    assert anniversary_matches(2048, []) == []
    assert anniversary_matches(2048, None) == []


def test_anniversary_matches_ignores_names_outside_the_taxonomy():
    assert anniversary_matches(2048, ["Not A Sequence"]) == []


def test_anniversary_matches_returns_one_entry_per_matching_sequence():
    matches = anniversary_matches(1, SEQUENCE_TAXONOMY)
    assert len(matches) == 7


# --- copy ---


def test_anniversary_sentence_reads_as_a_full_sentence_with_a_grouped_number():
    match = anniversary_matches(2048, ["Powers of 2"])[0]
    assert anniversary_sentence(match) == "Your age in days (2,048) is a power of two, 2¹¹."


def test_ordinal_handles_the_teens():
    assert [_ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 100)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "100th"
    ]


def test_superscript_handles_multiple_digits():
    assert _superscript(11) == "¹¹"
    assert _superscript(2) == "²"


# --- subscription reader ---


def test_included_sequences_for_subscription_reads_the_stored_list():
    subscription = {"included_sequences": ["Primes", "Powers of 2"]}
    # Ordered by the taxonomy, not by how they were stored.
    assert included_sequences_for_subscription(subscription) == ["Powers of 2", "Primes"]


def test_included_sequences_for_subscription_survives_a_missing_column():
    # Before the alter-table lands, every subscription row is missing the key.
    # Raising here would kill the whole daily run; and "nothing" is also the
    # correct default, so this path is safe in both directions.
    assert included_sequences_for_subscription({}) == []


def test_included_sequences_for_subscription_treats_null_as_nothing():
    assert included_sequences_for_subscription({"included_sequences": None}) == []


def test_included_sequences_for_subscription_ignores_unknown_names():
    subscription = {"included_sequences": ["Powers of 2", "Retired Sequence"]}
    assert included_sequences_for_subscription(subscription) == ["Powers of 2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_sequences.py -q`
Expected: collection error — `ImportError: cannot import name 'DEFAULT_SEQUENCES' from 'core.config'`.

- [ ] **Step 3: Add the taxonomy to `src/core/config.py`**

Insert after the `CATEGORY_NAMES` definition (currently `src/core/config.py:43-45`), before `__all__`:

```python
#: Integer sequences a subscriber can have their age in days checked against.
#: Unrelated to TAG_TAXONOMY/TAG_CATEGORIES above: these describe a property of
#: the number itself, not of any event, so they are deliberately a separate
#: taxonomy rather than a ninth category.
#: WARNING: these names are persisted verbatim into subscribers'
#: subscriptions.included_sequences and matched by exact string - renaming one
#: silently drops it from every subscriber who chose it. Same hazard as
#: TAG_CATEGORIES above; see SUPABASE_SETUP.md section 13.
SEQUENCE_TAXONOMY: List[str] = [
    "Powers of 2",
    "Powers of 10",
    "Triangle numbers",
    "Fibonacci numbers",
    "Primes",
    "Perfect squares",
    "Cubes",
    "Catalan numbers",
]

#: What the sequence multiselect is pre-loaded with the first time a visitor
#: enables the feature - never a database default, and never applied to an
#: existing subscriber. The four are the sparse, legible ones; primes alone
#: land roughly 1 day in 9 near a 90-year lifespan, and squares and cubes
#: cluster densely in the first months of life.
DEFAULT_SEQUENCES: List[str] = SEQUENCE_TAXONOMY[:4]
```

Replace the `__all__` line at the bottom of the file with:

```python
__all__ = [
    "CATEGORY_NAMES",
    "DATA_DIR",
    "DEFAULT_SEQUENCES",
    "PROJECT_ROOT",
    "SEQUENCE_TAXONOMY",
    "TAG_CATEGORIES",
    "TAG_TAXONOMY",
]
```

- [ ] **Step 4: Create `src/core/sequences.py`**

```python
"""Mathematical anniversaries: days when your age in days is an interesting number.

Membership is a property of the integer, so everything here is computed live
from age_days and nothing is stored. That also means there is no upper bound to
worry about - the calendar lets a visitor browse any month, past or future, and
every predicate below is O(sqrt(n)) at worst.

Kept apart from core.matching, which relates people to events: an event carries
a name, a phrase, a date, tags and a person, and an anniversary carries none of
those. The two share no vocabulary and no data.

Each sequence is the enumeration of its POSITIVE terms. That single rule settles
both degenerate cases without a line of special-case code: day 0 (your birthday)
matches nothing, and day 1 matches seven of the eight because 1 really is the
first power of two, the first triangular number, and so on. Genuine coincidences
further up - 144 is both a perfect square and a Fibonacci number, 21 and 55 are
both Fibonacci and triangular - are reported as the several matches they are.
"""

from __future__ import annotations

from math import isqrt
from typing import Callable, Collection, Dict, List, Optional, Tuple

from core.config import SEQUENCE_TAXONOMY

_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _superscript(n: int) -> str:
    """11 -> "¹¹". Exponents read better set as exponents in an editorial layout."""
    return str(n).translate(_SUPERSCRIPT_DIGITS)


def _ordinal(n: int) -> str:
    """1 -> "1st", 12 -> "12th", 21 -> "21st"."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# Each function below answers "is n in this sequence, and how do we describe it?"
# in one call, returning the description or None. Folding the predicate and the
# copy together means they cannot drift apart - there is no way to add a sequence
# whose membership test and whose printed index disagree.


def _powers_of_two(n: int) -> Optional[str]:
    if n < 1 or n & (n - 1):
        return None
    return f"a power of two, 2{_superscript(n.bit_length() - 1)}"


def _powers_of_ten(n: int) -> Optional[str]:
    if n < 1:
        return None
    remainder = n
    while remainder % 10 == 0:
        remainder //= 10
    if remainder != 1:
        return None
    return f"a power of ten, 10{_superscript(len(str(n)) - 1)}"


def _triangle_numbers(n: int) -> Optional[str]:
    if n < 1:
        return None
    index = (isqrt(8 * n + 1) - 1) // 2
    if index * (index + 1) // 2 != n:
        return None
    return f"the {_ordinal(index)} triangular number"


def _fibonacci_numbers(n: int) -> Optional[str]:
    if n < 1:
        return None
    current, following, index = 1, 1, 1
    while current < n:
        current, following = following, current + following
        index += 1
    if current != n:
        return None
    return f"the {_ordinal(index)} Fibonacci number"


def _primes(n: int) -> Optional[str]:
    if n < 2:
        return None
    if n % 2 == 0:
        return "a prime number" if n == 2 else None
    factor = 3
    while factor * factor <= n:
        if n % factor == 0:
            return None
        factor += 2
    return "a prime number"


def _perfect_squares(n: int) -> Optional[str]:
    if n < 1:
        return None
    root = isqrt(n)
    if root * root != n:
        return None
    return f"a perfect square, {root}{_superscript(2)}"


def _cubes(n: int) -> Optional[str]:
    if n < 1:
        return None
    # The float cube root drifts by more than a whole integer at this magnitude,
    # so the neighbours are checked rather than trusted.
    estimate = round(n ** (1 / 3))
    for root in (estimate - 1, estimate, estimate + 1):
        if root > 0 and root**3 == n:
            return f"a perfect cube, {root}{_superscript(3)}"
    return None


def _catalan_numbers(n: int) -> Optional[str]:
    if n < 1:
        return None
    # Enumerated 1-based over the DISTINCT terms 1, 2, 5, 14, 42, ... The
    # conventional C0 = 1 is skipped because it duplicates C1, which would make
    # every printed ordinal ambiguous.
    current, index, k = 1, 1, 1
    while current < n:
        k += 1
        current = current * 2 * (2 * k - 1) // (k + 1)
        index += 1
    if current != n:
        return None
    return f"the {_ordinal(index)} Catalan number"


#: Keyed by SEQUENCE_TAXONOMY name. A test asserts the keys match it exactly, so
#: adding a name without an implementation fails loudly rather than silently
#: producing a sequence nothing can ever match.
_SEQUENCES: Dict[str, Callable[[int], Optional[str]]] = {
    "Powers of 2": _powers_of_two,
    "Powers of 10": _powers_of_ten,
    "Triangle numbers": _triangle_numbers,
    "Fibonacci numbers": _fibonacci_numbers,
    "Primes": _primes,
    "Perfect squares": _perfect_squares,
    "Cubes": _cubes,
    "Catalan numbers": _catalan_numbers,
}


def sequences_for(age_days: int) -> List[Tuple[str, str]]:
    """(name, description) for every sequence age_days belongs to, in taxonomy order.

    Iterates the taxonomy rather than the dict so the order is guaranteed by the
    published constant, not by a dict literal someone might reorder.
    """
    found: List[Tuple[str, str]] = []
    for name in SEQUENCE_TAXONOMY:
        description = _SEQUENCES[name](age_days)
        if description is not None:
            found.append((name, description))
    return found


def anniversary_matches(age_days: int, included_sequences: Collection[str]) -> List[Dict]:
    """One match dict per included sequence age_days belongs to.

    The shared entry point for both the calendar and the daily cron job, so the
    day you see marked and the notification you receive can never disagree.
    A day in several sequences yields several matches; they are not merged.
    """
    included = set(included_sequences or ())
    return [
        {"sequence": name, "age_days": age_days, "description": description}
        for name, description in sequences_for(age_days)
        if name in included
    ]


def anniversary_sentence(match: Dict) -> str:
    """The display sentence for a match.

    Written fresh rather than routed through core.matching.full_sentence, which
    rebuilds an opening around an event's name and event_phrase - neither of
    which an anniversary has.
    """
    return f"Your age in days ({match['age_days']:,}) is {match['description']}."


def included_sequences_for_subscription(subscription: Dict) -> List[str]:
    """The sequences a subscription tracks, in taxonomy order.

    Read defensively: a row from before the column was added has no key at all,
    and raising would kill the whole daily run rather than one subscriber. Here
    the safe read and the intended default are the same thing - absent, null and
    empty all mean "this subscriber never opted in", which is how every existing
    subscriber starts.
    """
    stored = set(subscription.get("included_sequences") or ())
    return [name for name in SEQUENCE_TAXONOMY if name in stored]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_sequences.py -q`
Expected: **28 passed**.

- [ ] **Step 6: Run the whole suite**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: 237 + 28 = **265 passed**. The event category/tag tests must be untouched — this task adds a parallel taxonomy and changes none of the existing filtering.

- [ ] **Step 7: Commit**

```bash
git add src/core/config.py src/core/sequences.py tests/test_sequences.py
git commit -m "feat: compute mathematical anniversaries from age in days

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Per-subscription sequence preference

**Files:**
- Modify: `src/core/db.py:101-140`
- Modify: `src/app/ui.py:87-91` and `:130-134` (call sites only — the control comes in Task 4)
- Modify: `SUPABASE_SETUP.md` (new section 13, appended after section 12)
- Test: `tests/test_db.py:78-113`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `create_subscription(birthday: date, excluded_tags: Optional[List[str]] = None, excluded_categories: Optional[List[str]] = None, included_sequences: Optional[List[str]] = None) -> Dict`
  - `update_subscription_filters(token: str, excluded_tags: List[str], excluded_categories: List[str], included_sequences: List[str]) -> None`

- [ ] **Step 1: Update the failing tests**

In `tests/test_db.py`, replace the three subscription tests (currently `tests/test_db.py:78-113`) with:

```python
def test_create_subscription_defaults_to_no_filters_and_no_sequences():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1))

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == []
    assert inserted["excluded_categories"] == []
    # Inclusions, not exclusions: empty means mathematical anniversaries are off.
    assert inserted["included_sequences"] == []


def test_create_subscription_stores_all_three_preference_lists():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(
            date(2000, 1, 1), ["military"], ["Sport", "Disasters"], ["Powers of 2"]
        )

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == ["military"]
    assert inserted["excluded_categories"] == ["Sport", "Disasters"]
    assert inserted["included_sequences"] == ["Powers of 2"]


def test_update_subscription_filters_writes_all_three_columns_for_the_right_token():
    mock_client = MagicMock()

    with patch("core.db.get_client", return_value=mock_client):
        update_subscription_filters("tok123", ["disaster"], ["Sport"], ["Primes"])

    mock_client.table.assert_called_with("subscriptions")
    mock_client.table.return_value.update.assert_called_with(
        {
            "excluded_tags": ["disaster"],
            "excluded_categories": ["Sport"],
            "included_sequences": ["Primes"],
        }
    )
    mock_client.table.return_value.update.return_value.eq.assert_called_with("token", "tok123")
    mock_client.table.return_value.update.return_value.eq.return_value.execute.assert_called_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_db.py -q`
Expected: FAIL — 3 failures. `create_subscription` raises `KeyError: 'included_sequences'` on the inserted-row assertions, and `update_subscription_filters` raises `TypeError: update_subscription_filters() takes 3 positional arguments but 4 were given`.

- [ ] **Step 3: Update `src/core/db.py`**

Replace `create_subscription` and `update_subscription_filters` (currently `src/core/db.py:101-140`) with:

```python
def create_subscription(
    birthday: date,
    excluded_tags: Optional[List[str]] = None,
    excluded_categories: Optional[List[str]] = None,
    included_sequences: Optional[List[str]] = None,
) -> Dict:
    """Create a new subscription (magic-link token + ntfy topic) for a birthday.

    The three preference lists carry the visitor's current calendar filters
    forward into their notification preferences, so filter-then-subscribe is one
    action. Note the asymmetry: the two event lists are exclusions (empty means
    "everything"), while included_sequences is inclusions (empty means "no
    mathematical anniversaries"), because that feature is opt-in.
    """
    client = get_client()
    row = {
        "token": secrets.token_urlsafe(12),
        "ntfy_topic": f"achage-{secrets.token_urlsafe(9)}",
        "birthday": birthday.isoformat(),
        "excluded_tags": list(excluded_tags or []),
        "excluded_categories": list(excluded_categories or []),
        "included_sequences": list(included_sequences or []),
    }
    response = client.table("subscriptions").insert(row).execute()
    return response.data[0]


def update_subscription_filters(
    token: str,
    excluded_tags: List[str],
    excluded_categories: List[str],
    included_sequences: List[str],
) -> None:
    """Overwrite a subscription's stored calendar preferences in one write."""
    client = get_client()
    client.table("subscriptions").update(
        {
            "excluded_tags": list(excluded_tags),
            "excluded_categories": list(excluded_categories),
            "included_sequences": list(included_sequences),
        }
    ).eq("token", token).execute()
```

- [ ] **Step 4: Keep the two `ui.py` call sites compiling**

The sequence control lands in Task 4, so pass an empty list for now.

At `src/app/ui.py:87-91`:

```python
                new_subscription = create_subscription(
                    birthdate,
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                    [],
                )
```

At `src/app/ui.py:130-134`:

```python
                update_subscription_filters(
                    subscription["token"],
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                    [],
                )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **265 passed** (three tests replaced by three, so the total is unchanged from Task 1).

- [ ] **Step 6: Document the migration in `SUPABASE_SETUP.md`**

Append a new section to the end of the file:

````markdown
## 13. Mathematical anniversary preferences

Run this in the SQL editor **before** deploying the mathematical-anniversary application code —
the app's "Update preferences" button writes this column, and the write fails if it doesn't exist.

```sql
alter table subscriptions add column if not exists included_sequences text[] not null default '{}';
```

No backfill is needed, and no backfill is *wanted*. Unlike `excluded_tags` and `excluded_categories`,
this column stores **inclusions**: it lists the sequences a subscriber chose to track, and an empty
array means the whole feature is off for them.

That inversion is the entire rollout mechanism. Mathematical anniversaries must not start firing at
existing subscribers who signed up for historical events — and with primes enabled a subscriber
would hear from us roughly every ninth day. `default '{}'` makes every existing row opt-out by
construction, with nothing to migrate and nobody to miss. An exclusion column would have needed a
one-off `UPDATE` over every row plus app code to keep seeding it, either of which fails open if it
misses someone.

`core.sequences.included_sequences_for_subscription` reads the column defensively
(`.get("included_sequences") or []`), so if the cron job runs before this SQL is applied it falls
back to sending no anniversaries — which is also the intended default, so a slipped deployment
order fails safe in both directions.

**Renaming a sequence orphans subscribers, exactly like renaming a category (section 11).** The
eight names in `core.config.SEQUENCE_TAXONOMY` (`"Powers of 2"`, `"Fibonacci numbers"`, …) are
persisted verbatim into this column and matched by exact string. Renaming one — even a copy edit
like "Primes" → "Prime numbers" — silently drops it from every subscriber who selected it. Keep the
old string as an alias, or migrate the arrays.
````

- [ ] **Step 7: Commit**

```bash
git add src/core/db.py src/app/ui.py tests/test_db.py SUPABASE_SETUP.md
git commit -m "feat: store per-subscription mathematical anniversary sequences

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Mathematical anniversaries in the daily notification

**Files:**
- Modify: `scripts/send_daily_notifications.py:1-17` (docstring), `:27-28` (imports), `:47-56` (add a sibling sender), `:59-77` (`main`)
- Test: none — see the note below.

**Interfaces:**
- Consumes: `core.sequences.anniversary_matches`, `core.sequences.anniversary_sentence`, `core.sequences.included_sequences_for_subscription` (Task 1).
- Produces: nothing consumed by later tasks.

**No automated test for this file.** `MATCHERS` is built at module scope and calls `fetch_events()`, which needs live Supabase credentials, so the module cannot be imported in a test. That is exactly why the logic lives in `core.sequences`, where Task 1 tested it directly — including the missing-column path.

**`MATCHERS` is deliberately left alone.** It exists so a future matcher producing *event-shaped* records can be added without restructuring. Anniversaries are not event-shaped — they have no `name`, `event_phrase`, date or tags — so folding them in would force a `"kind"` discriminator and a branch into every consumer, and would route them through `events_for_subscription`, where they'd survive only by accident via the "untagged events always survive" rule. Two lists, each with one shape, is the smaller thing.

- [ ] **Step 1: Extend the imports**

In `scripts/send_daily_notifications.py`, add below the existing `core.matching` import (line 28):

```python
from core.sequences import (
    anniversary_matches,
    anniversary_sentence,
    included_sequences_for_subscription,
)
```

- [ ] **Step 2: Add the sibling notification sender**

Insert directly after `_send_ntfy_notification` (after line 56):

```python
def _send_anniversary_notification(topic: str, anniversary: Dict, token: str) -> None:
    """Push one mathematical anniversary.

    A sibling of _send_ntfy_notification rather than a branch inside it: that
    function builds its title from event['name'], and an anniversary has no
    name, no person and no date - only a number and what's interesting about it.
    """
    headers = {"Title": "You've hit a mathematical anniversary".encode("utf-8")}
    if token and APP_BASE_URL:
        headers["Click"] = f"{APP_BASE_URL}?u={token}"
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=anniversary_sentence(anniversary).encode("utf-8"),
        headers=headers,
        timeout=10,
    )
```

- [ ] **Step 3: Send anniversaries alongside event matches**

In `main()`, replace the per-subscriber body (currently lines 68-75) with:

```python
        matches: List[Dict] = []
        for matcher in MATCHERS:
            matches.extend(matcher(age_days))
        matches = events_for_subscription(matches, subscription)

        for event in matches:
            _send_ntfy_notification(subscription["ntfy_topic"], event, subscription["token"])
            notified += 1

        # Anniversaries are computed, not looked up, and carry their own opt-in
        # preference - the event category/tag filter has no bearing on them.
        anniversaries = anniversary_matches(
            age_days, included_sequences_for_subscription(subscription)
        )
        for anniversary in anniversaries:
            _send_anniversary_notification(
                subscription["ntfy_topic"], anniversary, subscription["token"]
            )
            notified += 1
```

- [ ] **Step 4: Update the module docstring**

Replace the third paragraph of the docstring (currently lines 13-16, beginning "Matches are gathered from a small list of matcher callables…") with:

```
Matches are gathered from a small list of matcher callables rather than one
hardcoded lookup, so a future matcher producing event-shaped records can be
added without restructuring this script.

Mathematical anniversaries - days when the subscriber's age in days is itself
an interesting number - are computed separately by core.sequences and kept in
their own list, because they share no fields with an event. They carry their
own opt-in preference (subscriptions.included_sequences), which is empty for
every subscriber until they choose otherwise.
```

- [ ] **Step 5: Verify the script still parses and the suite passes**

Run: `./venv/Scripts/python.exe -m py_compile scripts/send_daily_notifications.py`
Expected: no output (success).

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **265 passed**.

- [ ] **Step 6: Commit**

```bash
git add scripts/send_daily_notifications.py
git commit -m "feat: push mathematical anniversaries in the daily notification run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Calendar behaviour — filter control, per-day lookup, dialog

Delivers the working feature. The triangle is styled in Task 5; until then an anniversary day renders
as a match button with the existing red circle, which is enough to verify the wiring.

**Files:**
- Modify: `src/app/ui.py` — imports, session seeding, subscribe call, filter expander, caption, dialog, day loop
- Test: manual — no unit test, following the existing convention. All the branching logic was put in `core.sequences` in Task 1 precisely so it could be tested without a Streamlit context.

**Line numbers in this task are stale — locate by content, not by number.** Every `src/app/ui.py`
reference below was taken against the file as it stood before this plan began, and Task 2 already
added a line to each of the two subscription call sites. The surrounding code quoted in each step is
the reliable anchor.

**Interfaces:**
- Consumes: `core.config.SEQUENCE_TAXONOMY`, `core.config.DEFAULT_SEQUENCES`, `core.sequences.anniversary_matches`, `core.sequences.anniversary_sentence`, `core.sequences.included_sequences_for_subscription` (Task 1); `core.db.create_subscription`, `core.db.update_subscription_filters` (Task 2).
- Produces: the marker container keys `mark-event-{0|1}-…`, `mark-anniv-{0|1}-…`, `mark-today-{0|1}-…` that Task 5 styles. Replaces the key `today-match-…`, which no longer exists.

- [ ] **Step 1: Extend the imports**

In `src/app/ui.py`, change the `core.config` import (line 20) to:

```python
from core.config import CATEGORY_NAMES, DEFAULT_SEQUENCES, SEQUENCE_TAXONOMY, TAG_TAXONOMY
```

and add below the `core.matching` import block (after line 34):

```python
from core.sequences import (
    anniversary_matches,
    anniversary_sentence,
    included_sequences_for_subscription,
)
```

- [ ] **Step 2: Seed the sequence selection and compute the live value**

Insert after the `included_categories` seeding block (after `src/app/ui.py:72`), before `if subscription:`:

```python
# Mathematical anniversaries are opt-in, so the checkbox starts off for everyone
# who hasn't already chosen sequences - including existing subscribers, whose
# stored column is empty. The multiselect behind it is pre-loaded with the
# recommended four regardless, so enabling the feature is one click rather than
# five.
if "included_sequences" not in st.session_state:
    stored_sequences = included_sequences_for_subscription(subscription) if subscription else []
    st.session_state.included_sequences = stored_sequences or list(DEFAULT_SEQUENCES)
    st.session_state.anniversaries_on = bool(stored_sequences)

# Read here for the "Get notified" button below, which runs earlier in the
# script than the expander that renders the sequence widgets. Correct for that
# use: clicking the button is its own rerun, so widget state is current at the
# top of it. The expander RE-READS this into the same name after the widgets
# have run, because the multiselect assigns mid-script - see Task 4 Step 4.
active_sequences = (
    st.session_state.included_sequences if st.session_state.anniversaries_on else []
)
```

- [ ] **Step 3: Carry the sequences into the subscribe call**

Replace the `[]` placeholder from Task 2 at `src/app/ui.py` in the "Get notified" block:

```python
                new_subscription = create_subscription(
                    birthdate,
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                    active_sequences,
                )
```

- [ ] **Step 4: Add the sequence control to the filter expander**

Replace the whole expander block (currently `src/app/ui.py:114-138`) with:

```python
with st.expander("Filter what shows up on the calendar"):
    st.multiselect("Show events about:", options=CATEGORY_NAMES, key="included_categories")
    st.caption(
        "Every event belongs to exactly one of these. Events that haven't been "
        "tagged yet always show up, whatever you pick."
    )
    # A popover, not a nested expander - Streamlit rejects expander-in-expander.
    with st.popover("Advanced: filter by detailed tag"):
        st.multiselect("Show events tagged:", options=TAG_TAXONOMY, key="included_tags")
        st.caption(
            "These narrow things down within the categories you kept above. "
            "Unchecking a tag can never bring back an event from a category you hid."
        )

    st.checkbox("Also mark mathematical anniversaries", key="anniversaries_on")
    st.caption(
        "Days when your age in days is itself an interesting number. "
        "Marked with a triangle instead of a circle."
    )
    if st.session_state.anniversaries_on:
        # Deliberately NOT given a widget key: Streamlit discards the state of
        # widgets it didn't render, so a keyed multiselect would lose its
        # selection every time the checkbox above is unticked, and the read
        # above would then raise.
        st.session_state.included_sequences = st.multiselect(
            "Track these sequences:",
            options=SEQUENCE_TAXONOMY,
            default=st.session_state.included_sequences,
        )
        st.caption(
            "Primes and squares are off to begin with because they'd land far more "
            "often — a prime day comes round roughly once every nine days."
        )

    # Refresh the value now that the widgets above have run. The copy made at
    # the top of the script is already stale by this point: the multiselect
    # assigns included_sequences here, mid-script, so everything BELOW the
    # expander - the save button and the calendar - must re-read it or it will
    # render one interaction behind.
    active_sequences = (
        st.session_state.included_sequences if st.session_state.anniversaries_on else []
    )

    if subscription:
        if st.button("Update preferences"):
            try:
                update_subscription_filters(
                    subscription["token"],
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                    active_sequences,
                )
            except Exception:
                st.error("Couldn't save your preferences — try again in a moment.")
            else:
                st.success("Saved — your notifications will follow these filters from now on.")
```

**Both reads of `active_sequences` are load-bearing; neither is redundant.** The one in Step 2 runs
before these widgets exist and serves the "Get notified" button above them. This one runs after they
have rendered and serves everything below. Collapsing them to a single read at the top makes the
calendar lag one interaction behind the multiselect.

- [ ] **Step 5: Update the calendar caption**

Replace `src/app/ui.py:140`:

```python
st.caption("A red circle marks a day that matches a historical event, a triangle marks a mathematical anniversary — click either for details. A filled black date marks today. Use the filter above to narrow what counts.")
```

- [ ] **Step 6: Rewrite the dialog to hold both kinds**

Replace `show_event_dialog` (currently `src/app/ui.py:143-159`) with:

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

- [ ] **Step 7: Rewrite the day-cell rendering**

Replace the body of the innermost day loop (currently `src/app/ui.py:226-255`, from `day_date = date(...)` to the end of the file) with:

```python
            day_date = date(view_year, view_month, day)
            age_days = (day_date - birthdate).days
            day_matches = filter_events(
                EVENTS_BY_AGE.get(age_days, []),
                st.session_state.included_categories,
                st.session_state.included_tags,
            )
            day_anniversaries = anniversary_matches(age_days, active_sequences)
            is_today = day_date == today

            if not day_matches and not day_anniversaries:
                cell_class = "aa-cal-cell aa-today" if is_today else "aa-cal-cell"
                col.markdown(f"<div class='{cell_class}'>{day}</div>", unsafe_allow_html=True)
                continue

            # Three independent marks (circle, triangle, today's black fill) but
            # a container carries exactly one key, so they nest one per flag
            # rather than encoding all eight combinations in a single key. The
            # CSS matches each class as a descendant, so the depth is irrelevant.
            suffix = f"{view_year}-{view_month}-{day}"
            with col.container(key=f"mark-event-{int(bool(day_matches))}-{suffix}"):
                with st.container(key=f"mark-anniv-{int(bool(day_anniversaries))}-{suffix}"):
                    with st.container(key=f"mark-today-{int(is_today)}-{suffix}"):
                        if st.button(
                            str(day),
                            key=f"day_{suffix}",
                            type="primary",
                            use_container_width=True,
                        ):
                            show_day_dialog(day_date, day_matches, day_anniversaries)
```

- [ ] **Step 8: Verify nothing still references the old names**

Run: `grep -rn "show_event_dialog\|today-match-" src scripts tests`
Expected: no output.

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **265 passed** (unchanged — this task adds no tests).

- [ ] **Step 9: Verify the behaviour in the browser**

Start the app in the background:

```bash
./venv/Scripts/streamlit.exe run src/app/ui.py --server.port 8517
```

Then `preview_start {url: "http://localhost:8517"}`. Set the birthday to **1990-01-01** and navigate
to **May 1994**. With the four default sequences on, exactly two days there are anniversaries and
they are adjacent, which makes both the presence and the absence easy to see: **the 16th** (day
1,596, the 56th triangular number) and **the 17th** (day 1,597, the 17th Fibonacci number). Check
each of:

1. The expander is titled "Filter what shows up on the calendar" and opens without a `StreamlitAPIException`.
2. "Also mark mathematical anniversaries" starts **unticked**, and the 16th and 17th are unmarked.
3. Ticking it reveals a multiselect already holding exactly `Powers of 2`, `Powers of 10`, `Triangle numbers`, `Fibonacci numbers` — and the 16th and 17th become clickable buttons.
4. Adding `Primes` to the multiselect marks roughly one day in nine across the month; removing it takes them away again.
5. Unticking the checkbox removes every anniversary mark, and re-ticking restores the same selection (this is the widget-state-garbage-collection case — if the selection resets or an error appears, the multiselect has acquired a `key=` it must not have).
6. Clicking the 17th opens a dialog titled "This day" with a **Mathematical anniversaries** section reading "Your age in days (1,597) is the 17th Fibonacci number." and **no** "Historical matches" heading.
7. Browse nearby months for a day carrying both a circle and an anniversary (turning `Primes` on makes one easy to find) and confirm the dialog shows both sections, separated.
8. Changing the event category filter does not change which anniversary days are marked.
9. `read_console_messages` shows no errors.

- [ ] **Step 10: Commit**

```bash
git add src/app/ui.py
git commit -m "feat: mark and explain mathematical anniversaries on the calendar

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The triangle marker, and docs

**Files:**
- Modify: `src/app/styles.py:68-87` (marker rules), `:100-109` (mobile block)
- Modify: `README.md:11-26`
- Modify: `IDEAS.md`
- Test: manual — visual.

**Interfaces:**
- Consumes: the `mark-event-1-…` / `mark-anniv-1-…` / `mark-today-1-…` container classes from Task 4.
- Produces: nothing.

- [ ] **Step 1: Narrow the circle and add the triangle**

In `src/app/styles.py`, replace the marker block (currently lines 68-87, from the `/* Match-day buttons ... */` comment through the end of the today-match rules) with:

```css
/* Day buttons: shared geometry for any marked day, whichever mark it carries. */
.st-key-calendar-grid button[data-testid="stBaseButton-primary"] {
    position: relative; aspect-ratio: 1; width: 100%;
    background: transparent !important; border: none !important; box-shadow: none !important;
    font-weight: 700;
}
.st-key-calendar-grid button[data-testid="stBaseButton-primary"],
.st-key-calendar-grid button[data-testid="stBaseButton-primary"] * { color: var(--aa-accent) !important; }

/* Historical match: the hand-circled-in-red-pen mark. Scoped to the marker
   class rather than to every primary button, so an anniversary-only day
   doesn't inherit a circle it hasn't earned. */
[class*="st-key-mark-event-1-"] button[data-testid="stBaseButton-primary"]::before {
    content: ''; position: absolute; top: 50%; left: 50%;
    width: 30px; height: 30px; margin: -15px 0 0 -15px;
    border: 2.5px solid var(--aa-accent); border-radius: 50%; transform: rotate(-8deg);
}

/* Mathematical anniversary: a triangle drawn around the same centre, so a day
   carrying both marks gets both superimposed with no extra element. An SVG data
   URI rather than the CSS border trick, which can only produce a FILLED
   triangle - the mark has to be an outline to read as the same pen as the
   circle. The stroke colour is baked in because a data URI can't reference
   --aa-accent; keep %23a01f1f in step with it. */
[class*="st-key-mark-anniv-1-"] button[data-testid="stBaseButton-primary"]::after {
    content: ''; position: absolute; top: 50%; left: 50%;
    width: 36px; height: 36px; margin: -19px 0 0 -18px;
    background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Cpolygon points='20,4 36,34 4,34' fill='none' stroke='%23a01f1f' stroke-width='2.5' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat;
    transform: rotate(3deg);
    pointer-events: none;
}

/* A marked day that is also today: black fill, marks still in red. */
[class*="st-key-mark-today-1-"] button[data-testid="stBaseButton-primary"] {
    background: var(--aa-ink) !important;
}
[class*="st-key-mark-today-1-"] button[data-testid="stBaseButton-primary"],
[class*="st-key-mark-today-1-"] button[data-testid="stBaseButton-primary"] * { color: var(--aa-bg) !important; }
```

The vertical offset is `-19px` against a `-18px` horizontal one on purpose: a triangle's visual
centre sits below its geometric one, so it needs nudging up to look concentric with the circle.

- [ ] **Step 2: Scale both marks down on mobile**

In the `@media (max-width: 480px)` block, replace the existing `::before` rule with these two:

```css
    [class*="st-key-mark-event-1-"] button[data-testid="stBaseButton-primary"]::before {
        width: 24px; height: 24px; margin: -12px 0 0 -12px; border-width: 2px;
    }
    [class*="st-key-mark-anniv-1-"] button[data-testid="stBaseButton-primary"]::after {
        width: 30px; height: 30px; margin: -16px 0 0 -15px;
    }
```

- [ ] **Step 3: Verify the marks in the browser**

With the app running (Task 4 Step 9), birthday **1990-01-01**, anniversaries enabled with the four
defaults plus `Primes` (which gives roughly one anniversary day in nine, so every mark combination
is easy to find), browse **May 1994** and the months around it. Check:

1. An anniversary-only day shows a triangle and **no** circle.
2. A historical-match-only day shows a circle and **no** triangle.
3. A day with both shows both, concentric — the triangle around the circle, neither clipped by the cell.
4. Today, when it is also a marked day, is a black-filled cell with its marks still visible.
5. `resize_window {preset: "mobile"}`, reload, and confirm the grid is still 7 columns wide and neither mark overflows its cell.
6. `read_console_messages` shows no errors.

Capture a screenshot of a month containing all three mark states as proof. If the triangle looks
crowded against the cell border, adjust only the `width`/`height`/`margin` triple — the geometry is
the one thing here that is meant to be tuned by eye.

- [ ] **Step 4: Update the README feature list**

In `README.md`, add a bullet after the "Tag filtering" bullet (line 17-19):

```markdown
- **Mathematical anniversaries** — optionally also mark the days when your age in days is itself an
  interesting number: a power of two, a Fibonacci number, the 100th triangular number. Off by
  default, and each sequence can be switched on or off individually — primes are left off to begin
  with, since roughly one day in nine is one.
```

Then update the calendar-legend line in "Using the app" (line 29-30) to mention the second mark:

```markdown
1. Open the app and enter your birthday. You'll immediately see your current age and a calendar
   with a red circle marking any day that matches a historical event, and a triangle marking a
   mathematical anniversary if you've turned those on. Today's date is filled in black.
```

- [ ] **Step 5: Rewrite the IDEAS.md entry**

In `IDEAS.md`, replace the final entry (the four lines beginning "Add another type of calendar item
for \"mathematical anniversary days\"") with:

```markdown
- ~~Mathematical anniversary days~~ **Done.** Days when your age in days is itself an interesting
  number are marked with a triangle, computed live from `core.sequences` with no stored data. Eight
  sequences (`core.config.SEQUENCE_TAXONOMY`), individually selectable, with the whole feature
  opt-in via `subscriptions.included_sequences` — stored as inclusions, unlike the event filters, so
  it stays off for existing subscribers without a migration. Specs:
  `docs/superpowers/specs/2026-08-12-mathematical-anniversaries-design.md`.
- Let a subscriber track an arbitrary OEIS sequence by number, alongside the eight built-in ones.
  Needs its own design: an input and validation for the sequence id, a way to fetch and cache its
  terms, and per-subscriber sequence definitions rather than a fixed taxonomy.
```

- [ ] **Step 6: Run the full suite one last time**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **265 passed**.

- [ ] **Step 7: Commit**

```bash
git add src/app/styles.py README.md IDEAS.md
git commit -m "feat: mark mathematical anniversaries with a triangle on the calendar

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Deployment

Run the `included_sequences` SQL from Task 2 Step 6 in the Supabase SQL editor **before** deploying
the application code — Task 4's "Update preferences" button writes the column directly and would
fail against a database missing it.

Unlike the two event-filter migrations, a slipped order here is harmless in both directions: the
defensive read in `core.sequences.included_sequences_for_subscription` degrades a missing column to
"no sequences", which is also the intended default for every existing subscriber.
