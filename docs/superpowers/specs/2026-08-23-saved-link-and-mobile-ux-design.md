# Saved-link visibility, filter-row mobile fix, and calendar swipe navigation

## Context

Three independent pieces of user feedback about the returning-visitor experience:

1. A returning subscriber (visiting via their saved `?u=<token>` link) currently sees only a
   "welcome back" caption — their birthday isn't shown, and there is no way to see their ntfy
   notification link/topic again if they didn't copy it at creation time (`app/ui.py:49-55`).
2. The notification-settings (filter panel) rows look fine on desktop but wrap/split awkwardly on
   mobile, because the calendar grid already has a mobile column-stacking fix
   (`app/styles.py:107-116`) that the filter panel never received.
3. The calendar's month navigation is tap-only (prev/next buttons, or select boxes); the user
   asked whether swipe navigation is feasible on mobile at all, given this is a pure
   server-rendered Streamlit app with no client-side JS framework.

These three changes touch different files and have no dependencies on each other; they're grouped
in one spec because each is small.

## 1. Saved-link page: birthday header + notification-link reveal

In `app/ui.py`, the returning-subscriber branch (currently lines 49-55) changes to:

- A display-only birthday heading at the top of the page, formatted from
  `subscription["birthday"]` (e.g. "Born March 3, 1990"), replacing the current caption. No
  date-input widget is rendered in this branch, so the birthday is structurally non-editable here.
- A collapsed `st.expander` (matching the existing pattern used for "Get notified" and "What
  counts as a match") labeled to reveal the notification link. Opening it shows the same
  `st.code(subscription_link(subscription["token"]))` plus the ntfy-topic instructions currently
  shown only once at creation time (`ui.py:73-83`), reusing `subscription_link()` from
  `app/links.py:43` and the `ntfy_topic` already returned by `get_subscription()`. No new DB
  fields or helpers — everything needed is already persisted per-subscription (`token` and
  `ntfy_topic` are both generated with `secrets.token_urlsafe` at creation and never derived from
  the birthday, so two subscribers with the same birthday already get independent links).

## 2. Filter-panel mobile fix

`app/styles.py:107-116` forces `flex-wrap: nowrap !important; min-width: 0 !important; flex: 1 1
0 !important` on `.st-key-calendar-grid`'s `stColumn`s, countering Streamlit's default of
stacking columns to full width below ~640px viewport width. The filter panel's rows
(`label_col, tag_col, bell_col = st.columns([6, 2, 1])` in `filters.py:130-155`, each inside a
container keyed via `_row_key`) have no equivalent rule and so stack/wrap on mobile.

Add a parallel CSS rule in `styles.py`, scoped to filter-row containers (selector shaped like
`[class*="st-key-filter-row-"] [data-testid="stHorizontalBlock"]`, matching the `st-key-filter-row-*`
naming from `filters.py:64-85`), applying the same `nowrap` + `flex: 1 1 0` treatment so the
label/tag-popover/bell stay on one line at any viewport width. Pure CSS addition — no Python
changes.

## 3. Calendar swipe navigation (mobile)

A `st.components.v1.html(..., height=0)` snippet, rendered once near the calendar section in
`ui.py` (around lines 150-243), containing vanilla JS that:

- Reaches into `window.parent.document` (the components iframe is same-origin, so this is
  accessible) and attaches `touchstart`/`touchend` listeners to the calendar grid container,
  selected via the existing `.st-key-calendar-grid` class (`styles.py:107`).
- On `touchend`, compares horizontal vs. vertical displacement since `touchstart`. Only treats it
  as a swipe if horizontal movement clearly dominates (to avoid misfiring during vertical page
  scroll or a tap) and exceeds a distance threshold (~50px).
- On a qualifying left/right swipe, calls `.click()` on the existing prev/next button
  (`window.parent.document.querySelector('.st-key-nav-prev button')` /
  `.st-key-nav-next button`, classes already established at `ui.py:158-172`) — reusing the exact
  existing button handlers and Streamlit rerun path. No month/year rollover logic is duplicated
  or reimplemented in JS.
- Only affects touch input; desktop mouse/keyboard interaction is unaffected.

**Known fragility (accepted):** this relies on Streamlit's current internal DOM structure and
`st-key-*` class naming, which are not a public API and could change in a future Streamlit
version. A one-line code comment documents this dependency so a future break is easy to diagnose.
This is the only feasible approach without introducing a full custom-component JS build pipeline,
which this project doesn't otherwise have.

## Out of scope

- A proper Streamlit custom component (JS/React build) for swipe handling — the DOM-reaching hack
  is deliberately chosen over adding a new build toolchain for one gesture.
- Any change to how `token`/`ntfy_topic` are generated or stored — both are already independent
  per subscription and already persisted; this spec only surfaces what already exists.
- Editing birthday or notification settings from the saved-link page beyond what already exists
  (the "What counts as a match" expander) — the birthday display is read-only by design.

## Testing

- Manual verification in the browser preview (mobile viewport) for all three: saved-link page
  shows birthday + expandable notification link; filter rows stay single-line at narrow widths;
  swipe left/right on the calendar changes month on a touch-emulated viewport.
- No new unit-testable logic is introduced (CSS, JS-in-iframe, and display-only Streamlit
  markup) — existing `pytest` suite should still pass unchanged and is run as a regression check.
