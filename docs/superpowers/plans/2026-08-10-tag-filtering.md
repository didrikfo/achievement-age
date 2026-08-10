# Tag Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the tags the ingest pipeline already writes, so users can filter the calendar by tag and subscribers can limit which tags trigger a push notification.

**Architecture:** All filtering logic lives as pure functions in `core/matching.py`, consumed by both the Streamlit app and the daily cron script so the two can never disagree. The UI presents inclusion (all tags checked by default); the database stores the complement (`subscriptions.excluded_tags`) so new taxonomy tags default to visible for existing subscribers.

**Tech Stack:** Python 3.11, Streamlit, Supabase (PostgREST via `supabase-py`), pytest.

## Global Constraints

- **Untagged events always survive filtering.** An event with `tags == []` is "not yet categorized," not "excluded." Never hide one.
- **Tagged events survive on ANY single surviving tag (OR, not AND).** An event tagged `["military", "politics"]` is kept when only `military` is excluded.
- **The database stores exclusions, never inclusions.** Column is `subscriptions.excluded_tags text[] not null default '{}'`.
- **Read the preference defensively:** `subscription.get("excluded_tags") or []`. A missing column must degrade to "no filtering," never raise.
- **Helper output is ordered by `TAG_TAXONOMY`**, not set-iteration order, so stored values are stable and diffable.
- **The calendar indicator stays a single red circle.** Per-tag colors are explicitly out of scope; `tags.color` stays unused.
- Existing test conventions: `core` tests are plain function tests; `core.db` tests patch `core.db.get_client` with a `MagicMock` chain.

---

### Task 1: Move `TAG_TAXONOMY` to `core.config`

`core.matching` needs the taxonomy, but it currently lives in `ingest.enrichment` — a core module importing from `ingest` would invert the dependency direction. `core/config.py` is a plain constants module that both packages already import.

**Files:**
- Modify: `src/core/config.py`
- Modify: `src/ingest/enrichment.py:23-27`
- Test: `tests/test_enrichment.py` (existing, must keep passing unchanged)

**Interfaces:**
- Consumes: nothing
- Produces: `core.config.TAG_TAXONOMY: List[str]` — the 20 tag names. Re-exported as `ingest.enrichment.TAG_TAXONOMY` so every existing import keeps working.

- [ ] **Step 1: Move the constant into `core/config.py`**

Add to `src/core/config.py`, after the `DATA_DIR` definition:

```python
#: The fixed tag taxonomy. Lives here rather than in ingest.enrichment because
#: core.matching needs it for filtering, and a core module must not import from
#: ingest. ingest.enrichment re-exports it, so existing imports still work.
TAG_TAXONOMY = [
    "military", "politics", "science", "technology", "exploration", "space", "arts", "music",
    "film", "sports", "religion", "royalty", "economics", "law", "disaster", "health", "social",
    "education", "philosophy", "engineering",
]
```

Update the `__all__` line at the bottom of the file to:

```python
__all__ = ["DATA_DIR", "PROJECT_ROOT", "TAG_TAXONOMY"]
```

- [ ] **Step 2: Re-export from `ingest.enrichment`**

In `src/ingest/enrichment.py`, delete the `TAG_TAXONOMY = [...]` block at lines 23-27 and change the existing `core.config` import (line 16) to:

```python
from core.config import DATA_DIR, TAG_TAXONOMY
```

Do NOT remove `TAG_TAXONOMY` from the module's namespace — the plain import above is what re-exports it. `tests/test_enrichment.py:3` imports it from here and must keep working.

- [ ] **Step 3: Run the full suite to verify the move broke nothing**

Run: `pytest -q`
Expected: PASS, same count as before the change. This is a pure move — no behavior changes.

- [ ] **Step 4: Commit**

```bash
git add src/core/config.py src/ingest/enrichment.py
git commit -m "refactor: move TAG_TAXONOMY to core.config, re-export from enrichment"
```

---

### Task 2: Inclusion/exclusion inversion helpers

**Files:**
- Modify: `src/core/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `core.config.TAG_TAXONOMY` (Task 1)
- Produces:
  - `included_from_excluded(excluded_tags: Collection[str]) -> List[str]`
  - `excluded_from_included(included_tags: Collection[str]) -> List[str]`

  Both return a new list ordered by `TAG_TAXONOMY`. Both ignore names not in the taxonomy.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_matching.py`:

```python
from core.config import TAG_TAXONOMY
from core.matching import excluded_from_included, included_from_excluded


def test_no_exclusions_includes_the_whole_taxonomy():
    assert included_from_excluded([]) == TAG_TAXONOMY


def test_included_from_excluded_removes_the_excluded_names():
    result = included_from_excluded(["military", "sports"])
    assert "military" not in result
    assert "sports" not in result
    assert "science" in result
    assert len(result) == len(TAG_TAXONOMY) - 2


def test_excluded_from_included_is_the_complement():
    assert excluded_from_included(TAG_TAXONOMY) == []
    assert excluded_from_included([]) == TAG_TAXONOMY


def test_inversion_round_trips_a_ui_selection():
    selection = ["science", "space", "technology"]
    stored = excluded_from_included(selection)
    assert sorted(included_from_excluded(stored)) == sorted(selection)


def test_helpers_order_output_by_taxonomy_not_input_order():
    # "space" comes after "science" in TAG_TAXONOMY; input order must not leak through.
    assert included_from_excluded(
        [tag for tag in TAG_TAXONOMY if tag not in {"space", "science"}]
    ) == ["science", "space"]


def test_unknown_names_are_ignored():
    assert included_from_excluded(["not-a-real-tag"]) == TAG_TAXONOMY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_matching.py -q`
Expected: FAIL with `ImportError: cannot import name 'excluded_from_included' from 'core.matching'`

- [ ] **Step 3: Implement the helpers**

Add to `src/core/matching.py`. Extend the existing `typing` import to include `Collection`, and add the config import near the top:

```python
from core.config import TAG_TAXONOMY


def included_from_excluded(excluded_tags: Collection[str]) -> List[str]:
    """Return the taxonomy tags a subscriber should see, given what they excluded.

    Ordered by TAG_TAXONOMY so the result is stable regardless of input order.
    """
    excluded = set(excluded_tags or ())
    return [tag for tag in TAG_TAXONOMY if tag not in excluded]


def excluded_from_included(included_tags: Collection[str]) -> List[str]:
    """Return what to store for a UI selection: the taxonomy minus that selection.

    Exclusions are stored rather than inclusions so a tag added to TAG_TAXONOMY
    later is visible to existing subscribers by default instead of silently
    hidden.
    """
    included = set(included_tags or ())
    return [tag for tag in TAG_TAXONOMY if tag not in included]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_matching.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/matching.py tests/test_matching.py
git commit -m "feat: add tag inclusion/exclusion inversion helpers"
```

---

### Task 3: `filter_events_by_tags` and `events_for_subscription`

**Files:**
- Modify: `src/core/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `included_from_excluded` (Task 2)
- Produces:
  - `filter_events_by_tags(events: Iterable[Dict], included_tags: Collection[str]) -> List[Dict]` — used by the Streamlit calendar with the live multiselect value.
  - `events_for_subscription(events: Iterable[Dict], subscription: Dict) -> List[Dict]` — used by the cron script; reads the stored preference defensively.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_matching.py`:

```python
from core.matching import events_for_subscription, filter_events_by_tags


def _tagged(tags):
    return {"name": "Someone", "age_days": 1, "event_phrase": "", "tags": tags}


def test_untagged_event_survives_every_filter():
    untagged = _tagged([])
    assert filter_events_by_tags([untagged], ["science"]) == [untagged]
    assert filter_events_by_tags([untagged], []) == [untagged]


def test_event_with_all_tags_excluded_is_dropped():
    assert filter_events_by_tags([_tagged(["military"])], ["science"]) == []


def test_event_survives_on_any_single_surviving_tag():
    # Only "politics" is included; the event keeps it despite "military" being out.
    event = _tagged(["military", "politics"])
    assert filter_events_by_tags([event], ["politics"]) == [event]


def test_empty_inclusion_drops_tagged_but_keeps_untagged():
    untagged = _tagged([])
    tagged = _tagged(["science"])
    assert filter_events_by_tags([tagged, untagged], []) == [untagged]


def test_missing_tags_key_is_treated_as_untagged():
    # An event dict from a code path that never set "tags" must not raise.
    event = {"name": "Someone", "age_days": 1, "event_phrase": ""}
    assert filter_events_by_tags([event], ["science"]) == [event]


def test_events_for_subscription_applies_stored_exclusions():
    science = _tagged(["science"])
    military = _tagged(["military"])
    subscription = {"excluded_tags": ["military"]}
    assert events_for_subscription([science, military], subscription) == [science]


def test_events_for_subscription_survives_a_missing_column():
    # Before the alter-table lands, subscription rows have no excluded_tags key
    # at all. That must mean "no filtering", not a KeyError that kills the cron.
    science = _tagged(["science"])
    military = _tagged(["military"])
    assert events_for_subscription([science, military], {}) == [science, military]


def test_events_for_subscription_treats_null_column_as_no_filtering():
    events = [_tagged(["science"])]
    assert events_for_subscription(events, {"excluded_tags": None}) == events
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_matching.py -q`
Expected: FAIL with `ImportError: cannot import name 'filter_events_by_tags' from 'core.matching'`

- [ ] **Step 3: Implement both functions**

Add to `src/core/matching.py`:

```python
def filter_events_by_tags(events: Iterable[Dict], included_tags: Collection[str]) -> List[Dict]:
    """Keep events that are untagged, or carry at least one tag in included_tags.

    Two deliberately permissive rules:

    - An untagged event always survives. The corpus is tagged by a manually-run
      backfill, so untagged rows are a normal transient state - hiding them would
      drop real matches because of backfill lag rather than user intent.
    - A tagged event survives on any single surviving tag, not all of them, so
      unchecking one box can't remove events the user never asked to hide.
    """
    included = set(included_tags or ())
    kept: List[Dict] = []
    for event in events:
        tags = event.get("tags") or []
        if not tags or any(tag in included for tag in tags):
            kept.append(event)
    return kept


def events_for_subscription(events: Iterable[Dict], subscription: Dict) -> List[Dict]:
    """Filter events by a subscription's stored tag preference.

    Reads excluded_tags defensively: if the column hasn't been added to the
    database yet, every subscriber's row is missing the key, and raising here
    would kill the whole daily notification run rather than just one subscriber.
    Absent or null means no filtering, which is the pre-feature behavior.
    """
    excluded = subscription.get("excluded_tags") or []
    return filter_events_by_tags(events, included_from_excluded(excluded))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_matching.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/matching.py tests/test_matching.py
git commit -m "feat: add tag filtering for events and subscriptions"
```

---

### Task 4: `fetch_events()` returns a flat `tags` list

**Files:**
- Modify: `src/core/db.py:59-79`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: every dict from `fetch_events()` carries `event["tags"]: List[str]` — always a list, `[]` when the event has no tags, never `None`. The nested `event_tags` key is removed from the returned dicts.

**Note:** `tests/test_db.py:17` asserts the exact select string and WILL fail until updated in Step 1. That is expected — it is the existing test being brought in line, not a regression.

- [ ] **Step 1: Write the failing tests**

In `tests/test_db.py`, update the select assertion in `test_fetch_events_selects_with_person_join` to the new string:

```python
    mock_client.table.return_value.select.assert_called_with(
        "*, persons(wikipedia_url), event_tags(tags(name))"
    )
```

Then add these tests:

```python
def test_fetch_events_flattens_nested_tag_rows():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.range.return_value.execute.return_value.data = [
        {
            "id": 1,
            "name": "Ada Lovelace",
            "persons": None,
            "event_tags": [{"tags": {"name": "science"}}, {"tags": {"name": "technology"}}],
        },
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    assert result[0]["tags"] == ["science", "technology"]
    # The nested join shape is not left behind for callers to trip over.
    assert "event_tags" not in result[0]


def test_fetch_events_gives_untagged_events_an_empty_list():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.range.return_value.execute.return_value.data = [
        {"id": 1, "name": "No Tags", "persons": None, "event_tags": []},
        {"id": 2, "name": "Null Tags", "persons": None, "event_tags": None},
    ]

    with patch("core.db.get_client", return_value=mock_client):
        result = fetch_events()

    assert result[0]["tags"] == []
    assert result[1]["tags"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -q`
Expected: FAIL — the select-string assertion fails, and both new tests fail with `KeyError: 'tags'`.

- [ ] **Step 3: Implement the join and flattening**

In `src/core/db.py`, replace the body of `fetch_events()` (lines 64-79) with:

```python
    client = get_client()
    events: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("*, persons(wikipedia_url), event_tags(tags(name))")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        events.extend(_flatten_tags(event) for event in page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return events
```

Add this helper directly above `fetch_events`:

```python
def _flatten_tags(event: Dict) -> Dict:
    """Collapse the nested event_tags(tags(name)) join into a flat list of names.

    PostgREST returns [{"tags": {"name": "science"}}, ...]; callers just want
    ["science", ...]. Always produces a list - never None - so no downstream
    code needs a None check.
    """
    tag_rows = event.pop("event_tags", None) or []
    event["tags"] = [row["tags"]["name"] for row in tag_rows if row.get("tags")]
    return event
```

Also update the `fetch_events` docstring's first line to mention tags:

```python
    """Return every row from the events table, joined with person data and tag names.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/db.py tests/test_db.py
git commit -m "feat: join tags into fetch_events and flatten to a name list"
```

---

### Task 5: Subscriptions carry a tag preference

**Files:**
- Modify: `src/core/db.py:89-98`
- Modify: `SUPABASE_SETUP.md` (new section 10, after the section 9 that ends the file)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `create_subscription(birthday: date, excluded_tags: Optional[List[str]] = None) -> Dict` — the new parameter is optional and defaults to `[]`, so existing callers are unaffected.
  - `update_subscription_tags(token: str, excluded_tags: List[str]) -> None`

- [ ] **Step 1: Write the failing tests**

In `tests/test_db.py`, first extend the imports at the top of the file:

```python
from datetime import date
from unittest.mock import MagicMock, patch

from core.db import create_subscription, fetch_events, update_subscription_tags
```

Then append these tests:

```python
def test_create_subscription_defaults_to_no_exclusions():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1))

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == []
    assert inserted["birthday"] == "2000-01-01"


def test_create_subscription_stores_the_given_exclusions():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [{"token": "abc"}]

    with patch("core.db.get_client", return_value=mock_client):
        create_subscription(date(2000, 1, 1), ["military", "sports"])

    inserted = mock_client.table.return_value.insert.call_args.args[0]
    assert inserted["excluded_tags"] == ["military", "sports"]


def test_update_subscription_tags_targets_the_right_token():
    mock_client = MagicMock()

    with patch("core.db.get_client", return_value=mock_client):
        update_subscription_tags("tok123", ["disaster"])

    mock_client.table.assert_called_with("subscriptions")
    mock_client.table.return_value.update.assert_called_with({"excluded_tags": ["disaster"]})
    mock_client.table.return_value.update.return_value.eq.assert_called_with("token", "tok123")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -q`
Expected: FAIL with `ImportError: cannot import name 'update_subscription_tags' from 'core.db'`

- [ ] **Step 3: Implement the two functions**

In `src/core/db.py`, replace `create_subscription` with:

```python
def create_subscription(birthday: date, excluded_tags: Optional[List[str]] = None) -> Dict:
    """Create a new subscription (magic-link token + ntfy topic) for a birthday.

    excluded_tags carries the visitor's current calendar filter forward into
    their notification preference, so filter-then-subscribe is one action.
    """
    client = get_client()
    row = {
        "token": secrets.token_urlsafe(12),
        "ntfy_topic": f"achage-{secrets.token_urlsafe(9)}",
        "birthday": birthday.isoformat(),
        "excluded_tags": list(excluded_tags or []),
    }
    response = client.table("subscriptions").insert(row).execute()
    return response.data[0]


def update_subscription_tags(token: str, excluded_tags: List[str]) -> None:
    """Overwrite a subscription's stored tag exclusions."""
    client = get_client()
    client.table("subscriptions").update({"excluded_tags": list(excluded_tags)}).eq(
        "token", token
    ).execute()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -q`
Expected: PASS

- [ ] **Step 5: Document the schema change**

Append a new section to the end of `SUPABASE_SETUP.md`:

````markdown
## 10. Tag filtering preferences

Run this in the SQL editor **before** deploying the tag-filtering application code — the app's
"Update preferences" button writes to this column, and the write fails if it doesn't exist.

```sql
alter table subscriptions add column excluded_tags text[] not null default '{}';
```

No backfill is needed. The `default '{}'` fills every existing row with an empty exclusion list,
so current subscribers keep receiving every match exactly as before.

The column stores **exclusions**, not inclusions: the app's filter UI shows all tags checked by
default and stores the complement of the selection. That way a tag added to `TAG_TAXONOMY` later
is visible to existing subscribers by default, rather than silently hidden because it wasn't in
an inclusion list written before the tag existed.

`scripts/send_daily_notifications.py` reads this column defensively (`.get("excluded_tags") or []`),
so if the cron job runs before this SQL is applied it falls back to no filtering rather than
failing the entire run.
````

- [ ] **Step 6: Commit**

```bash
git add src/core/db.py tests/test_db.py SUPABASE_SETUP.md
git commit -m "feat: store per-subscription tag exclusions"
```

---

### Task 6: Filter the daily notifications

**Files:**
- Modify: `scripts/send_daily_notifications.py:53-70`

**Interfaces:**
- Consumes: `core.matching.events_for_subscription` (Task 3)
- Produces: nothing consumed by later tasks

**Why the filter goes on the combined list:** `MATCHERS` (line 38) exists so future non-database matchers (the mathematical-anniversary idea in `IDEAS.md`) can be added without restructuring. Those would return untagged results, which the untagged-always-survives rule passes through. Filtering inside `_make_db_event_matcher` instead would bury the rule where a future matcher wouldn't inherit it.

- [ ] **Step 1: Add the filtering step**

In `scripts/send_daily_notifications.py`, extend the import on line 22:

```python
from core.matching import events_by_age_days, events_for_subscription, full_sentence
```

Then in `main()`, replace the match-gathering block (lines 62-67) with:

```python
        matches: List[Dict] = []
        for matcher in MATCHERS:
            matches.extend(matcher(age_days))
        matches = events_for_subscription(matches, subscription)

        for event in matches:
            _send_ntfy_notification(subscription["ntfy_topic"], event, subscription["token"])
            notified += 1
```

- [ ] **Step 2: Update the module docstring**

Add a sentence to the docstring at the top of the file, after the existing first paragraph:

```
Each subscriber's matches are filtered against their stored tag preference
(subscriptions.excluded_tags) before sending, so a subscriber only hears about
the categories they kept.
```

- [ ] **Step 3: Verify the script still imports and the suite passes**

Run: `pytest -q`
Expected: PASS

The script itself can't be imported in a test — `MATCHERS` is built at module scope and calls `fetch_events()`, which needs live Supabase credentials. That's why the filtering logic lives in `core.matching.events_for_subscription`, which Task 3 tested directly, including the missing-column path.

- [ ] **Step 4: Commit**

```bash
git add scripts/send_daily_notifications.py
git commit -m "feat: filter daily notifications by subscriber tag preference"
```

---

### Task 7: Calendar filter UI

**Files:**
- Modify: `src/app/ui.py`
- Modify: `README.md:11-22`

**Interfaces:**
- Consumes: `core.config.TAG_TAXONOMY` (Task 1), `core.matching.filter_events_by_tags` / `included_from_excluded` / `excluded_from_included` (Tasks 2-3), `core.db.create_subscription` / `update_subscription_tags` (Task 5)
- Produces: nothing consumed by later tasks

**No automated tests for this task.** The existing suite doesn't test Streamlit wiring, and that convention is correct — all the branching logic was pulled into `core.matching` in Tasks 2-3 precisely so it could be tested without a Streamlit context. This task is verified by running the app.

- [ ] **Step 1: Extend the imports**

In `src/app/ui.py`, update the import block (lines 19-23):

```python
from core.age import age_breakdown
from core.config import TAG_TAXONOMY
from core.db import (
    create_subscription,
    fetch_events,
    get_config_value,
    get_subscription,
    update_subscription_tags,
)
from core.matching import (
    events_by_age_days,
    excluded_from_included,
    filter_events_by_tags,
    full_sentence,
    included_from_excluded,
)
```

- [ ] **Step 2: Seed the filter state right after identity resolves**

Insert this immediately after `subscription = get_subscription(token) if token else None` (line 45), **before** the `if subscription:` block on line 47.

It has to go here, not next to the expander: the "Get notified" button at line 58 reads `st.session_state.included_tags`, and that button's code runs earlier in the script than the expander added in Step 3.

```python
# The filter is a session value for anonymous visitors and a saved preference
# for subscribers. Seed it once per session so a rerun doesn't clobber an
# in-progress selection.
if "included_tags" not in st.session_state:
    if subscription:
        st.session_state.included_tags = included_from_excluded(
            subscription.get("excluded_tags") or []
        )
    else:
        st.session_state.included_tags = list(TAG_TAXONOMY)
```

- [ ] **Step 3: Add the filter expander**

Insert this immediately after the age display block (after line 75, before the `st.caption` on line 77):

```python
with st.expander("Filter which events show up"):
    st.session_state.included_tags = st.multiselect(
        "Show events tagged:",
        options=TAG_TAXONOMY,
        default=st.session_state.included_tags,
    )
    st.caption(
        "Events that haven't been tagged yet always show up, whatever you pick here."
    )
    if subscription:
        if st.button("Update preferences"):
            update_subscription_tags(
                subscription["token"], excluded_from_included(st.session_state.included_tags)
            )
            st.success("Saved — your notifications will follow these tags from now on.")
```

- [ ] **Step 4: Carry the filter into new subscriptions**

In the "Get notified" expander, change the `create_subscription` call (line 59) to pass the current selection:

```python
        if st.button("Get notified"):
            new_subscription = create_subscription(
                birthdate, excluded_from_included(st.session_state.included_tags)
            )
```

- [ ] **Step 5: Apply the filter in the calendar loop**

In the day loop, replace the `day_matches` assignment (line 164) with:

```python
            day_matches = filter_events_by_tags(
                EVENTS_BY_AGE.get(age_days, []), st.session_state.included_tags
            )
```

Filtering happens here, on the small per-day list — never by rebuilding `EVENTS_BY_AGE`. That index comes from a `@st.cache_data`-wrapped load shared across users, so it must stay filter-independent.

- [ ] **Step 6: Update the on-screen copy**

Replace the caption at line 77:

```python
st.caption("A red circle marks a day that matches a historical event — click it for details. A filled black date marks today. Use the filter above to narrow which events count.")
```

And in the post-subscription instructions (lines 63-68), change the first numbered item to mention preferences:

```python
                f"1. **Bookmark or add this page to your home screen** — it remembers your "
                f"birthday and your tag filter, and it's the only way back to these "
                f"preferences, so don't lose it.\n"
```

- [ ] **Step 7: Run the app and verify by hand**

Run: `streamlit run src/app/ui.py`

Check each of these:
1. The "Filter which events show up" expander is present, collapsed, and lists all 20 tags with every one selected.
2. Unchecking a common tag (e.g. `military`) removes red indicators from some days in a month that had them.
3. Clicking a still-red day opens the dialog and shows only events that survived the filter.
4. Unchecking every tag leaves only days whose events are untagged.
5. Clicking "Get notified" creates a subscription; opening the resulting `?u=` link shows the expander with the same tags still selected.
6. On the `?u=` link, unchecking a tag and clicking "Update preferences" shows the success message; reloading the link keeps that tag unchecked.

- [ ] **Step 8: Update the README feature list**

In `README.md`, add a bullet to the "What it does" list after the "Event detail" bullet:

```markdown
- **Tag filtering** — every event is tagged (science, military, arts, …); filter the calendar down
  to the categories you care about. If you're subscribed, your filter is saved to your private link
  and also decides which matches are worth a notification.
```

- [ ] **Step 9: Commit**

```bash
git add src/app/ui.py README.md
git commit -m "feat: add tag filter to the calendar and subscription flow"
```

---

## Deployment

The `alter table` from Task 5 Step 5 must be run in the Supabase SQL editor **before** the application code is deployed. The defensive read in Task 6 keeps the cron job alive if the order slips, but Task 7's "Update preferences" button writes to the column directly and would fail against a database missing it.
