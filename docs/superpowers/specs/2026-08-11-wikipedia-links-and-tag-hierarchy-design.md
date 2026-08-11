# Wikipedia links, full subscription URLs, and a two-level tag hierarchy

## Context

Three independent changes, grouped because they all touch the event dialog and the subscription
flow in `src/app/ui.py`:

1. **`persons.wikipedia_url` is empty.** The column has existed since the persons/event-detail
   work, and `ui.py` already renders a link when one is present — but a live check of the database
   found 0 of 1,682 person rows populated, so the feature has never actually shown. No local data
   file carries Wikipedia URLs either, so the values have to be fetched.
2. **The copyable subscription link is not a URL.** It is built as `f"{APP_BASE_URL}?u={token}"`,
   and `APP_BASE_URL` is an optional secret that is blank in local development — so the "link" a
   visitor copies can be the bare string `?u=abc123`, which cannot be bookmarked or pasted.
3. **Twenty tags is too many to filter with.** The corpus is now 3,341 events, every one tagged,
   and the distribution is heavily skewed: politics 1532, military 903, law 491, royalty 471,
   religion 294, social 226, arts 200, science 191, exploration 187, disaster 159, sports 131,
   technology 104, music 103, economics 65, space 54, health 52, engineering 51, education 44,
   film 36, philosophy 15. A visitor asked to reason about twenty checkboxes mostly sees noise.

### Out of scope

- **Wikipedia links on events themselves.** Wanted later, and both the person link and the event
  link should appear together when that happens. The display built here is shaped to accept a
  second link without a redesign, but no `events.wikipedia_url` column is added now.
- **Re-tagging the corpus.** The coarse category is derived from existing tags, so the LLM prompt,
  `TAG_TAXONOMY`, `validate_tags`, and the `tags`/`event_tags` rows are all untouched.
- **Per-category calendar colors.** Still a single red circle, as ruled in the tag-filtering spec.

## 1. Wikipedia links for people

### 1.1 How a link is verified

The failure that matters is not a missing link, it is a *wrong* link: "John Smith" resolves to a
Wikipedia disambiguation page or to a different John Smith. Two facts make verification cheap:

- Every person's exact birth date is already derivable from their events — `event date − age_days`.
  All 3,341 events have a `person_id`, so no name-matching is needed to group them.
- The MediaWiki API returns, in one batched call, the canonical title after redirects, the page
  URL, a `disambiguation` flag, and the page's Wikidata Q-id.

So each candidate link is checked against a birth date we already know, and only verified links are
written.

### 1.2 `src/ingest/sources/wikipedia.py` (new)

Two network functions, both isolated here so tests can monkeypatch them the way `test_wikidata.py`
already does for `wikidata._get_json`:

```python
def resolve_titles(names: List[str]) -> Dict[str, Dict]:
    """Batch-resolve article titles. 50 names per request.

    Returns {requested_name: {"title", "url", "qid", "status"}}, where status is
    one of "found", "missing", "disambiguation".
    """

def fetch_birth_year(qid: str) -> Optional[int]:
    """The birth year from a Wikidata item's P569 statement, or None if it has none."""
```

`resolve_titles` calls `action=query&titles=...&redirects=1&prop=pageprops|info&inprop=url&
ppprop=wikibase_item|disambiguation`, batching 50 titles per request (verified live: 6 names cost
669 bytes, so the whole run is ~34 requests). The response's `normalized` and `redirects` maps are
followed *backwards* to attribute each returned page to the name that was asked for — a page
reached by redirect comes back under its canonical title, not the requested one, so a naive
title-keyed lookup would drop it.

`fetch_birth_year` calls the Wikidata REST endpoint
`https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/{qid}/statements?property=P569`.
Only the year is compared, so any statement precision down to year level is usable — unlike
`wikidata.parse_birth_claim`, which needs day precision because it computes ages.

Two batched alternatives were measured and rejected: `action=wbgetentities` with `props=claims`
returns roughly 300 KB per entity (3 MB for ten), and the SPARQL endpoint answered a ten-item
`VALUES` query with HTTP 429. Per-item REST is 6 KB and ~0.3 s, so ~10 minutes for 1,682 people —
acceptable for a one-off, resumable script.

Both functions carry the existing `USER_AGENT` and `REQUEST_DELAY_SECONDS = 0.2` conventions from
`sources/wikidata.py`.

### 1.3 `src/ingest/backfill_person_wikipedia.py` (new)

Run once, resumable:

```bash
./venv/Scripts/python.exe -m ingest.backfill_person_wikipedia
```

1. Fetch persons whose `wikipedia_url` is null. Already-populated rows are skipped, so a re-run
   after a partial failure costs nothing and never overwrites a hand-corrected value.
2. Build `person_id → birth date` from `fetch_events()` (`date(year, month, day) − age_days`). A
   person whose events imply two different birth dates is not verifiable and goes straight to the
   review file — that is a data bug worth seeing, not something to average away.
3. `resolve_titles` in batches. `missing` and `disambiguation` are rejected with that reason.
4. `fetch_birth_year` per surviving Q-id; reject on mismatch or on no birth claim. A ±1 year
   tolerance is allowed, because a Julian/Gregorian or timezone edge can shift a January or
   December birth date by a day across a year boundary.
5. Write verified URLs to `persons.wikipedia_url`. Everything else appends to
   `data/tmp/person_wikipedia_review.json` via the existing `enrichment.write_review_entries`,
   with `{name, status, detail, candidate_url}`.
6. Print a summary line per outcome: verified / missing / disambiguation / year mismatch / no birth
   claim / conflicting local birth dates.

Every lookup outcome is cached by `normalize_name(name)` in `data/wikipedia_person_cache.json`,
written with the atomic temp-file-then-`os.replace` pattern `wikidata.save_cache` already uses, so
an interrupted run cannot leave a truncated cache and a second run makes no repeat requests.

### 1.4 Display

`ui.py`'s event dialog currently ends with:

```python
st.markdown(f"[Read more on Wikipedia]({wikipedia_url})")
```

That becomes a "further reading" line that names its target, so a reader knows *what* they would be
opening — and so a second link can join it later without the two being indistinguishable:

```
Further reading: Alan Turing on Wikipedia
```

Rendered as one small caption-styled line beneath the event's description. When event-level links
arrive, the same line grows a second entry (`Further reading: Alan Turing · Enigma machine`); the
line is built from a list of `(label, url)` pairs so that is an append, not a rewrite. Events whose
person has no link render no line at all — no dead "no link available" text.

## 2. Full subscription URL

### 2.1 `src/app/links.py` (new)

```python
def base_url_from(url: str) -> str:
    """Strip query string, fragment and trailing slash from a page URL."""

def subscription_link(token: str) -> str:
    """The full bookmarkable URL for a subscription token."""
```

`subscription_link` reads the app's own address from `st.context.url` (present in the installed
Streamlit 1.45.1) and passes it through `base_url_from`; if the context is unavailable or empty it
falls back to `get_config_value("APP_BASE_URL", default="")`. This is correct in local development,
on Streamlit Community Cloud, and on any future domain, with no secret to keep in sync.

The split exists so the URL manipulation — the part with edge cases — is a pure function testable
without a Streamlit runtime. `scripts/send_daily_notifications.py` keeps reading the `APP_BASE_URL`
environment variable directly: it runs in GitHub Actions with no browser context, so there is
nothing for `st.context` to report.

### 2.2 Setup text

The success block shows the full URL in the existing `st.code` block (so the copy button yields
something pasteable), followed by:

1. **Save this link.** Bookmark it or add it to your home screen — it remembers your birthday and
   your filters, and it's the only way back to them.
2. Install the [ntfy app](https://ntfy.sh) and subscribe to the topic `{ntfy_topic}`.
3. You'll get a notification whenever your age matches an event.

*Considered and dropped:* re-showing the link to returning subscribers. They arrived through it, so
it is already in their address bar.

## 3. Two-level tag hierarchy

### 3.1 Categories

`core/config.py` gains an ordered mapping. Its key order **is** the precedence order:

```python
TAG_CATEGORIES: Dict[str, List[str]] = {
    "Sport":               ["sports"],
    "Disasters":           ["disaster"],
    "Exploration & Space": ["exploration", "space"],
    "Arts & Culture":      ["arts", "music", "film", "philosophy"],
    "Science & Technology":["science", "technology", "engineering", "health"],
    "Society & Belief":    ["religion", "social", "education"],
    "War & Conflict":      ["military"],
    "Politics & Power":    ["politics", "law", "royalty", "economics"],
}
```

Every tag belongs to exactly one category; a test asserts the values partition `TAG_TAXONOMY` with
nothing missing and nothing duplicated, so adding a tag to the taxonomy without homing it fails
loudly instead of silently making events uncategorized.

**Precedence runs most-specific to most-general**, which matches how the filter is expected to be
used: someone who wants to exclude war, disasters, or sport is excluding a *subject*, and those
events must land in that category to be excludable at all. Someone excluding politics is excluding
a broad background theme and will tolerate the occasional war event that is also political. On the
real data this puts the 398 military+politics events in War & Conflict, the 251 law+politics events
in Politics & Power, and the 59 disaster+politics events in Disasters.

### 3.2 `core/matching.py`

```python
def primary_category(event: Dict) -> Optional[str]:
    """The event's single coarse category: the first category in TAG_CATEGORIES
    order that any of the event's tags belongs to. None if the event has no
    recognized tag."""
```

Derived at read time, not stored. No migration, no backfill, and it self-corrects when an event's
tags change. Tags outside the taxonomy (impossible via `validate_tags`, possible via a hand edit in
the Supabase table editor) are ignored rather than crashing.

`filter_events_by_tags(events, included_tags)` is replaced by:

```python
def filter_events(events, included_categories, included_tags) -> List[Dict]:
```

An event is kept when:

- it has no recognized category — the existing permissive rule for untagged events, preserved
  because untagged rows are a normal transient backfill state; **or**
- its `primary_category` is in `included_categories` **and** at least one of its tags is in
  `included_tags`.

With every fine tag selected (the default) the second clause reduces to pure category filtering.
The advanced control can only ever narrow within kept categories, never pull an event back in from
a hidden one — so each event has exactly one place it can appear, and unchecking a box can never
make something new appear.

The two inversion helpers generalize to take the taxonomy they invert against, so tags and
categories share one implementation rather than growing a near-duplicate pair:

```python
def included_from_excluded(excluded: Collection[str], taxonomy: Sequence[str]) -> List[str]:
def excluded_from_included(included: Collection[str], taxonomy: Sequence[str]) -> List[str]:
```

`included_tags_for_subscription` gains a sibling `included_categories_for_subscription`, and
`events_for_subscription` passes both into `filter_events`. Both read their column with
`.get(...) or []`, so a cron run against a database missing the new column filters on tags alone
rather than failing the entire run.

### 3.3 Schema

```sql
alter table subscriptions add column if not exists excluded_categories text[] not null default '{}';
```

Documented as section 11 of `SUPABASE_SETUP.md`, with the same "run before deploying the code"
warning as section 10 — the "Update preferences" button writes this column.

Exclusions rather than inclusions, for the same reason as `excluded_tags`: a category added later
is then visible to existing subscribers by default instead of silently hidden. `default '{}'` means
every current subscriber keeps receiving exactly what they receive today, so no backfill is needed.

`core.db` follows: `create_subscription(birthday, excluded_tags, excluded_categories)`, and
`update_subscription_tags` becomes `update_subscription_filters(token, excluded_tags,
excluded_categories)` — renamed because it no longer writes only tags.

### 3.4 UI

Inside the existing "Filter which events show up" expander:

- `st.multiselect("Show events about:", options=list(TAG_CATEGORIES), key="included_categories")` —
  the primary control, all eight selected by default.
- A nested `st.expander("Advanced: filter by detailed tag")` holding the current 20-tag multiselect
  unchanged, as a **flat list** rather than grouped by category, captioned to say it narrows within
  the categories chosen above.
- The existing caption about untagged events always showing, unchanged.
- For subscribers, the "Update preferences" button saves both exclusion lists in one write.

Session seeding mirrors the current pattern: `included_categories` is seeded once per session from
the subscription (or to everything, for anonymous visitors), so a rerun cannot clobber an
in-progress selection.

## Testing

Unit tests, following existing conventions (`tests/test_matching.py`, `tests/test_wikidata.py`):

- `primary_category`: precedence resolves military+politics to War & Conflict and law+politics to
  Politics & Power; untagged returns None; an unknown tag is ignored.
- `TAG_CATEGORIES` partitions `TAG_TAXONOMY` exactly.
- `filter_events`: untagged events always survive; an event in an excluded category is dropped even
  when one of its tags is included; an event in an included category is dropped when *none* of its
  tags is included; all-selected is a no-op.
- The generalized inversion helpers round-trip against both taxonomies and order by taxonomy.
- `events_for_subscription` with the `excluded_categories` key absent behaves as tags-only.
- `base_url_from`: strips query, fragment, and trailing slash; leaves a clean base URL alone.
- `resolve_titles`: attributes a redirected page back to the requested name; flags disambiguation
  and missing pages — with the HTTP layer monkeypatched, as `test_wikidata.py` does.
- The backfill's verification decision (accept on year match, reject on mismatch, ±1 tolerance)
  tested as a pure function against fixture data, with no network.

Manual verification, because none of it is unit-testable: run the backfill against the live
database and read the summary and review file; then check in the running app that an event dialog
shows the correct person's link, that the copied subscription link is a full pasteable URL, and
that category filtering visibly changes which days are circled.

## Housekeeping

`IDEAS.md`'s tag-filtering entry still reads "not yet implemented" and points at the tag-filtering
spec as pending. That work shipped, and this spec extends it, so the entry is rewritten to describe
what now exists rather than being left as a stale to-do.

## Rollout order

1. Apply the `excluded_categories` SQL.
2. Deploy the application code.
3. Run the Wikipedia backfill (independent of the other two — links simply start appearing).
