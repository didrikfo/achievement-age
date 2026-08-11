# Tag filtering: surfacing the tags the pipeline already writes

## Context

The ingest pipeline has assigned tags to events since the LLM-enrichment work. `tags` and
`event_tags` exist in Supabase, seeded with a fixed 20-name taxonomy and per-tag hex colors
(`SUPABASE_SETUP.md` section 4). `ingest.enrichment.TAG_TAXONOMY` is the canonical list,
`validate_tags` caps each event at 3 tags, and `build_tag_rows` writes them to `event_tags`.

None of that reaches the user. `core.db.fetch_events()` selects `"*, persons(wikipedia_url)"` — no
tag join — and `src/app/ui.py` contains no tag or filter code at all. Every event's tags are
write-only data.

This spec wires that data through to two places: a filter control in the calendar UI, and the daily
notification job, so a subscriber can stop being pushed about categories they don't care about.

### Scope note on IDEAS.md

The IDEAS.md entry for this work also floats coloring the calendar indicator per tag, and hedges
that it "might get too busy with lots of tags." That hedge is accepted: the indicator stays a single
red circle. Filtering already solves the underlying problem (too much undifferentiated noise) without
adding 20 colors to a month grid. The seeded `tags.color` values stay unused for now.

## What gets built

### 1. Schema: a per-subscription tag preference

```sql
alter table subscriptions add column excluded_tags text[] not null default '{}';
```

No backfill. `default '{}'` means every existing subscriber keeps receiving every match, which is
exactly today's behavior.

**Stored as exclusions, not inclusions.** The UI presents inclusion (all tags checked by default,
uncheck to hide), but the database stores the complement. This matters when the taxonomy grows: a
new tag added to `TAG_TAXONOMY` is automatically visible to everyone, because it appears in nobody's
`excluded_tags`. Storing inclusions would silently hide every future tag from every existing
subscriber.

The inversion lives in `core.matching` as a pair of helpers, not inline in the UI — both the app and
the cron script need the exclusions-to-inclusions direction, and keeping them out of `ui.py` makes
them testable without a Streamlit context:

```python
def included_from_excluded(excluded_tags: Collection[str]) -> List[str]:
    """TAG_TAXONOMY minus excluded_tags — what a subscriber should see."""

def excluded_from_included(included_tags: Collection[str]) -> List[str]:
    """TAG_TAXONOMY minus included_tags — what to store for a UI selection."""
```

Both order their output by `TAG_TAXONOMY` so stored values are stable and diffable rather than
set-iteration order. `TAG_TAXONOMY` currently lives in `ingest.enrichment`; importing it from
`core.matching` would point a core module at the ingest package, so it moves to `core.config`
(which both packages may import) and `ingest.enrichment` re-exports it to keep existing imports
working.

### 2. `fetch_events()` returns tags

`core.db.fetch_events()` extends its select to pull the join through:

```python
.select("*, persons(wikipedia_url), event_tags(tags(name))")
```

and flattens the nested result so each event dict carries a plain list:

```python
event["tags"] = ["science", "exploration"]
```

An event with no `event_tags` rows gets `[]`, never `None` — downstream code should never need a
None check. Pagination (`EVENTS_PAGE_SIZE`) is unchanged; flattening happens per page as rows are
accumulated.

### 3. `filter_events_by_tags` in `core.matching`

One shared function, used by both the app and the cron script so the two can never drift:

```python
def filter_events_by_tags(events: Iterable[Dict], included_tags: Collection[str]) -> List[Dict]:
    """Keep events that are untagged, or carry at least one tag in included_tags."""
```

Two rules, both deliberately permissive:

**Untagged events always survive.** An event with `tags == []` is "not yet categorized," not
"belongs to no category the user wants." The corpus is tagged by a manually-run backfill, so
untagged rows are a normal transient state, and hiding them would silently drop real matches
because of backfill lag rather than user intent.

**Tagged events survive on any single surviving tag (OR, not AND).** An event tagged
`["military", "politics"]` still appears for a user who excluded only `military`. Dropping it would
mean one unchecked box removes events the user never asked to hide.

The function is pure and takes plain dicts — no Supabase, no Streamlit — so it is directly testable.

### 4. Streamlit UI

**The control.** A collapsed `st.expander` above the calendar, matching the existing "Get notified
when your age matches an event" expander pattern rather than adding permanent visual weight to a
lean page. Inside: one `st.multiselect` over all 20 taxonomy tags, labeled "Show events tagged:",
defaulting to all selected, with a caption noting that untagged events always appear.

**Initial state resolves by identity**, mirroring how `birthdate` already resolves at `ui.py:44-51`:

| Visitor | Multiselect starts at | Save affordance |
|---|---|---|
| Returning subscriber (`?u=` resolves) | `TAG_TAXONOMY - subscription["excluded_tags"]` | "Update preferences" button writes back to their row, confirms with `st.success` |
| Anonymous (no token) | All tags selected, held in `st.session_state` | None — nothing to save to yet |

Saving is an explicit button, not auto-save on widget change. The selection controls what a user
gets pushed on future days, so a stray click should not silently rewrite it.

**Subscribing carries the filter forward.** When an anonymous visitor clicks "Get notified", the
current selection is inverted and passed into `create_subscription(birthday, excluded_tags)`, so
filter-then-subscribe is one continuous action rather than a thing to redo afterward.

**Calendar rendering.** In the day loop (`ui.py:155-187`), `day_matches` is passed through
`filter_events_by_tags` before the `if day_matches` check. A day whose events are all filtered out
renders as a plain cell with no red indicator, and the dialog receives the same filtered list, so
what the indicator promises and what the dialog shows always agree.

**Filtering happens after the index lookup, never by rebuilding the index.** `EVENTS_BY_AGE` is
built at module scope from a `@st.cache_data`-wrapped load (`ui.py:28-33`). That cached object is
shared, so it must stay filter-independent; per-user filtering applies to the small per-day list
that comes back out of it.

**Copy changes.** The caption at `ui.py:77` gains a note about filtering. The post-subscription
instructions at `ui.py:63-68` must state that the bookmarked link now carries notification
preferences as well as the birthday — that link is what this app has instead of a login, and its
scope has grown.

### 5. Notification pipeline

`scripts/send_daily_notifications.py` gains one step in its per-subscriber loop: after gathering
`matches` from `MATCHERS`, filter them through `filter_events_by_tags` before sending.

Filtering applies to the **combined** match list rather than inside `_make_db_event_matcher`. The
`MATCHERS` list exists so future non-database matchers (the mathematical-anniversary idea in
IDEAS.md) can be added without restructuring; those would return untagged results, which the
untagged-always-survives rule passes through untouched. Filtering inside the DB matcher would
instead bury the rule where a future matcher wouldn't inherit it.

**The preference is read defensively:**

```python
excluded = subscription.get("excluded_tags") or []
```

If the `alter table` has not been run yet, a `subscription["excluded_tags"]` lookup would raise
`KeyError` and kill the cron run for *every* subscriber, not just the migration-lagged one.
`SUPABASE_SETUP.md:70` already warns about this class of ordering hazard for the `persons` join.
Degrading a missing column to "no filtering" reproduces today's behavior instead of breaking the
daily push.

`fetch_all_subscriptions()` already does `select("*")`, so it needs no change.

## Testing

Added to the existing `tests/` layout:

**`tests/test_matching.py`** — `filter_events_by_tags`, the core logic and the only piece with real
branching:
- untagged event survives every included set, including the empty one
- event whose tags are all excluded is dropped
- event with one surviving tag among several excluded ones is kept (the OR rule)
- empty included set drops every tagged event but keeps untagged ones

**`tests/test_db.py`** — the nested `event_tags(tags(name))` join flattens to a flat `tags` list, and
an event with no tag rows yields `[]` rather than `None`.

**`tests/test_matching.py`** — `included_from_excluded` / `excluded_from_included` round-trip: a UI
selection inverts to the right stored exclusions, and reading it back reproduces the original
selection. Also that an empty exclusion list yields the full taxonomy, and that output is ordered by
`TAG_TAXONOMY`. This guards the inversion, the easiest thing in the design to get backwards.

**Notification filtering** — a subscriber with exclusions receives only surviving matches; a
subscriber dict with no `excluded_tags` key at all still receives everything (the migration-lag
path).

No Streamlit widget-level tests. The existing suite doesn't test UI wiring, and that convention is
right — the logic worth testing lives in `core.matching` precisely so it can be exercised without a
Streamlit context.

## Deployment order

The schema change must land before the application code, same constraint as the `persons` join in
`SUPABASE_SETUP.md` section 3:

1. Run the `alter table` in the Supabase SQL editor.
2. Deploy the app and cron changes.

The defensive `.get()` in the notification script makes step 2 survivable if the order slips, but
the UI's "Update preferences" write would fail against a missing column, so the order still holds.

## Out of scope

- Per-tag calendar indicator colors (see the scope note above).
- Editing the tag taxonomy from the UI. `TAG_TAXONOMY` stays a code constant seeded by hand.
- Changing which tags any event carries. This spec only surfaces what the pipeline already wrote.
