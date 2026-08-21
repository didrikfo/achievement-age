# A two-channel filter: calendar and notifications

## Context

The filter is three preference axes wearing three different shapes, surfaced through three
different widget idioms inside one expander, and governing two different things at once.

| Axis | Stored as | Semantics | Widget today |
|---|---|---|---|
| 8 coarse categories | `subscriptions.excluded_categories` | exclusions | `st.multiselect` |
| 20 fine tags | `subscriptions.excluded_tags` | exclusions, gated behind category | `st.multiselect` in a `st.popover` |
| 8 integer sequences | `subscriptions.included_sequences` | **inclusions**, opt-in | `st.checkbox` + `st.multiselect` |

Two problems follow from that table.

**One selection serves two audiences.** The expander is titled "Filter what shows up on the
calendar", but `scripts/send_daily_notifications.py` reads the same three columns to decide what to
push. So the calendar and the notifications cannot disagree — you cannot mark prime days on the
calendar without also being pushed one roughly every nine days, and you cannot browse every
category while being notified about science only.

**The panel is assembled, not designed.** Mixed idioms, inverted semantics between neighbouring
controls, and a fine-tag layer whose rule has to be explained in prose ("unchecking a tag can never
bring back an event from a category you hid"). `src/app/ui.py` carries the cost: three separate
session-state seeding blocks, a `sequence_picker` shadow key that exists purely to dodge Streamlit's
element-id hashing, and `active_sequences` computed twice because a widget assigns to it mid-script.

### Goals

1. Choose, per row, whether it is marked on the calendar and whether it also notifies.
2. One consistent idiom for every filterable thing.
3. Existing subscribers keep exactly the notifications they get today, with no data migration.

### Out of scope

Everything about the *content* of a match, which is a separate spec: the wording of
`event_phrase`, the "The same age that…" construction in the event dialog, the reword prompt, a
batched sanity-check pass over generated texts, and adding a Wikipedia link to the notification
body. This spec changes *which* matches reach you, never *how they read*.

Also out of scope: per-category calendar colours (ruled out twice already), retagging the corpus,
and any change to `filter_events` or `anniversary_matches` themselves.

## 1. The model

Two channels over one set of rows.

```
calendar : which rows are marked on the calendar
notify   : which rows also push a notification     — invariant: notify ⊆ calendar
```

A **row** is one of the 8 categories (`CATEGORY_NAMES`) or one of the 8 sequences
(`SEQUENCE_TAXONOMY`). Sixteen rows, each independently on or off per channel.

Fine tags are **not** rows. A tag narrows its category on both channels equally — the notification
channel always uses the calendar's tag selection verbatim. This was a deliberate choice over
per-tag channels: it covers every scenario worth having ("all events marked, notify only for
science") at 16 rows of state instead of 36, and it keeps the tag layer as what it actually is — a
refinement of a category, not a peer of one.

`notify` is never stored directly. It is stored as a mirror flag plus an override:

- **mirror on** (the default) — `notify` *is* `calendar`; the override columns are not read.
- **mirror off** — `notify` comes from its own columns, **intersected with `calendar` on read**.

The intersection is what enforces the invariant without any data cleanup. Drop a category from the
calendar months after muting a different one, and any stale notify entry for the dropped row dies
on read rather than leaking a notification for a row the subscriber can no longer see.

## 2. Storage

The three existing columns keep their **exact current meaning** and become the calendar channel.
Three new columns carry the notification channel.

```sql
alter table subscriptions add column if not exists notify_mirrors_calendar    boolean  not null default true;
alter table subscriptions add column if not exists notify_excluded_categories text[]   not null default '{}';
alter table subscriptions add column if not exists notify_included_sequences  text[]   not null default '{}';
```

**`default true` on the mirror flag is the entire migration.** Every existing subscriber lands on
"notify = calendar", which is precisely the behaviour they have today. Nobody who muted War &
Conflict starts receiving war notifications, and no row needs rewriting.

Semantics follow the axis they mirror, deliberately rather than for symmetry's sake:

- `notify_excluded_categories` stores **exclusions**, like its calendar counterpart, so a category
  added to `TAG_CATEGORIES` later notifies by default instead of being silently muted for everyone.
- `notify_included_sequences` stores **inclusions**, like its calendar counterpart, because
  mathematical anniversaries are opt-in and a sequence added later must not start pushing itself.

There is no `notify_excluded_tags` column. Tags are not a per-channel axis.

`SUPABASE_SETUP.md` gains a section 14 documenting the above, and extends the existing
rename-hazard warnings: a `TAG_CATEGORIES` key is now persisted into **two** columns and a
`SEQUENCE_TAXONOMY` name into **two**, so renaming either orphans twice as much subscriber state.

## 3. Code structure

### 3.1 `src/core/preferences.py` (new, pure, no Streamlit)

One module owning the preference model, so the app and the cron job cannot disagree about what a
subscription means.

```python
@dataclass(frozen=True)
class ChannelSelection:
    categories: Tuple[str, ...]
    tags: Tuple[str, ...]
    sequences: Tuple[str, ...]

@dataclass(frozen=True)
class NotifyOverride:
    """Only the two axes the notification channel can differ on.

    Not a ChannelSelection: that type has a `tags` field, and a notification-only
    tag selection is a state the model forbids. Making it unrepresentable here is
    cheaper than validating it everywhere downstream.
    """
    categories: Tuple[str, ...]
    sequences: Tuple[str, ...]

@dataclass(frozen=True)
class Preferences:
    calendar: ChannelSelection
    notify_mirrors_calendar: bool
    notify_override: NotifyOverride   # ignored while mirroring

    @property
    def notify(self) -> ChannelSelection:
        """The effective notification channel, with the subset invariant applied.

        While mirroring, this is `calendar` exactly. Otherwise categories and
        sequences come from the override intersected with `calendar`, and `tags`
        is always `calendar.tags`.
        """


def default_preferences() -> Preferences:
    """A visitor with no subscription: every category and tag, no sequences, mirroring."""

def preferences_from_subscription(subscription: Optional[Dict]) -> Preferences:
    """Read a subscription row defensively. A missing column means the pre-feature default."""

def preferences_to_columns(preferences: Preferences) -> Dict[str, object]:
    """The six columns to write, for both create_subscription and update_subscription_filters."""
```

The click behaviour of section 4.3 also lives here, as pure functions over an immutable
`Preferences`, so that the rules — muting breaks the mirror, seeding the override from the calendar,
a dim bell turning both channels on — are unit-testable without a Streamlit runtime. A `Row` is a
`(kind, name)` pair where kind is `"category"` or `"sequence"`, which lets the panel render all
sixteen rows in one uniform loop:

```python
def all_rows() -> Tuple[Row, ...]                              # 8 categories, then 8 sequences
def is_on_calendar(preferences: Preferences, row: Row) -> bool
def is_notifying(preferences: Preferences, row: Row) -> bool
def toggle_calendar(preferences: Preferences, row: Row) -> Preferences
def toggle_notify(preferences: Preferences, row: Row) -> Preferences
def toggle_tag(preferences: Preferences, tag: str) -> Preferences
def set_mirror(preferences: Preferences, mirroring: bool) -> Preferences
```

`app/filters.py` then holds no rules of its own — it renders a row, and hands the click to one of
these.

Every read stays defensive in the style already established: a missing or null column means the
pre-feature default, because a cron run against a database that hasn't had the SQL applied yet must
degrade to today's behaviour rather than kill the whole run over one subscriber.

This module **absorbs** `included_tags_for_subscription` and `included_categories_for_subscription`
(currently in `core/matching.py`) and `included_sequences_for_subscription` (currently in
`core/sequences.py`) — one concept split across two modules, both of which this change touches
anyway. `matching.py` and `sequences.py` keep their matching logic and lose only the
preference-reading functions.

### 3.2 `src/app/filters.py` (new)

The Streamlit panel and nothing else: renders the rows, owns the session state, returns a resolved
`Preferences`. `ui.py` calls it once and uses the result for the calendar. This is the extraction
that keeps `ui.py` from growing past the ~400 lines it already carries.

### 3.3 Filtering stays where it is

`filter_events` and `anniversary_matches` are unchanged. They are simply called with a different
selection per channel:

| | events | anniversaries |
|---|---|---|
| calendar | `filter_events(events, p.calendar.categories, p.calendar.tags)` | `anniversary_matches(age, p.calendar.sequences)` |
| notifications | `filter_events(events, p.notify.categories, p.notify.tags)` | `anniversary_matches(age, p.notify.sequences)` |

`core/matching.events_for_subscription` is replaced by this two-line call at the one site that used
it, rather than gaining a channel parameter.

## 4. The panel

Inside the existing expander, retitled **"What counts as a match"** — "Filter what shows up on the
calendar" is now actively wrong, because the panel governs notifications too.

### 4.1 Structure

```
[toggle]  Notify me about everything I've marked
          (caption; for anonymous visitors: bells only apply once you subscribe)
─────────────────────────────────────────────── 
HISTORICAL EVENTS
  Sport                                      🔔
  Disasters                                  🔔
  Exploration & Space          2 tags ⌄      🔔
  Arts & Culture               4 tags ⌄      🔔   ← dim: row is off
  Science & Technology         4 tags ⌄      🔔
  Society & Belief             3 tags ⌄      🔔
  War & Conflict                             🔔̸   ← slashed: marked, but silent
  Politics & Power             4 tags ⌄      🔔
MATHEMATICAL ANNIVERSARIES
  Powers of 2 … Catalan numbers              🔔
```

Each row is a full-width flat `st.button` for the label plus a small `st.button` for the bell,
styled through keyed containers — the same `[class*="st-key-…"] button[data-testid=…]` mechanism
`styles.py` already uses for the calendar's event, anniversary and today marks.

**Only 5 of the 8 categories get a tag popover.** Sport, Disasters and War & Conflict map to a
single tag each (`sports`, `disaster`, `military`), so a "narrow by tag" control there is a no-op.

Tags move from one flat 20-item popover to a per-category one. The caption explaining that
unchecking a tag can never resurrect a hidden category is deleted along with it: a tag now visibly
lives inside its category, so the rule is structural rather than prose.

### 4.2 Row and bell states

| Row | Bell | Meaning |
|---|---|---|
| normal weight | 🔔 bright | marked, and it notifies |
| normal weight | 🔔 with red slash | marked, stays quiet |
| dimmed to ~34%, struck through | 🔔 dim | not counted at all |

The red slash is drawn by a CSS pseudo-element over the ordinary bell glyph, in `var(--aa-accent)`
(`#a01f1f`) so it matches the calendar's red circle. **Fallback:** if the overlay proves fragile
across platforms — emoji metrics vary — swap the glyph to 🔕 instead. That is a CSS-only change,
not a redesign.

### 4.3 Interactions

- **Click a row** — toggles it on/off for the calendar. Turning a row off does not clear its
  notification state; the intersection on read handles it, so turning the row back on restores what
  it had.
- **Click a bright or slashed bell** — toggles notifications for that row, **and switches the
  mirror off automatically**. The toggle is the way back to mirroring, not a gate to pass first, so
  the feature is discoverable by clicking the thing it applies to. While mirroring, every marked
  row shows a bright bell, so the first such click is always a mute.
- **Breaking the mirror seeds the override from the calendar**, then applies the click. So the
  instant the mirror drops, nothing has changed except the one row that was clicked — the panel
  never silently rearranges itself around the user.
- **Click a dim bell** — turns the row on *and* its notifications on. A control that looks
  clickable and ignores you is worse than one that does the obvious thing. This does **not** break
  the mirror: "on for both channels" is what mirroring already means, so there is nothing to
  override.
- **Toggle the mirror back on** — the override stops being read. It is not cleared, so toggling
  twice is not destructive within a session.

### 4.4 State handling

One authoritative `Preferences` in `st.session_state`, seeded once per session from the
subscription (or `default_preferences()` for an anonymous visitor), and mutated by button clicks.

**Every control in the panel is a button, including the tag toggles** (rendered inside the popover
with a ☑/☐ in the label). Buttons carry no persistent widget state, so there is exactly one source
of truth. This is what removes the current `sequence_picker` shadow key, the mid-script assignment,
and the double read of `active_sequences` — all three exist only because stateful widgets were
mirroring session values.

### 4.5 Saving

Subscribers get an explicit **Save preferences** button. Not autosave: every click is a full
rerun, and autosave would mean a database write per click. While session state differs from what
was loaded, the panel shows an unsaved-changes caption so a selection cannot be silently lost.

Anonymous visitors keep session-only preferences that are carried into `create_subscription`, as
today — bells included, so "notify me about science only" is set up in one visit rather than two.

## 5. Notifications

`scripts/send_daily_notifications.py` builds `Preferences` per subscriber and filters both match
lists through the **notify** channel. With the mirror flag defaulting to true, the output for every
existing subscriber is byte-for-byte what it is today.

Nothing else about the notification changes here — body text, title and click-through URL are the
other spec's territory.

## 6. Testing

**`tests/test_preferences.py`** (new, pure):
- mirror on ⇒ `notify == calendar`, whatever the override columns hold
- mirror off ⇒ override applied, intersected with calendar
- a notify entry for a row absent from the calendar is dropped on read
- `notify.tags` equals `calendar.tags` in both mirror states
- absent / null columns ⇒ pre-feature defaults (the un-migrated-database case)
- `preferences_to_columns` round-trips through `preferences_from_subscription`
- a category added to `TAG_CATEGORIES` notifies by default; a sequence added to
  `SEQUENCE_TAXONOMY` does not

**`tests/test_filters.py`** (new, Streamlit `AppTest`). Note that AppTest is **not** currently used
anywhere in this repo — the only mention is a comment in `ui.py` recording a past debugging
session — so this brings in a new test harness. It cannot point at `ui.py`, which opens a Supabase
connection at import time; it needs a small script that renders only the panel. That constraint is
itself an argument for the `app/filters.py` extraction in 3.2.

Because the click logic lives in `core/preferences.py` as pure functions (3.1), these tests cover
only wiring — that a click reaches the right function — and the interesting cases are unit-tested
without Streamlit.
- clicking a row toggles calendar membership
- clicking a bell toggles notification and turns the mirror off
- clicking a dim bell turns both on
- re-enabling the mirror restores mirrored behaviour without losing the override

**Updated:** `tests/test_db.py` for the six-column write; a notification-script test asserting that
a mirroring subscriber's output is unchanged, and that a non-mirroring one is narrowed.

## 7. Risks

- **Rename hazard, doubled.** A `TAG_CATEGORIES` key now persists into two columns and a
  `SEQUENCE_TAXONOMY` name into two. Renaming either silently orphans subscriber state in both.
  Documented in `SUPABASE_SETUP.md` alongside the existing warnings.
- **Rerun per click.** Sixteen rows means the panel cannot feel instant the way a client-side
  checkbox grid would. Accepted: it is the cost of doing this in Streamlit at all, and it is the
  same cost the calendar already pays per day click.
- **Emoji overlay.** Covered by the 🔕 fallback in 4.2.
- **Widget count.** Up to ~40 buttons per rerun in the expander. Well within Streamlit's range, but
  worth watching if rows are ever added.
