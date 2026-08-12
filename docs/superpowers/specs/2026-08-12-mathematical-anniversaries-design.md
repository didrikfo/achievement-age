# Mathematical anniversary days

## Context

The calendar currently marks one kind of day: one where your age in days equals the age in days some
historical figure was at a notable moment. Those matches come out of the `events` table and are
filtered through a two-level category/tag hierarchy.

This spec adds a second, entirely different kind of day — one where your **age in days is itself an
interesting number**. Day 2,048 is a power of two. Day 4,181 is a Fibonacci number. Day 16,384 is
both a power of two and a perfect square. These need no database, no ingest run, and no corpus:
they are a property of the integer, computable from `age_days` alone.

The `MATCHERS` list in `scripts/send_daily_notifications.py` was written with exactly this in mind —
its module docstring names "age-in-days is a round number in base 10/binary" as the motivating future
case. The `IDEAS.md` entry that this spec implements asks for the sequences to be filterable both as
a whole and individually, and for a different calendar symbol (a triangle rather than a circle).

### Out of scope

- **OEIS custom sequences.** Letting a subscriber name an arbitrary OEIS sequence number to track is
  a genuinely different feature — it needs its own input UI, its own validation, and a per-subscriber
  sequence definition rather than a fixed taxonomy. Deliberately deferred to its own design.
- **Any change to the `events` table or its schema.** Nothing here is an event.
- **Per-sequence marker colors or shapes.** One triangle covers every sequence, for the same reason
  one red circle covers all twenty event tags (ruled in the tag-filtering spec, reaffirmed in the
  category spec). A month grid does not have room for eight symbols.

## The eight sequences

| Sequence | On when the category is first enabled? | Matches in a ~90-year life |
|---|---|---|
| Powers of 2 | yes | 16 (day 1 → 32,768) |
| Powers of 10 | yes | 5 (day 1 → 10,000) |
| Triangle numbers | yes | ~255 |
| Fibonacci numbers | yes | 23 |
| Primes | **no** | ~3,500 |
| Perfect squares | **no** | ~181 |
| Cubes | **no** | ~32 |
| Catalan numbers | **no** | 12 |

The split is about **firing rate**, not about which sequences are interesting. Prime density near a
90-year lifespan (~32,850 days) is roughly `1/ln(N)` ≈ **1 in 9 days**, so primes alone would turn a
feature that currently interrupts you a handful of times a year into a near-weekly push. Perfect
squares and cubes are sparse in absolute terms but cluster densely in childhood (days 1, 4, 9, 16, 25
…) and would make a newborn's first month solid triangles. Catalan numbers are simply obscure enough
that nobody should be opted in without asking for them.

The default-on four are all sparse *and* legible: a power of two, a Fibonacci number, and a
triangular number are all things a non-mathematician recognizes as a real coincidence.

**Round multiples of 1,000 were considered and cut.** They recur on a fixed 2.74-year cadence, which
makes them a countdown rather than a discovery — the opposite of what the feature is for.

### What counts as a member

Every sequence is **the enumeration of its positive terms**. Powers of two are 1, 2, 4, 8, …;
triangular numbers are T₁ = 1, T₂ = 3, …; Fibonacci is F₁ = 1, F₂ = 1, F₃ = 2, …; Catalan is
displayed 1-based over the distinct terms 1, 2, 5, 14, … (the conventional C₀ = 1 is skipped because
it duplicates C₁ and would make every printed ordinal off by one).

This one rule settles both degenerate cases without a single special case in the code:

- **Day 0** — your birthday — matches nothing, because no sequence's positive enumeration contains 0.
- **Day 1** matches seven of the eight (everything but primes), because 1 genuinely is the first
  power of two, the first power of ten, the first triangular number, and so on.
- **Negative days** (browsing months before you were born) match nothing.

Day 1 lighting up seven times is left exactly as it falls out. So are the real coincidences further
up: day 144 is both a perfect square and a Fibonacci number (the largest such, by Cohn's theorem),
and days 21 and 55 are both Fibonacci and triangular. Suppressing these would mean writing code to
hide the most mathematically interesting days in the feature.

## Architecture

### Computed live, never stored

Every anniversary is derived from `age_days` at the moment it is needed. There is no table, no
migration, and no backfill, because there is nothing to store: the answer to "is 2,048 a power of
two" does not depend on any data this app owns.

This is not just an economy. The calendar lets a visitor browse **any** month, past or future — the
year selector spans 1900 to at least fifty years out — so a stored table would have to be dense over
every integer any visitor might land on. A predicate is O(√n) at worst and needs no bound at all.

### `src/core/sequences.py` (new)

All of it lives in one new module rather than in `core/matching.py`. `matching.py` is about relating
*people* to *events* — name normalization, age-in-days indexing, phrase rendering, tag filtering.
Integer sequence membership shares no vocabulary and no data with any of that. A new module keeps
each testable in isolation and keeps `matching.py` from becoming the place things go when they have
nowhere else to be.

The module's core is one dict from sequence name to a single function:

```python
_SEQUENCES: Dict[str, Callable[[int], Optional[str]]]
```

Each function answers "is `n` in this sequence, and if so how should we describe it?" in one call,
returning the description or `None`. Collapsing the predicate and the copy into one function means
they cannot disagree — there is no way to add a sequence whose membership test passes but whose
description was written for a different index. A test asserts the dict's keys are exactly
`SEQUENCE_TAXONOMY`, mirroring the existing partition test over `TAG_CATEGORIES`.

The public surface:

```python
def sequences_for(age_days: int) -> List[Tuple[str, str]]:
    """(sequence name, description) for every sequence age_days belongs to, in taxonomy order."""

def anniversary_matches(age_days: int, included_sequences: Collection[str]) -> List[Dict]:
    """One match dict per included sequence age_days belongs to."""

def anniversary_sentence(match: Dict) -> str:
    """The display sentence: "Your age in days (2,048) is a power of two, 2¹¹.\""""

def included_sequences_for_subscription(subscription: Dict) -> List[str]:
    """The sequences a subscription tracks. Missing/null column means none."""
```

A match dict is `{"sequence": str, "age_days": int, "description": str}`.

`anniversary_matches` is the shared entry point the daily cron and the calendar both call, so the
notification you receive and the day you see circled can never disagree — the same reason
`full_sentence()` is shared today.

### Anniversary matches stay in their own list

Anniversary dicts are **not** mixed into the event match list, and no new entry is added to
`MATCHERS`. The two record types have nothing in common: an event has `name`, `event_phrase`,
`year/month/day`, `tags`, and a person; an anniversary has none of those. Merging them would mean
every consumer downstream — the notification title builder, the dialog, the filter — carrying a
`"kind"` discriminator and branching on it, and it would quietly route anniversaries through
`filter_events`, where they would survive only by accident (via the "untagged events always survive"
rule) rather than by design.

Two lists, each with one shape, is the smaller thing. `MATCHERS` stays as it is — it remains the
right seam for a future matcher that produces *event-shaped* records.

## Filtering and rollout

### A separate taxonomy, stored as inclusions

The eight sequence names are a new taxonomy in `core/config.py`, alongside — not inside —
`TAG_TAXONOMY` and `TAG_CATEGORIES`:

```python
SEQUENCE_TAXONOMY: List[str] = [
    "Powers of 2", "Powers of 10", "Triangle numbers", "Fibonacci numbers",
    "Primes", "Perfect squares", "Cubes", "Catalan numbers",
]

DEFAULT_SEQUENCES: List[str] = SEQUENCE_TAXONOMY[:4]
```

They are deliberately kept out of `TAG_CATEGORIES` even though the original sketch put them there as
a ninth category. Two things make that the wrong home:

1. `TAG_CATEGORIES` is documented and **tested** as an exact partition of `TAG_TAXONOMY`. Sequence
   names are not event tags: they have no row in the `tags` table, no `event_tags` join, and no
   ingest step that assigns them. Adding them would mean weakening the invariant that currently
   catches an unhomed tag.
2. `primary_category()` derives an event's category from its stored tags. An anniversary has no tags
   and is not an event, so it could never be categorized by that function at all.

### The default-off requirement, and why inclusions get it for free

**This feature must be off for every existing subscriber the day it ships, without a data
migration.** Shipping it on would start pushing notifications — near-weekly ones, if primes were
included — to people who subscribed to something else entirely.

The event filter stores **exclusions** precisely so that a *newly added tag* is visible by default:
it appears in nobody's `excluded_tags`, so everybody gets it. That is the right default for a
twenty-first tag on the same kind of content, and exactly the wrong default here. Reusing that column
would require a one-off `UPDATE` writing the new category into every existing row, plus app code to
keep seeding it for new subscriptions — a migration that silently breaks the feature for anyone it
misses.

So the sequence preference is stored as **inclusions**:

```sql
alter table subscriptions add column if not exists included_sequences text[] not null default '{}';
```

`default '{}'` is the entire rollout story. Every existing row is born empty, empty means no
sequences, and no sequences means no triangles and no pushes. There is nothing to migrate and
nothing to miss.

The same choice makes the defensive read and the safe default the same thing:

```python
subscription.get("included_sequences") or []
```

A subscription row from before the `alter table` has no such key. For the event columns, that case
degrades to "no filtering" — the pre-feature behavior. Here it degrades to "no anniversaries" — also
exactly the pre-feature behavior. A cron run that beats the migration is harmless in both directions.

### Off for anonymous visitors too

A first-time visitor with no subscription also starts with no anniversaries marked. The alternative
— on for anonymous browsing, off for existing subscribers — would mean a subscriber's own private
link shows *fewer* marks than the anonymous view of the same app, which reads as a bug. One rule,
applied everywhere, is worth the small cost in discoverability; the filter panel is where a visitor
already goes to change what the calendar shows.

### The four-on/four-off split is UI seeding, not schema

`DEFAULT_SEQUENCES` never reaches the database as a default. It is what the multiselect is
**pre-loaded** with, so that the moment a visitor enables the feature they get the four sparse,
legible sequences rather than an empty box they have to populate themselves. Turning the feature on
and saving writes those four names explicitly; the stored value is always the literal set of
sequences that subscriber chose.

### Anniversaries are not subject to the event filter

The category and tag filters apply to events only. Unchecking "Science & Technology" has no effect on
whether day 2,048 is marked, and the sequence filter has no effect on which historical events appear.
Two independent kinds of day, two independent controls.

## UI

### The filter panel

The existing expander is relabelled from "Filter which events show up" to
**"Filter what shows up on the calendar"** — it now governs two different things. Inside, below the
existing category multiselect and advanced tag popover:

```
☐ Also mark mathematical anniversaries

   Days when your age in days is itself an interesting number. Marked with a
   triangle instead of a circle.
```

Ticking the checkbox reveals a multiselect over all eight sequences, pre-loaded with the four
defaults, captioned:

> Primes and squares are off to begin with because they'd land far more often — a prime
> day comes round roughly once every nine days.

A checkbox gating a multiselect, rather than a bare multiselect whose empty state means "off",
because those two states need to look different: "I haven't turned this on" and "I turned it on and
then deselected everything" are the same stored value but very different things to a reader.

The multiselect is **not** given a Streamlit widget `key`. Streamlit garbage-collects widget state
for widgets that were not rendered on a run, so a keyed multiselect hidden behind an unticked
checkbox would lose its selection — and a later read would raise. It is held in a plain session
variable and passed as `default=` instead, which survives not being rendered.

Session seeding, once per session, mirroring the existing pattern:

| Visitor | Checkbox | Multiselect pre-loaded with |
|---|---|---|
| Never opted in (anonymous, or subscriber with an empty column) | unticked | the four defaults |
| Subscriber with stored sequences | ticked | their stored sequences |

Because the checkbox starts unticked with the defaults already loaded behind it, enabling the feature
is one click, not five.

The live selection is carried into `create_subscription` on subscribe and written by "Update
preferences" for existing subscribers, exactly like the two event filters.

### The triangle marker

A day carrying one or more anniversaries gets a **triangle**; a day carrying one or more historical
matches gets the existing **red circle**; a day carrying both gets **both, superimposed**. The mark
does not indicate how many matches or which sequence — it says "there is something here," and the
dialog says what.

Both marks are drawn as pseudo-elements on the same day button (`::before` for the circle, `::after`
for the triangle), so superimposition needs no extra element and no extra branch. The triangle is a
stroked SVG data URI rather than a CSS border trick, because an *outlined* triangle is what matches
the hand-annotated look of the circle, and CSS border triangles are solid.

This requires narrowing one existing rule: the circle is currently drawn on **every** primary button
inside the calendar grid, so an anniversary-only day would inherit a circle it should not have. Both
marks become conditional on a marker class instead.

Those classes come from Streamlit container keys, which already carry the `today`-plus-match case
(`st-key-today-match-…`). Since a container has exactly one key, the three independent flags — has
events, has anniversaries, is today — are expressed as **nested** containers, one per active flag,
each contributing its own `st-key-mark-*-…` class. The CSS matches on `[class*="st-key-mark-event-"]
button`, a descendant selector, so nesting depth does not matter and the three compose freely.

### The dialog

`show_event_dialog` becomes `show_day_dialog(day_date, event_matches, anniversary_matches)`, titled
"This day" rather than "Matching event" — it may now contain no events at all.

The two kinds are listed under their own subheadings, never interleaved and never merged, because a
sentence about Ada Lovelace and a sentence about the number 2,048 have nothing to do with each other
beyond falling on the same date. A subheading is only rendered when its list is non-empty, so a
triangle-only day shows no empty "Historical matches" section.

When a day carries more than three matches in total, the list is wrapped in a fixed-height scrolling
container so the dialog cannot grow past the viewport. Below that threshold it renders at natural
height rather than leaving a mostly-empty scroll box. (Day 1 is the pathological case: seven
anniversaries at once.)

### Copy

The description is generated per sequence, carrying the index or exponent where that is the
interesting part:

| Sequence | Example sentence |
|---|---|
| Powers of 2 | Your age in days (2,048) is a power of two, 2¹¹. |
| Powers of 10 | Your age in days (10,000) is a power of ten, 10⁴. |
| Triangle numbers | Your age in days (5,050) is the 100th triangular number. |
| Fibonacci numbers | Your age in days (4,181) is the 19th Fibonacci number. |
| Primes | Your age in days (10,007) is a prime number. |
| Perfect squares | Your age in days (10,000) is a perfect square, 100². |
| Cubes | Your age in days (8,000) is a perfect cube, 20³. |
| Catalan numbers | Your age in days (4,862) is the 9th Catalan number. |

Written as a fresh template rather than by bending `full_sentence()`, which reconstructs an opening
around an event's `name` and `event_phrase` — neither of which exists here.

### Notifications

`_send_ntfy_notification` builds its title from `event['name']`, so it gets a sibling rather than a
branch: `_send_anniversary_notification`, with the title **"You've hit a mathematical anniversary"**
(parallel to the existing "You're now as old as X was") and `anniversary_sentence` as the body. Both
carry the same click-through link.

One notification per matching sequence, matching how multiple event matches on one day already
behave.

## Testing

`tests/test_sequences.py` (new) carries the weight, since every sequence predicate is a small pure
function over integers:

- Each of the eight: known members and known near-misses, including a value large enough to exercise
  the top of a human lifespan (32,768 is a power of two; 32,767 is not).
- **Day 0 matches nothing** and **day 1 matches seven of eight** — these lock in the
  positive-terms-only rule, which is the whole reason no degenerate-case handling exists.
- **Day 144 is both a perfect square and a Fibonacci number**, and **days 21 and 55 are both
  Fibonacci and triangular** — these lock in the decision *not* to deduplicate coincidences.
- Negative input matches nothing.
- `_SEQUENCES` keys are exactly `SEQUENCE_TAXONOMY`, and `sequences_for` returns in taxonomy order.
- `DEFAULT_SEQUENCES` is a subset of the taxonomy and **excludes primes** — a regression test on the
  firing-rate decision, which is the easiest thing here to undo by accident.
- `anniversary_matches` returns `[]` for an empty inclusion list, and ignores names outside the
  taxonomy.
- `included_sequences_for_subscription` returns `[]` for a missing key and for a null column — the
  migration-lag path, which here doubles as the rollout default.
- The ordinal and superscript helpers, including 11th/12th/13th.

`tests/test_db.py` gains: `create_subscription` defaults `included_sequences` to `[]`, stores a given
list, and `update_subscription_filters` writes all three preference columns in one update.

No Streamlit widget tests, following the existing convention — all the branching logic lives in
`core.sequences` precisely so it can be exercised without a Streamlit context. The UI is verified by
running the app: that enabling the feature makes triangles appear, that a both-kinds day shows both
marks, and that the dialog separates the two lists.

`scripts/send_daily_notifications.py` remains untestable as a module (its `MATCHERS` list calls
`fetch_events()` at import time, which needs live credentials) — which is why the logic it calls is
in `core.sequences`.

## Rollout order

1. Apply the `included_sequences` SQL in the Supabase SQL editor.
2. Deploy the app and cron changes.

Step 1 must land first because "Update preferences" writes the column directly. The defensive read
keeps the cron job alive if the order slips, and — unlike the event columns — a slipped order here
fails safe in the direction the feature already wants: nobody gets anniversaries until the column
exists.

## Housekeeping

`IDEAS.md`'s mathematical-anniversary entry is rewritten from a to-do into a description of what
shipped, per that file's own stated convention. The OEIS idea it does not cover is split out as its
own remaining entry rather than being deleted along with it.
