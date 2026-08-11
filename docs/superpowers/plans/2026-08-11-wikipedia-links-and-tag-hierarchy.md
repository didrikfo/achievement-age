# Wikipedia Links, Full Subscription URLs, and Tag Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate and display verified Wikipedia links for people, make the copyable subscription link a real bookmarkable URL, and put a two-level (8 coarse categories over the existing 20 tags) filter in front of the calendar and notifications.

**Architecture:** The coarse category is *derived*, not stored — an ordered `TAG_CATEGORIES` mapping in `core/config.py` doubles as the precedence order, and `core.matching.primary_category()` picks the first matching category from an event's existing tags at read time. No re-tagging, no category column, no LLM run. Wikipedia links are fetched by a one-off resumable ingest script that batch-resolves article titles against the MediaWiki API and verifies each candidate's birth year against the birth date already implied by the person's events.

**Tech Stack:** Python 3.11+, Streamlit 1.45.1, Supabase (PostgREST via `supabase-py`), `requests`, pytest.

## Global Constraints

- Run all commands from the repo root with the project venv: `./venv/Scripts/python.exe`.
- Test command: `./venv/Scripts/python.exe -m pytest -q`. Baseline before this plan: **195 passed**.
- `pyproject.toml` sets `pythonpath = ["src"]`, so tests import `core.*`, `app.*`, `ingest.*` directly.
- **Streamlit expanders may not be nested** (`StreamlitAPIException: Expanders may not be nested inside other expanders`, `delta_generator.py:601`). A `st.popover` inside an expander **is** allowed — only popover-in-popover is rejected. The advanced tag control therefore uses `st.popover`.
- The event corpus is 3,341 events / 1,682 persons, all events tagged and all carrying a `person_id`. 0 persons currently have a `wikipedia_url`.
- Fine-tag taxonomy (`core.config.TAG_TAXONOMY`) is unchanged by this plan: `military, politics, science, technology, exploration, space, arts, music, film, sports, religion, royalty, economics, law, disaster, health, social, education, philosophy, engineering`.
- Category display names are exactly: `Sport`, `Disasters`, `Exploration & Space`, `Arts & Culture`, `Science & Technology`, `Society & Belief`, `War & Conflict`, `Politics & Power` — in that order, which **is** the precedence order.
- Subscription preferences are stored as **exclusions**, never inclusions, so taxonomy additions default to visible.
- Commit messages use conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `refactor:`) and end with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- Do **not** launch the app with the Browser pane's `preview_start {name: ...}` mode if working in a git worktree — it always starts from the main checkout. Launch Streamlit manually instead: `./venv/Scripts/streamlit.exe run src/app/ui.py --server.port 8517` (background), then `preview_start {url: "http://localhost:8517"}`.
- Spec: `docs/superpowers/specs/2026-08-11-wikipedia-links-and-tag-hierarchy-design.md`.

## File Structure

| File | Responsibility |
|---|---|
| `src/core/config.py` (modify) | Adds `TAG_CATEGORIES` (ordered category → tags) and `CATEGORY_NAMES`. |
| `src/core/matching.py` (modify) | Adds `primary_category`; replaces `filter_events_by_tags` with `filter_events`; generalizes the inversion helpers; adds `included_categories_for_subscription`. |
| `src/core/db.py` (modify) | `create_subscription` / `update_subscription_filters` carry `excluded_categories`. |
| `src/app/links.py` (create) | URL helpers: `base_url_from`, `app_base_url`, `subscription_link`, `further_reading_links`. Pure parts unit-testable without a Streamlit runtime. |
| `src/app/ui.py` (modify) | Category multiselect + advanced tag popover, full subscription URL + setup copy, "further reading" line in the event dialog. |
| `src/ingest/sources/wikipedia.py` (create) | Network layer: batched MediaWiki title resolution, Wikidata REST birth-year lookup. |
| `src/ingest/backfill_person_wikipedia.py` (create) | One-off resumable backfill: derive birth dates, resolve, verify, write, report. |
| `scripts/send_daily_notifications.py` | No change — it calls `events_for_subscription`, whose signature is unchanged. |
| `SUPABASE_SETUP.md` (modify) | New section 11 (`excluded_categories` SQL) and section 12 (Wikipedia backfill runbook). |
| `IDEAS.md` (modify) | Tag-filtering entry rewritten from stale to-do to shipped description. |

---

### Task 1: Category taxonomy and `primary_category`

**Files:**
- Modify: `src/core/config.py`
- Modify: `src/core/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `core.config.TAG_TAXONOMY` (existing list of 20 tag names).
- Produces: `core.config.TAG_CATEGORIES: Dict[str, List[str]]`, `core.config.CATEGORY_NAMES: List[str]`, `core.matching.primary_category(event: Dict) -> Optional[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matching.py`:

```python
def test_categories_partition_the_tag_taxonomy():
    homed = [tag for tags in TAG_CATEGORIES.values() for tag in tags]
    assert sorted(homed) == sorted(TAG_TAXONOMY)
    assert len(homed) == len(set(homed))


def test_primary_category_of_a_single_tag_event():
    assert primary_category(_tagged(["science"])) == "Science & Technology"


def test_primary_category_prefers_the_more_specific_category():
    # 398 events in the corpus carry both. War is the specific subject; politics
    # is the background theme, so these must be excludable as war.
    assert primary_category(_tagged(["military", "politics"])) == "War & Conflict"
    # Tag order within the event must not change the answer.
    assert primary_category(_tagged(["politics", "military"])) == "War & Conflict"


def test_primary_category_keeps_general_pairs_in_the_general_category():
    assert primary_category(_tagged(["law", "politics"])) == "Politics & Power"
    assert primary_category(_tagged(["politics", "royalty"])) == "Politics & Power"


def test_primary_category_of_an_untagged_event_is_none():
    assert primary_category(_tagged([])) is None
    assert primary_category({"name": "Someone"}) is None


def test_primary_category_ignores_tags_outside_the_taxonomy():
    # Only reachable by a hand edit in the Supabase table editor - must not raise.
    assert primary_category(_tagged(["not-a-real-tag"])) is None
    assert primary_category(_tagged(["not-a-real-tag", "sports"])) == "Sport"
```

Extend the existing imports at the top of `tests/test_matching.py`:

```python
from core.config import CATEGORY_NAMES, TAG_CATEGORIES, TAG_TAXONOMY
from core.matching import (
    events_by_age_days,
    events_for_subscription,
    excluded_from_included,
    filter_events_by_tags,
    full_sentence,
    included_from_excluded,
    included_tags_for_subscription,
    name_matches_text,
    normalize_name,
    primary_category,
)
```

(`_tagged` is the existing helper at `tests/test_matching.py:116`; `CATEGORY_NAMES` is imported now because Task 2's tests use it.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_matching.py -q`
Expected: collection error — `ImportError: cannot import name 'TAG_CATEGORIES' from 'core.config'`.

- [ ] **Step 3: Add the taxonomy to `src/core/config.py`**

Replace the `TAG_TAXONOMY` block's trailing `__all__` line and add below the existing list:

```python
#: Coarse categories over TAG_TAXONOMY, used as the default level of filtering.
#: Every tag belongs to exactly one category (asserted by a test), and the key
#: order IS the precedence order used to give an event a single category:
#: most-specific subject first, most-general background theme last. An event
#: tagged military+politics is a war event someone excluding war expects to
#: lose; an event tagged law+politics is ordinary politics.
TAG_CATEGORIES: Dict[str, List[str]] = {
    "Sport": ["sports"],
    "Disasters": ["disaster"],
    "Exploration & Space": ["exploration", "space"],
    "Arts & Culture": ["arts", "music", "film", "philosophy"],
    "Science & Technology": ["science", "technology", "engineering", "health"],
    "Society & Belief": ["religion", "social", "education"],
    "War & Conflict": ["military"],
    "Politics & Power": ["politics", "law", "royalty", "economics"],
}

#: The category taxonomy, in precedence order. The categories counterpart of
#: TAG_TAXONOMY - what a filter selection is inverted against.
CATEGORY_NAMES: List[str] = list(TAG_CATEGORIES)

__all__ = ["CATEGORY_NAMES", "DATA_DIR", "PROJECT_ROOT", "TAG_CATEGORIES", "TAG_TAXONOMY"]
```

Update the import line at the top of the file:

```python
from typing import Dict, List
```

- [ ] **Step 4: Add `primary_category` to `src/core/matching.py`**

Change the config import at the top of the file:

```python
from core.config import CATEGORY_NAMES, TAG_CATEGORIES, TAG_TAXONOMY
```

and add, above `included_from_excluded`:

```python
def primary_category(event: Dict) -> Optional[str]:
    """The event's single coarse category, or None if it has no recognized tag.

    Derived from the event's tags at read time rather than stored, so it costs
    no schema change and self-corrects when an event's tags are edited. The
    first category in TAG_CATEGORIES order that the event has any tag in wins -
    see the precedence note there.
    """
    tags = set(event.get("tags") or ())
    for category, category_tags in TAG_CATEGORIES.items():
        if tags.intersection(category_tags):
            return category
    return None
```

Add `Optional` to the typing import on line 7:

```python
from typing import Collection, Dict, Iterable, List, Optional, Sequence
```

(`Sequence` is imported now because Task 2 uses it.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_matching.py -q`
Expected: PASS, no failures.

- [ ] **Step 6: Run the whole suite**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: 195 + 6 = **201 passed**.

- [ ] **Step 7: Commit**

```bash
git add src/core/config.py src/core/matching.py tests/test_matching.py
git commit -m "feat: derive a single coarse category for each event from its tags

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Two-level filtering in `core.matching`

**Files:**
- Modify: `src/core/matching.py`
- Modify: `src/app/ui.py:210-212` (call site only — the UI control comes in Task 4)
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `core.config.CATEGORY_NAMES`, `core.config.TAG_TAXONOMY`, `core.matching.primary_category`.
- Produces:
  - `included_from_excluded(excluded: Collection[str], taxonomy: Sequence[str] = TAG_TAXONOMY) -> List[str]`
  - `excluded_from_included(included: Collection[str], taxonomy: Sequence[str] = TAG_TAXONOMY) -> List[str]`
  - `filter_events(events: Iterable[Dict], included_categories: Collection[str], included_tags: Collection[str]) -> List[Dict]` (replaces `filter_events_by_tags`)
  - `included_categories_for_subscription(subscription: Dict) -> List[str]`
  - `events_for_subscription(events, subscription) -> List[Dict]` (signature unchanged, now filters on both axes)

- [ ] **Step 1: Rewrite the affected tests in `tests/test_matching.py`**

Replace the five `filter_events_by_tags` tests (currently `tests/test_matching.py:120-145`) with:

```python
ALL_CATEGORIES = CATEGORY_NAMES
ALL_TAGS = TAG_TAXONOMY


def test_untagged_event_survives_every_filter():
    untagged = _tagged([])
    assert filter_events([untagged], ALL_CATEGORIES, ALL_TAGS) == [untagged]
    assert filter_events([untagged], [], []) == [untagged]


def test_event_in_an_excluded_category_is_dropped():
    military = _tagged(["military"])
    assert filter_events([military], ["Science & Technology"], ALL_TAGS) == []


def test_event_in_an_included_category_survives():
    military = _tagged(["military"])
    assert filter_events([military], ["War & Conflict"], ALL_TAGS) == [military]


def test_category_gate_beats_a_kept_secondary_tag():
    # politics is kept, but the event's category is War & Conflict, which is not:
    # each event lives in exactly one bucket and cannot leak back in via a tag.
    event = _tagged(["military", "politics"])
    assert filter_events([event], ["Politics & Power"], ALL_TAGS) == []


def test_advanced_tags_narrow_within_a_kept_category():
    # Category kept, but every one of the event's tags is unchecked.
    event = _tagged(["music"])
    assert filter_events([event], ["Arts & Culture"], ["arts", "film"]) == []
    assert filter_events([event], ["Arts & Culture"], ["arts", "music"]) == [event]


def test_all_selected_is_a_no_op():
    events = [_tagged(["military"]), _tagged(["science", "space"]), _tagged([])]
    assert filter_events(events, ALL_CATEGORIES, ALL_TAGS) == events


def test_missing_tags_key_is_treated_as_untagged():
    # An event dict from a code path that never set "tags" must not raise.
    event = {"name": "Someone", "age_days": 1, "event_phrase": ""}
    assert filter_events([event], [], []) == [event]
```

Replace the `events_for_subscription` / `included_tags_for_subscription` tests (currently `tests/test_matching.py:148-183`) with:

```python
def test_events_for_subscription_applies_stored_tag_exclusions():
    science = _tagged(["science"])
    military = _tagged(["military"])
    subscription = {"excluded_tags": ["military"], "excluded_categories": ["War & Conflict"]}
    assert events_for_subscription([science, military], subscription) == [science]


def test_events_for_subscription_applies_stored_category_exclusions():
    science = _tagged(["science"])
    sport = _tagged(["sports"])
    subscription = {"excluded_categories": ["Sport"]}
    assert events_for_subscription([science, sport], subscription) == [science]


def test_events_for_subscription_survives_a_missing_column():
    # Before the alter-table lands, subscription rows have no excluded_categories
    # key at all. That must mean "no filtering", not a KeyError that kills the cron.
    science = _tagged(["science"])
    military = _tagged(["military"])
    assert events_for_subscription([science, military], {}) == [science, military]


def test_events_for_subscription_treats_null_columns_as_no_filtering():
    events = [_tagged(["science"])]
    subscription = {"excluded_tags": None, "excluded_categories": None}
    assert events_for_subscription(events, subscription) == events


def test_included_tags_for_subscription_applies_stored_exclusions():
    subscription = {"excluded_tags": ["military", "sports"]}
    result = included_tags_for_subscription(subscription)
    assert "military" not in result
    assert "sports" not in result
    assert "science" in result


def test_included_tags_for_subscription_survives_a_missing_column():
    assert included_tags_for_subscription({}) == TAG_TAXONOMY


def test_included_categories_for_subscription_applies_stored_exclusions():
    subscription = {"excluded_categories": ["Sport", "Disasters"]}
    result = included_categories_for_subscription(subscription)
    assert "Sport" not in result
    assert "Disasters" not in result
    assert "Politics & Power" in result


def test_included_categories_for_subscription_survives_a_missing_column():
    assert included_categories_for_subscription({}) == CATEGORY_NAMES
    assert included_categories_for_subscription({"excluded_categories": None}) == CATEGORY_NAMES
```

Add two tests for the generalized inversion helpers, next to the existing ones:

```python
def test_inversion_helpers_work_over_the_category_taxonomy():
    stored = excluded_from_included(["Sport", "Disasters"], CATEGORY_NAMES)
    assert "Sport" not in stored
    assert "Politics & Power" in stored
    assert included_from_excluded(stored, CATEGORY_NAMES) == ["Sport", "Disasters"]


def test_inversion_helpers_order_by_the_given_taxonomy():
    # Input order must not leak through: Sport precedes Disasters in CATEGORY_NAMES.
    assert included_from_excluded(
        [name for name in CATEGORY_NAMES if name not in {"Sport", "Disasters"}],
        CATEGORY_NAMES,
    ) == ["Sport", "Disasters"]
```

Update the import block at the top of the file — `filter_events_by_tags` is gone:

```python
from core.matching import (
    events_by_age_days,
    events_for_subscription,
    excluded_from_included,
    filter_events,
    full_sentence,
    included_categories_for_subscription,
    included_from_excluded,
    included_tags_for_subscription,
    name_matches_text,
    normalize_name,
    primary_category,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_matching.py -q`
Expected: collection error — `ImportError: cannot import name 'filter_events' from 'core.matching'`.

- [ ] **Step 3: Rewrite the helpers in `src/core/matching.py`**

Replace `included_from_excluded`, `excluded_from_included`, `filter_events_by_tags`, `included_tags_for_subscription` and `events_for_subscription` with:

```python
def included_from_excluded(
    excluded: Collection[str], taxonomy: Sequence[str] = TAG_TAXONOMY
) -> List[str]:
    """Return the taxonomy entries a subscriber should see, given what they excluded.

    Ordered by `taxonomy` so the result is stable regardless of input order.
    Defaults to the tag taxonomy; pass CATEGORY_NAMES for the coarse one.
    """
    excluded_set = set(excluded or ())
    return [entry for entry in taxonomy if entry not in excluded_set]


def excluded_from_included(
    included: Collection[str], taxonomy: Sequence[str] = TAG_TAXONOMY
) -> List[str]:
    """Return what to store for a UI selection: the taxonomy minus that selection.

    Exclusions are stored rather than inclusions so an entry added to the
    taxonomy later is visible to existing subscribers by default instead of
    silently hidden.
    """
    included_set = set(included or ())
    return [entry for entry in taxonomy if entry not in included_set]


def filter_events(
    events: Iterable[Dict],
    included_categories: Collection[str],
    included_tags: Collection[str],
) -> List[Dict]:
    """Keep events whose coarse category is included and that keep at least one tag.

    Two levels, deliberately asymmetric:

    - An event with no recognized category always survives. The corpus is tagged
      by a manually-run backfill, so untagged rows are a normal transient state -
      hiding them would drop real matches because of backfill lag rather than
      user intent.
    - Otherwise the event's single primary_category must be included, AND at
      least one of its tags must be included. The category gate comes first, so
      an event can never leak back in from a hidden category on a secondary tag;
      the tag check can only narrow within kept categories. With every tag
      selected (the default) this reduces to pure category filtering.
    """
    categories = set(included_categories or ())
    tags = set(included_tags or ())
    kept: List[Dict] = []
    for event in events:
        category = primary_category(event)
        if category is None:
            kept.append(event)
            continue
        if category in categories and any(tag in tags for tag in event.get("tags") or ()):
            kept.append(event)
    return kept


def included_tags_for_subscription(subscription: Dict) -> List[str]:
    """The fine tags a subscription should see. Missing/null column means everything."""
    return included_from_excluded(subscription.get("excluded_tags") or [], TAG_TAXONOMY)


def included_categories_for_subscription(subscription: Dict) -> List[str]:
    """The categories a subscription should see. Missing/null column means everything."""
    return included_from_excluded(subscription.get("excluded_categories") or [], CATEGORY_NAMES)


def events_for_subscription(events: Iterable[Dict], subscription: Dict) -> List[Dict]:
    """Filter events by a subscription's stored category and tag preferences.

    Reads both columns defensively: if either hasn't been added to the database
    yet, every subscriber's row is missing that key, and raising here would kill
    the whole daily notification run rather than just one subscriber. Absent or
    null means no filtering on that axis, which is the pre-feature behavior.
    """
    return filter_events(
        events,
        included_categories_for_subscription(subscription),
        included_tags_for_subscription(subscription),
    )
```

- [ ] **Step 4: Update the one call site in `src/app/ui.py`**

The filter control itself lands in Task 4; this keeps the app running in between. Change the import block (`src/app/ui.py:20`):

```python
from core.config import CATEGORY_NAMES, TAG_TAXONOMY
```

and the matching import:

```python
from core.matching import (
    events_by_age_days,
    excluded_from_included,
    filter_events,
    full_sentence,
    included_tags_for_subscription,
)
```

Then replace the day-matches lookup (`src/app/ui.py:210-212`):

```python
            day_matches = filter_events(
                EVENTS_BY_AGE.get(age_days, []), CATEGORY_NAMES, st.session_state.included_tags
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **207 passed** (201, minus the 11 replaced tests, plus 17 new).

- [ ] **Step 6: Verify nothing still references the old name**

Run: `grep -rn "filter_events_by_tags" src scripts tests`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add src/core/matching.py src/app/ui.py tests/test_matching.py
git commit -m "feat: gate event filtering on coarse category before fine tags

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Store category exclusions per subscription

**Files:**
- Modify: `src/core/db.py:101-131`
- Modify: `src/app/ui.py` (two call sites)
- Modify: `SUPABASE_SETUP.md` (new section 11)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `create_subscription(birthday: date, excluded_tags: Optional[List[str]] = None, excluded_categories: Optional[List[str]] = None) -> Dict`
  - `update_subscription_filters(token: str, excluded_tags: List[str], excluded_categories: List[str]) -> None` (replaces `update_subscription_tags`)

- [ ] **Step 1: Write the failing tests**

In `tests/test_db.py`, change the import line to:

```python
from core.db import create_subscription, fetch_events, update_subscription_filters
```

Replace `test_update_subscription_tags_targets_the_right_token` (`tests/test_db.py:101-110`) and add the category cases:

```python
def test_create_subscription_defaults_to_no_exclusions_on_either_axis():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1))

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == []
    assert inserted["excluded_categories"] == []


def test_create_subscription_stores_both_exclusion_lists():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1), ["military"], ["Sport", "Disasters"])

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == ["military"]
    assert inserted["excluded_categories"] == ["Sport", "Disasters"]


def test_update_subscription_filters_writes_both_columns_for_the_right_token():
    mock_client = MagicMock()

    with patch("core.db.get_client", return_value=mock_client):
        update_subscription_filters("tok123", ["disaster"], ["Sport"])

    mock_client.table.assert_called_with("subscriptions")
    mock_client.table.return_value.update.assert_called_with(
        {"excluded_tags": ["disaster"], "excluded_categories": ["Sport"]}
    )
    mock_client.table.return_value.update.return_value.eq.assert_called_with("token", "tok123")
    mock_client.table.return_value.update.return_value.eq.return_value.execute.assert_called_once()
```

Delete the now-superseded `test_create_subscription_defaults_to_no_exclusions` and
`test_create_subscription_stores_the_given_exclusions` (`tests/test_db.py:78-98`) — the two new
`create_subscription` tests above replace them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_db.py -q`
Expected: collection error — `ImportError: cannot import name 'update_subscription_filters' from 'core.db'`.

- [ ] **Step 3: Update `src/core/db.py`**

Replace `create_subscription` and `update_subscription_tags` with:

```python
def create_subscription(
    birthday: date,
    excluded_tags: Optional[List[str]] = None,
    excluded_categories: Optional[List[str]] = None,
) -> Dict:
    """Create a new subscription (magic-link token + ntfy topic) for a birthday.

    The two exclusion lists carry the visitor's current calendar filter forward
    into their notification preference, so filter-then-subscribe is one action.
    """
    client = get_client()
    row = {
        "token": secrets.token_urlsafe(12),
        "ntfy_topic": f"achage-{secrets.token_urlsafe(9)}",
        "birthday": birthday.isoformat(),
        "excluded_tags": list(excluded_tags or []),
        "excluded_categories": list(excluded_categories or []),
    }
    response = client.table("subscriptions").insert(row).execute()
    return response.data[0]


def update_subscription_filters(
    token: str, excluded_tags: List[str], excluded_categories: List[str]
) -> None:
    """Overwrite a subscription's stored category and tag exclusions in one write."""
    client = get_client()
    client.table("subscriptions").update(
        {
            "excluded_tags": list(excluded_tags),
            "excluded_categories": list(excluded_categories),
        }
    ).eq("token", token).execute()
```

- [ ] **Step 4: Update the two call sites in `src/app/ui.py`**

The category control lands in Task 4, so pass an empty category exclusion list for now. Change the
`core.db` import:

```python
from core.db import (
    create_subscription,
    fetch_events,
    get_config_value,
    get_subscription,
    update_subscription_filters,
)
```

At `src/app/ui.py:82-84`:

```python
                new_subscription = create_subscription(
                    birthdate, excluded_from_included(st.session_state.included_tags), []
                )
```

At `src/app/ui.py:114-118`:

```python
                update_subscription_filters(
                    subscription["token"],
                    excluded_from_included(st.session_state.included_tags),
                    [],
                )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **207 passed** (three tests replaced by three new ones, so the total is unchanged).

- [ ] **Step 6: Document the migration in `SUPABASE_SETUP.md`**

Append a new section after section 10:

````markdown
## 11. Coarse category filtering preferences

Run this in the SQL editor **before** deploying the category-filtering application code — the
app's "Update preferences" button writes this column, and the write fails if it doesn't exist.

```sql
alter table subscriptions add column if not exists excluded_categories text[] not null default '{}';
```

No backfill is needed. The `default '{}'` fills every existing row with an empty exclusion list, so
current subscribers keep receiving every match exactly as before.

The eight categories are derived from the existing tags at read time
(`core.config.TAG_CATEGORIES` → `core.matching.primary_category`), so there is nothing to migrate
on the `events`, `tags` or `event_tags` side and no re-tagging run. Changing which category a tag
belongs to is a code change that takes effect immediately, with no backfill.

Like `excluded_tags`, this column stores **exclusions**, so a category added later is visible to
existing subscribers by default. `core.matching.events_for_subscription` reads it defensively, so
if the cron job runs before this SQL is applied it falls back to tag-only filtering rather than
failing the run.
````

- [ ] **Step 7: Commit**

```bash
git add src/core/db.py src/app/ui.py tests/test_db.py SUPABASE_SETUP.md
git commit -m "feat: store per-subscription category exclusions

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Category filter UI

**Files:**
- Modify: `src/app/ui.py:63-67` (session seeding), `:75-98` (subscribe block), `:107-123` (filter expander), `:210-212` (day lookup)
- Test: manual (Streamlit UI — no unit test; the logic underneath is covered by Tasks 2 and 3)

**Interfaces:**
- Consumes: `core.config.CATEGORY_NAMES`, `core.matching.filter_events`, `core.matching.excluded_from_included`, `core.matching.included_categories_for_subscription`, `core.db.update_subscription_filters`, `core.db.create_subscription`.
- Produces: `st.session_state.included_categories` (list of category names), used by the day lookup.

- [ ] **Step 1: Seed the category selection into session state**

Extend the matching import block in `src/app/ui.py`:

```python
from core.matching import (
    events_by_age_days,
    excluded_from_included,
    filter_events,
    full_sentence,
    included_categories_for_subscription,
    included_tags_for_subscription,
)
```

Replace the seeding block (`src/app/ui.py:62-67`) with:

```python
# The filter is a session value for anonymous visitors and a saved preference
# for subscribers. Seed it once per session so a rerun doesn't clobber an
# in-progress selection.
if "included_tags" not in st.session_state:
    if subscription:
        st.session_state.included_tags = included_tags_for_subscription(subscription)
    else:
        st.session_state.included_tags = list(TAG_TAXONOMY)

if "included_categories" not in st.session_state:
    if subscription:
        st.session_state.included_categories = included_categories_for_subscription(subscription)
    else:
        st.session_state.included_categories = list(CATEGORY_NAMES)
```

- [ ] **Step 2: Rebuild the filter expander**

Replace the whole expander block (`src/app/ui.py:107-121`) with:

```python
with st.expander("Filter which events show up"):
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
    if subscription:
        if st.button("Update preferences"):
            try:
                update_subscription_filters(
                    subscription["token"],
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                )
            except Exception:
                st.error("Couldn't save your preferences — try again in a moment.")
            else:
                st.success("Saved — your notifications will follow these filters from now on.")
```

- [ ] **Step 3: Carry the categories into the subscribe call**

At the `create_subscription` call inside the "Get notified" block:

```python
                new_subscription = create_subscription(
                    birthdate,
                    excluded_from_included(st.session_state.included_tags),
                    excluded_from_included(st.session_state.included_categories, CATEGORY_NAMES),
                )
```

- [ ] **Step 4: Use the live category selection in the day lookup**

At `src/app/ui.py:210-212`:

```python
            day_matches = filter_events(
                EVENTS_BY_AGE.get(age_days, []),
                st.session_state.included_categories,
                st.session_state.included_tags,
            )
```

- [ ] **Step 5: Run the suite (nothing should break)**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **207 passed**.

- [ ] **Step 6: Verify live in the browser**

Start the app (background) and open it:

```bash
./venv/Scripts/streamlit.exe run src/app/ui.py --server.port 8517
```

Then `preview_start {url: "http://localhost:8517"}`. Check, with a birthday that produces several
matches (e.g. 1990-01-01) on a month showing multiple circled days:

1. The filter expander opens without a `StreamlitAPIException`, and "Advanced: filter by detailed tag" opens as a popover containing the 20-tag multiselect.
2. Removing "War & Conflict" from the category multiselect visibly reduces the circled days.
3. Re-adding it restores them.
4. Opening the advanced popover and removing a tag inside a kept category reduces them further.
5. `read_console_messages` shows no errors.

Capture a screenshot of the expanded filter panel as proof.

- [ ] **Step 7: Commit**

```bash
git add src/app/ui.py
git commit -m "feat: filter the calendar by coarse category with advanced tag narrowing

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Full bookmarkable subscription URL

**Files:**
- Create: `src/app/links.py`
- Modify: `src/app/ui.py:38` (drop the module-level `APP_BASE_URL`), `:86-98` (link + setup copy)
- Modify: `requirements.txt`
- Test: `tests/test_links.py` (create)

**Interfaces:**
- Consumes: `core.db.get_config_value`.
- Produces: `app.links.base_url_from(url: str) -> str`, `app.links.app_base_url() -> str`, `app.links.subscription_link(token: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_links.py`:

```python
from app.links import base_url_from


def test_strips_query_string():
    assert base_url_from("https://almanac.streamlit.app/?u=abc123") == "https://almanac.streamlit.app"


def test_strips_fragment_and_query_together():
    assert base_url_from("https://example.com/app?u=x#section") == "https://example.com/app"


def test_strips_a_trailing_slash():
    assert base_url_from("https://example.com/") == "https://example.com"


def test_leaves_a_clean_base_url_alone():
    assert base_url_from("https://example.com/app") == "https://example.com/app"


def test_keeps_a_localhost_port():
    assert base_url_from("http://localhost:8517/?u=abc") == "http://localhost:8517"


def test_empty_input_gives_empty_output():
    assert base_url_from("") == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_links.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'app.links'`.

- [ ] **Step 3: Create `src/app/links.py`**

```python
"""URL helpers for the Streamlit app.

The pure URL manipulation lives apart from the Streamlit lookups so it can be
tested without a Streamlit runtime.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import streamlit as st

from core.db import get_config_value


def base_url_from(url: str) -> str:
    """Strip query string, fragment and trailing slash from a page URL."""
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")


def app_base_url() -> str:
    """The app's own base URL: the live browser context first, the secret as fallback.

    st.context.url reports the address the visitor actually loaded, so this is
    correct in local development, on Streamlit Community Cloud, and on any
    future domain without a secret to keep in sync. APP_BASE_URL remains the
    fallback for any context where st.context is unavailable, and stays the only
    source for scripts/send_daily_notifications.py, which has no browser at all.
    """
    try:
        context_url = st.context.url
    except Exception:
        context_url = None
    return base_url_from(context_url or "") or base_url_from(
        get_config_value("APP_BASE_URL", default="")
    )


def subscription_link(token: str) -> str:
    """The full bookmarkable URL that logs a subscriber back in."""
    return f"{app_base_url()}?u={token}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_links.py -q`
Expected: **6 passed**. Then `./venv/Scripts/python.exe -m pytest -q` → **213 passed**.

- [ ] **Step 5: Use it in `src/app/ui.py`**

Delete the module-level `APP_BASE_URL = get_config_value("APP_BASE_URL", default="")` line
(`src/app/ui.py:38`) and add to the imports, below the `core.*` imports:

```python
from app.links import subscription_link
from app.styles import MASTHEAD_HTML, PAGE_CSS
```

`get_config_value` is no longer used in `ui.py` — remove it from the `core.db` import block.

Replace the success block (`src/app/ui.py:88-98`) with:

```python
                link = subscription_link(new_subscription["token"])
                st.success("Subscription created! Save this link, then subscribe to your notification topic:")
                st.code(link, language=None)
                st.markdown(
                    f"1. **Save this link.** Bookmark it or add it to your home screen — it "
                    f"remembers your birthday and your filters, and it's the only way back to "
                    f"them, so don't lose it.\n"
                    f"2. Install the [ntfy app](https://ntfy.sh) and subscribe to the topic "
                    f"`{new_subscription['ntfy_topic']}`.\n"
                    f"3. You'll get a notification whenever your age matches an event."
                )
```

- [ ] **Step 6: Pin the Streamlit floor in `requirements.txt`**

`st.context.url` arrived in Streamlit 1.45.0, and Streamlit Community Cloud installs from this file,
so the unpinned `streamlit` line must gain a floor. Change the `streamlit` line to:

```
streamlit>=1.45
```

- [ ] **Step 7: Verify live in the browser**

With the app running (see Task 4 Step 6), enter a birthday, expand "Get notified when your age
matches an event", and click "Get notified". Confirm the code block shows a complete URL —
`http://localhost:8517?u=<token>` — not a bare `?u=…`, and that pasting it into the browser loads
the app with "Welcome back — this link remembers your birthday."

*(This creates a real subscription row. That's fine — it is the same flow you'd test by hand — but
note the token so it can be deleted from the Supabase table editor afterwards if you'd rather not
keep it.)*

- [ ] **Step 8: Commit**

```bash
git add src/app/links.py src/app/ui.py tests/test_links.py requirements.txt
git commit -m "feat: make the subscription link a full bookmarkable URL

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Wikipedia lookup source module

**Files:**
- Create: `src/ingest/sources/wikipedia.py`
- Test: `tests/test_wikipedia.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `wikipedia.resolve_titles(names: List[str]) -> Dict[str, Dict]` — each value is `{"status": "found"|"missing"|"disambiguation", "title": Optional[str], "url": Optional[str], "qid": Optional[str]}`, keyed by the **requested** name.
  - `wikipedia.fetch_birth_year(qid: str) -> Optional[int]`
  - `wikipedia._get_json(url: str, params: Dict) -> Dict` — the single network seam, monkeypatched in tests.
  - `wikipedia.TITLE_BATCH_SIZE = 50`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wikipedia.py`:

```python
from ingest.sources import wikipedia


def _fake_api(monkeypatch, api_payload=None, statements_payload=None):
    """Stub the module's single network seam.

    Dispatches on URL: the MediaWiki api.php call versus the Wikidata REST
    statements call.
    """
    def fake_get_json(url, params):
        if "api.php" in url:
            return api_payload or {}
        return statements_payload or {}

    monkeypatch.setattr(wikipedia, "_get_json", fake_get_json)


def test_resolve_titles_returns_url_and_qid_for_a_plain_hit(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "pages": {
                    "736": {
                        "title": "Albert Einstein",
                        "fullurl": "https://en.wikipedia.org/wiki/Albert_Einstein",
                        "pageprops": {"wikibase_item": "Q937"},
                    }
                }
            }
        },
    )

    result = wikipedia.resolve_titles(["Albert Einstein"])

    assert result["Albert Einstein"] == {
        "status": "found",
        "title": "Albert Einstein",
        "url": "https://en.wikipedia.org/wiki/Albert_Einstein",
        "qid": "Q937",
    }


def test_resolve_titles_follows_a_redirect_back_to_the_requested_name(monkeypatch):
    # A redirected page comes back under its canonical title. Keying results by
    # the returned title would silently lose the name that was asked for.
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "redirects": [{"from": "Ada Byron", "to": "Ada Lovelace"}],
                "pages": {
                    "1": {
                        "title": "Ada Lovelace",
                        "fullurl": "https://en.wikipedia.org/wiki/Ada_Lovelace",
                        "pageprops": {"wikibase_item": "Q7259"},
                    }
                },
            }
        },
    )

    result = wikipedia.resolve_titles(["Ada Byron"])

    assert result["Ada Byron"]["status"] == "found"
    assert result["Ada Byron"]["title"] == "Ada Lovelace"


def test_resolve_titles_follows_normalization_then_redirect(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "normalized": [{"from": "ada byron", "to": "Ada byron"}],
                "redirects": [{"from": "Ada byron", "to": "Ada Lovelace"}],
                "pages": {
                    "1": {
                        "title": "Ada Lovelace",
                        "fullurl": "https://en.wikipedia.org/wiki/Ada_Lovelace",
                        "pageprops": {"wikibase_item": "Q7259"},
                    }
                },
            }
        },
    )

    assert wikipedia.resolve_titles(["ada byron"])["ada byron"]["status"] == "found"


def test_resolve_titles_flags_a_disambiguation_page(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={
            "query": {
                "pages": {
                    "20605753": {
                        "title": "John Smith",
                        "fullurl": "https://en.wikipedia.org/wiki/John_Smith",
                        "pageprops": {"wikibase_item": "Q245903", "disambiguation": ""},
                    }
                }
            }
        },
    )

    assert wikipedia.resolve_titles(["John Smith"])["John Smith"]["status"] == "disambiguation"


def test_resolve_titles_flags_a_missing_page(monkeypatch):
    _fake_api(
        monkeypatch,
        api_payload={"query": {"pages": {"-1": {"title": "Nobody At All", "missing": ""}}}},
    )

    result = wikipedia.resolve_titles(["Nobody At All"])

    assert result["Nobody At All"]["status"] == "missing"
    assert result["Nobody At All"]["url"] is None


def test_resolve_titles_reports_a_name_the_api_never_mentioned(monkeypatch):
    _fake_api(monkeypatch, api_payload={"query": {"pages": {}}})

    assert wikipedia.resolve_titles(["Ghost"])["Ghost"]["status"] == "missing"


def test_resolve_titles_batches_by_fifty(monkeypatch):
    calls = []

    def fake_get_json(url, params):
        calls.append(params["titles"].split("|"))
        return {"query": {"pages": {}}}

    monkeypatch.setattr(wikipedia, "_get_json", fake_get_json)

    names = [f"Person {i}" for i in range(120)]
    result = wikipedia.resolve_titles(names)

    assert [len(batch) for batch in calls] == [50, 50, 20]
    assert len(result) == 120


def test_fetch_birth_year_reads_the_rest_statement_shape(monkeypatch):
    _fake_api(
        monkeypatch,
        statements_payload={
            "P569": [
                {
                    "rank": "normal",
                    "property": {"id": "P569", "data_type": "time"},
                    "value": {"type": "value", "content": {"time": "+1879-03-14T00:00:00Z", "precision": 11}},
                }
            ]
        },
    )

    assert wikipedia.fetch_birth_year("Q937") == 1879


def test_fetch_birth_year_accepts_year_precision(monkeypatch):
    # Only the year is compared, so a year-precision claim is still usable here
    # (unlike wikidata.parse_birth_claim, which needs a day to compute an age).
    _fake_api(
        monkeypatch,
        statements_payload={
            "P569": [{"value": {"type": "value", "content": {"time": "+1452-01-01T00:00:00Z", "precision": 9}}}]
        },
    )

    assert wikipedia.fetch_birth_year("Q762") == 1452


def test_fetch_birth_year_returns_none_for_a_bc_date(monkeypatch):
    _fake_api(
        monkeypatch,
        statements_payload={
            "P569": [{"value": {"type": "value", "content": {"time": "-0106-01-03T00:00:00Z", "precision": 11}}}]
        },
    )

    assert wikipedia.fetch_birth_year("Q1541") is None


def test_fetch_birth_year_returns_none_when_there_is_no_claim(monkeypatch):
    _fake_api(monkeypatch, statements_payload={"P569": []})

    assert wikipedia.fetch_birth_year("Q1") is None


def test_fetch_birth_year_returns_none_for_a_novalue_statement(monkeypatch):
    _fake_api(monkeypatch, statements_payload={"P569": [{"value": {"type": "novalue"}}]})

    assert wikipedia.fetch_birth_year("Q1") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_wikipedia.py -q`
Expected: collection error — `ImportError: cannot import name 'wikipedia' from 'ingest.sources'`.

- [ ] **Step 3: Create `src/ingest/sources/wikipedia.py`**

```python
"""English Wikipedia article lookup for the people in the events corpus.

Resolves a person's name to their Wikipedia article, and exposes the birth year
Wikidata holds for that article's subject so a caller can check the article is
about the right person. The failure that matters is not a missing link but a
wrong one - "John Smith" resolving to a disambiguation page or to a different
John Smith - so every candidate is verifiable against a birth year the caller
already knows.

Batching note: title resolution takes 50 names per request, so the whole corpus
costs ~34 requests. Birth years are fetched one item at a time on purpose. The
batched alternatives were measured and rejected: `action=wbgetentities` with
`props=claims` returns roughly 300 KB per entity, and the SPARQL endpoint
answers a ten-item VALUES query with HTTP 429.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

API_URL = "https://en.wikipedia.org/w/api.php"
STATEMENTS_URL = "https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/{qid}/statements"
USER_AGENT = "achievement-age/0.1 (https://github.com/didrikfo/achievement-age) ingest script"
REQUEST_DELAY_SECONDS = 0.2
TITLE_BATCH_SIZE = 50
BIRTH_DATE_PROPERTY = "P569"

#: Redirect chains are short; the cap only exists so a cyclic chain can't hang.
MAX_REDIRECT_HOPS = 5


def _get_json(url: str, params: Dict) -> Dict:
    """The only network call in this module - monkeypatched in tests.

    Rate-limited and identified by User-Agent, per Wikimedia's API etiquette.
    """
    time.sleep(REQUEST_DELAY_SECONDS)
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.json()


def _alias_map(query: Dict) -> Dict[str, str]:
    """from -> to for every title rewrite the API applied (normalization, redirects)."""
    alias: Dict[str, str] = {}
    for entry in query.get("normalized") or []:
        alias[entry["from"]] = entry["to"]
    for entry in query.get("redirects") or []:
        alias[entry["from"]] = entry["to"]
    return alias


def _final_title(name: str, alias: Dict[str, str]) -> str:
    """Follow a requested name through normalization and redirects to its final title."""
    title = name
    for _ in range(MAX_REDIRECT_HOPS):
        next_title = alias.get(title)
        if next_title is None or next_title == title:
            break
        title = next_title
    return title


def _missing(title: Optional[str] = None) -> Dict:
    return {"status": "missing", "title": title, "url": None, "qid": None}


def _resolve_batch(names: List[str]) -> Dict[str, Dict]:
    payload = _get_json(
        API_URL,
        {
            "action": "query",
            "titles": "|".join(names),
            "redirects": 1,
            "prop": "pageprops|info",
            "inprop": "url",
            "ppprop": "wikibase_item|disambiguation",
            "format": "json",
        },
    )
    query = payload.get("query") or {}
    alias = _alias_map(query)
    pages_by_title = {
        page.get("title"): page for page in (query.get("pages") or {}).values() if page.get("title")
    }

    results: Dict[str, Dict] = {}
    for name in names:
        page = pages_by_title.get(_final_title(name, alias))
        if page is None or "missing" in page or "invalid" in page:
            results[name] = _missing(page.get("title") if page else None)
            continue
        props = page.get("pageprops") or {}
        results[name] = {
            "status": "disambiguation" if "disambiguation" in props else "found",
            "title": page.get("title"),
            "url": page.get("fullurl"),
            "qid": props.get("wikibase_item"),
        }
    return results


def resolve_titles(names: List[str]) -> Dict[str, Dict]:
    """Resolve article titles for names, keyed by the name that was requested.

    Each value is {"status", "title", "url", "qid"}, where status is one of
    "found", "missing" or "disambiguation". A name the API never mentions is
    reported as missing rather than omitted, so callers can rely on every
    requested name having an entry.
    """
    results: Dict[str, Dict] = {}
    for start in range(0, len(names), TITLE_BATCH_SIZE):
        results.update(_resolve_batch(names[start : start + TITLE_BATCH_SIZE]))
    return results


def _year_from_time(time_string: str) -> Optional[int]:
    """Year from a Wikidata time literal like "+1879-03-14T00:00:00Z".

    None for BC dates (a leading "-") and anything unparseable. Precision is
    ignored: even a year-precision claim carries the year, which is all this
    module compares.
    """
    if not time_string.startswith("+"):
        return None
    try:
        return int(time_string[1:].split("-", 1)[0])
    except ValueError:
        return None


def fetch_birth_year(qid: str) -> Optional[int]:
    """The birth year on a Wikidata item, or None if it has no usable one."""
    payload = _get_json(STATEMENTS_URL.format(qid=qid), {"property": BIRTH_DATE_PROPERTY})
    for statement in payload.get(BIRTH_DATE_PROPERTY) or []:
        content = ((statement.get("value") or {}).get("content")) or {}
        year = _year_from_time(content.get("time", "")) if isinstance(content, dict) else None
        if year is not None:
            return year
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_wikipedia.py -q`
Expected: **12 passed**.

- [ ] **Step 5: Smoke-test against the real APIs**

Run:

```bash
./venv/Scripts/python.exe -c "from ingest.sources import wikipedia; r = wikipedia.resolve_titles(['Albert Einstein', 'John Smith', 'Nobodyxyz Atall']); print(r); print(wikipedia.fetch_birth_year(r['Albert Einstein']['qid']))"
```

Expected: Einstein `found` with a `Q937` qid and a real URL, `John Smith` `disambiguation`,
`Nobodyxyz Atall` `missing`, and a final line reading `1879`.

- [ ] **Step 6: Run the whole suite and commit**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **225 passed**.

```bash
git add src/ingest/sources/wikipedia.py tests/test_wikipedia.py
git commit -m "feat: add batched Wikipedia title resolution with Wikidata birth-year lookup

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Person Wikipedia backfill script

**Files:**
- Create: `src/ingest/backfill_person_wikipedia.py`
- Modify: `SUPABASE_SETUP.md` (new section 12)
- Test: `tests/test_backfill_person_wikipedia.py` (create)

**Interfaces:**
- Consumes: `ingest.sources.wikipedia.resolve_titles`, `ingest.sources.wikipedia.fetch_birth_year`, `ingest.sources.wikidata.load_cache`, `ingest.sources.wikidata.save_cache`, `ingest.enrichment.write_review_entries`, `core.db.fetch_events`, `core.db.get_client`, `core.matching.normalize_name`.
- Produces: `birth_years_by_person(events) -> Tuple[Dict[int, int], List[int]]`, `year_matches(expected_year, found_year) -> bool`, `main()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backfill_person_wikipedia.py`:

```python
from ingest.backfill_person_wikipedia import birth_years_by_person, year_matches


def _event(person_id, event_date, age_days):
    year, month, day = event_date
    return {"person_id": person_id, "year": year, "month": month, "day": day, "age_days": age_days}


def test_birth_year_is_derived_from_an_event_date_minus_age():
    # Einstein: born 1879-03-14, special relativity paper 1905-06-30.
    events = [_event(1, (1905, 6, 30), 9605)]

    years, conflicting = birth_years_by_person(events)

    assert years == {1: 1879}
    assert conflicting == []


def test_two_events_for_one_person_agree():
    events = [_event(1, (1905, 6, 30), 9605), _event(1, (1921, 11, 9), 15581)]

    years, conflicting = birth_years_by_person(events)

    assert years == {1: 1879}
    assert conflicting == []


def test_conflicting_events_are_reported_not_averaged():
    events = [_event(1, (1905, 6, 30), 9605), _event(1, (1905, 6, 30), 100)]

    years, conflicting = birth_years_by_person(events)

    assert 1 not in years
    assert conflicting == [1]


def test_events_with_an_unusable_date_are_skipped():
    # month 0 / day 0 placeholders exist in scraped source data; date() rejects
    # them, and one bad row must not take out the whole run.
    events = [_event(1, (1905, 0, 0), 100), _event(2, (1905, 6, 30), 9605)]

    years, conflicting = birth_years_by_person(events)

    assert years == {2: 1879}
    assert 1 not in years


def test_events_without_a_person_id_are_ignored():
    events = [_event(None, (1905, 6, 30), 9605)]

    assert birth_years_by_person(events) == ({}, [])


def test_year_matches_exactly():
    assert year_matches(1879, 1879)


def test_year_matches_within_one_year():
    # A Julian/Gregorian or timezone edge can shift a January or December birth
    # date across a year boundary; that is not a wrong-person signal.
    assert year_matches(1879, 1880)
    assert year_matches(1879, 1878)


def test_year_does_not_match_a_different_person():
    assert not year_matches(1879, 1955)


def test_year_does_not_match_when_wikidata_has_none():
    assert not year_matches(1879, None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_backfill_person_wikipedia.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'ingest.backfill_person_wikipedia'`.

- [ ] **Step 3: Create `src/ingest/backfill_person_wikipedia.py`**

```python
"""One-off backfill: fill persons.wikipedia_url with verified article links.

Run after the events corpus exists (persons rows are created by the migration
and enrichment scripts):

    ./venv/Scripts/python.exe -m ingest.backfill_person_wikipedia

The failure that matters here is a *wrong* link, not a missing one. Every
candidate article is checked against the person's real birth year - derivable
from any of their events, since an event row carries both its own date and the
person's age in days at the time - and only verified links are written. Anything
rejected goes to data/tmp/person_wikipedia_review.json with a reason rather than
being guessed at.

Safe to rerun: rows that already have a URL are never fetched or overwritten
(so a hand-corrected value survives), and every lookup outcome is cached by
normalized name, so a second run makes no network requests for names already
attempted.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.db import fetch_events, get_client
from core.matching import normalize_name
from ingest.enrichment import write_review_entries
from ingest.sources import wikipedia
from ingest.sources.wikidata import load_cache, save_cache

CACHE_PATH = DATA_DIR / "wikipedia_person_cache.json"
REVIEW_PATH = DATA_DIR / "tmp" / "person_wikipedia_review.json"
PERSONS_PAGE_SIZE = 1000

#: A Julian/Gregorian or timezone edge can shift a January or December birth
#: date across a year boundary. That is not a wrong-person signal.
YEAR_TOLERANCE = 1

#: How often to persist the cache during the birth-year phase, so an interrupted
#: run keeps most of its progress.
CACHE_SAVE_EVERY = 25


def birth_years_by_person(events: List[Dict]) -> Tuple[Dict[int, int], List[int]]:
    """Map person_id -> birth year, plus the person_ids whose events disagree.

    An event's date minus the person's age in days at that event is their birth
    date. A person whose events imply two different birth dates is not
    verifiable and is reported rather than averaged away - that is a data bug
    worth seeing. Rows with an unusable date (month or day 0, as scraped source
    data sometimes has) are skipped: one bad row must not take out the run.
    """
    birth_dates: Dict[int, set] = {}
    for event in events:
        person_id = event.get("person_id")
        if person_id is None:
            continue
        try:
            event_date = date(int(event["year"]), int(event["month"]), int(event["day"]))
            birth_date = event_date - timedelta(days=int(event["age_days"]))
        except (KeyError, TypeError, ValueError):
            continue
        birth_dates.setdefault(person_id, set()).add(birth_date)

    years = {
        person_id: next(iter(dates)).year
        for person_id, dates in birth_dates.items()
        if len(dates) == 1
    }
    conflicting = [person_id for person_id, dates in birth_dates.items() if len(dates) > 1]
    return years, conflicting


def year_matches(expected_year: int, found_year: Optional[int]) -> bool:
    """Whether a Wikidata birth year confirms the person we expected."""
    return found_year is not None and abs(found_year - expected_year) <= YEAR_TOLERANCE


def fetch_persons_missing_url(client) -> List[Dict]:
    """Every persons row without a wikipedia_url, paginated past the PostgREST cap."""
    persons: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("persons")
            .select("id, name")
            .is_("wikipedia_url", "null")
            .range(start, start + PERSONS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        persons.extend(page)
        if len(page) < PERSONS_PAGE_SIZE:
            break
        start += PERSONS_PAGE_SIZE
    return persons


def main(cache_path: Path = CACHE_PATH, review_path: Path = REVIEW_PATH) -> Dict[str, int]:
    client = get_client()

    persons = fetch_persons_missing_url(client)
    birth_years, conflicting = birth_years_by_person(fetch_events())
    cache = load_cache(cache_path)
    counts: Counter = Counter()
    review: List[Dict] = []

    # Phase 1: resolve every uncached name in batches of 50.
    uncached = [person["name"] for person in persons if normalize_name(person["name"]) not in cache]
    print(f"{len(persons)} person(s) without a link; {len(uncached)} to resolve, rest cached.")
    for name, resolved in wikipedia.resolve_titles(uncached).items():
        cache[normalize_name(name)] = dict(resolved, birth_year_checked=False)
    save_cache(cache, cache_path)

    # Phase 2: verify each candidate's birth year, then write or reject.
    for index, person in enumerate(persons, start=1):
        key = normalize_name(person["name"])
        entry = cache.get(key) or {"status": "missing", "title": None, "url": None, "qid": None}

        if person["id"] in conflicting:
            counts["conflicting_local_birth_dates"] += 1
            review.append({"name": person["name"], "status": "conflicting_local_birth_dates",
                           "detail": "this person's events imply more than one birth date",
                           "candidate_url": entry.get("url")})
            continue

        expected_year = birth_years.get(person["id"])
        if expected_year is None:
            counts["no_local_birth_date"] += 1
            review.append({"name": person["name"], "status": "no_local_birth_date",
                           "detail": "no event row to derive a birth date from",
                           "candidate_url": entry.get("url")})
            continue

        if entry.get("status") != "found" or not entry.get("url"):
            counts[entry.get("status", "missing")] += 1
            review.append({"name": person["name"], "status": entry.get("status", "missing"),
                           "detail": f"title lookup returned {entry.get('status')!r}",
                           "candidate_url": entry.get("url")})
            continue

        if not entry.get("birth_year_checked"):
            entry["birth_year"] = wikipedia.fetch_birth_year(entry["qid"]) if entry.get("qid") else None
            entry["birth_year_checked"] = True
            cache[key] = entry
            if index % CACHE_SAVE_EVERY == 0:
                save_cache(cache, cache_path)

        if not year_matches(expected_year, entry.get("birth_year")):
            counts["year_mismatch"] += 1
            review.append({"name": person["name"], "status": "year_mismatch",
                           "detail": f"expected birth year {expected_year}, "
                                     f"article subject has {entry.get('birth_year')}",
                           "candidate_url": entry.get("url")})
            continue

        client.table("persons").update({"wikipedia_url": entry["url"]}).eq("id", person["id"]).execute()
        counts["verified"] += 1
        if counts["verified"] % 100 == 0:
            print(f"  written {counts['verified']} link(s)...")

    save_cache(cache, cache_path)
    write_review_entries(review, review_path)

    print(f"Done. {counts['verified']} link(s) written.")
    for status, count in sorted(counts.items()):
        if status != "verified":
            print(f"  {status}: {count}")
    if review:
        print(f"{len(review)} person(s) need a manual look - see {review_path}.")
    return dict(counts)


if __name__ == "__main__":  # pragma: no cover - manual one-off script
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_backfill_person_wikipedia.py -q`
Expected: **9 passed**.

- [ ] **Step 5: Run the whole suite**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **234 passed**.

- [ ] **Step 6: Run the backfill against the live database**

```bash
./venv/Scripts/python.exe -m ingest.backfill_person_wikipedia
```

This makes ~34 title requests plus one birth-year request per candidate, so budget roughly 10–15
minutes for 1,682 people. Expected: a "written N link(s)" line, a per-status breakdown, and a
review file. Then confirm the write landed:

```bash
PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); from core.db import get_client; c=get_client(); print(c.table('persons').select('id', count='exact').not_.is_('wikipedia_url','null').execute().count, 'persons now have a link')"
```

Expected: a count in the high hundreds to low thousands, not 0. Spot-check three rows in the
Supabase table editor — the URL must be the article about that exact person. Then read
`data/tmp/person_wikipedia_review.json` and sanity-check that the rejections look like genuine
misses (obscure names, disambiguation pages) rather than a systematic bug.

- [ ] **Step 7: Document the runbook in `SUPABASE_SETUP.md`**

Append after section 11:

````markdown
## 12. Person Wikipedia links

Fills `persons.wikipedia_url`, which the event dialog renders as a "further reading" link. No SQL
is needed — the column has existed since section 3.

```bash
./venv/Scripts/python.exe -m ingest.backfill_person_wikipedia
```

Roughly 10–15 minutes for the full corpus: article titles resolve 50 per request, but each
candidate's birth year is one small Wikidata REST call.

Every candidate article is verified against the person's real birth year, derived from their own
event rows (event date minus age in days), with a ±1 year tolerance for calendar edge cases. Only
verified links are written; disambiguation pages, missing articles, birth-year mismatches and
people whose events imply conflicting birth dates are listed in
`data/tmp/person_wikipedia_review.json` instead. Read that file after a run — the rejections should
look like obscure names and genuine ambiguity, not a systematic failure.

Safe to rerun: rows that already have a URL are skipped entirely, so hand-corrected values are
never overwritten, and every lookup outcome is cached in `data/wikipedia_person_cache.json`. To
force a re-lookup for one person, clear their `wikipedia_url` in the Supabase table editor and
delete their entry from that cache file.
````

- [ ] **Step 8: Commit**

```bash
git add src/ingest/backfill_person_wikipedia.py tests/test_backfill_person_wikipedia.py SUPABASE_SETUP.md
git commit -m "feat: backfill verified Wikipedia links for people

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Further-reading links in the event dialog

**Files:**
- Modify: `src/app/links.py`
- Modify: `src/app/ui.py:126-141` (the dialog)
- Modify: `IDEAS.md`
- Test: `tests/test_links.py`

**Interfaces:**
- Consumes: `app.links` (created in Task 5), the `persons(wikipedia_url)` join already returned by `core.db.fetch_events`.
- Produces: `app.links.further_reading_links(event: Dict) -> List[Tuple[str, str]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_links.py`:

```python
from app.links import base_url_from, further_reading_links


def test_further_reading_links_uses_the_persons_join():
    event = {
        "name": "Ada Lovelace",
        "persons": {"wikipedia_url": "https://en.wikipedia.org/wiki/Ada_Lovelace"},
    }
    assert further_reading_links(event) == [
        ("Ada Lovelace", "https://en.wikipedia.org/wiki/Ada_Lovelace")
    ]


def test_further_reading_links_is_empty_without_a_link():
    assert further_reading_links({"name": "Someone", "persons": {"wikipedia_url": None}}) == []


def test_further_reading_links_handles_an_unjoined_person():
    # fetch_events returns persons: None for an event with no person row.
    assert further_reading_links({"name": "Someone", "persons": None}) == []
    assert further_reading_links({"name": "Someone"}) == []
```

(Replace the existing `from app.links import base_url_from` line with the combined import above.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_links.py -q`
Expected: collection error — `ImportError: cannot import name 'further_reading_links' from 'app.links'`.

- [ ] **Step 3: Add `further_reading_links` to `src/app/links.py`**

Add the typing import it needs, above the `urllib.parse` import:

```python
from typing import Dict, List, Tuple
```

Then append the function:

```python
def further_reading_links(event: Dict) -> List[Tuple[str, str]]:
    """(label, url) pairs for an event's further-reading line.

    A list rather than a single value because event-level Wikipedia links are
    planned next: when they arrive this grows one entry and the display does not
    change shape. Labels name their target so a reader knows what they'd open.
    """
    person = event.get("persons") or {}
    url = person.get("wikipedia_url")
    return [(event["name"], url)] if url else []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_links.py -q`
Expected: **9 passed**.

- [ ] **Step 5: Render it in the dialog**

Add `further_reading_links` to the `app.links` import in `src/app/ui.py`:

```python
from app.links import further_reading_links, subscription_link
```

Replace the tail of `show_event_dialog` (`src/app/ui.py:133-141`) with:

```python
    for event in matches:
        st.markdown(f"- {full_sentence(event)}")
        description = event.get("detailed_description") or event.get("text")
        if description:
            st.caption(description)
        links = further_reading_links(event)
        if links:
            joined = " · ".join(f"[{label}]({url})" for label, url in links)
            st.caption(f"Further reading on Wikipedia: {joined}")
```

- [ ] **Step 6: Verify live in the browser**

With the backfill from Task 7 already run, restart the app, pick a birthday with matches, and open
a circled day. Expected: under a matched event, a small line reading
`Further reading on Wikipedia: <person name>` whose link opens that person's article. Events whose
person has no link show no line at all — not an empty label. Screenshot the dialog as proof.

- [ ] **Step 7: Refresh the stale `IDEAS.md` entry**

Replace the tag bullet (the one beginning "Add support for event tags") with:

```markdown
- ~~Event tags and a filtering system~~ **Done.** Events carry 1–3 fine tags from a fixed
  20-name taxonomy, grouped into 8 coarse categories (`core.config.TAG_CATEGORIES`). The calendar
  filters on categories by default, with the fine tags behind an "Advanced" popover, and each
  subscriber's choice is carried into their daily notifications. Per-tag indicator colors were
  considered and dropped — a single red circle stays. Specs:
  `docs/superpowers/specs/2026-08-10-tag-filtering-design.md` and
  `docs/superpowers/specs/2026-08-11-wikipedia-links-and-tag-hierarchy-design.md`.
- Add Wikipedia links to *events* (not just the people), shown next to the person's link in the
  event dialog's "further reading" line. `app.links.further_reading_links` already returns a list
  for exactly this.
```

- [ ] **Step 8: Run the whole suite and commit**

Run: `./venv/Scripts/python.exe -m pytest -q`
Expected: **237 passed**.

```bash
git add src/app/links.py src/app/ui.py tests/test_links.py IDEAS.md
git commit -m "feat: show a further-reading Wikipedia link in the event dialog

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Rollout

1. Apply the `excluded_categories` SQL from `SUPABASE_SETUP.md` section 11 **before** deploying (Task 3 documents it; the "Update preferences" button fails without it).
2. Deploy the application code (Tasks 1–5, 8).
3. Run the Wikipedia backfill (Task 7) — independent of the rest; links simply start appearing.
4. Set `APP_BASE_URL` on Streamlit Community Cloud if it isn't set. It is now only a fallback for the app, but `scripts/send_daily_notifications.py` still needs it for the notification "Click" URL.
