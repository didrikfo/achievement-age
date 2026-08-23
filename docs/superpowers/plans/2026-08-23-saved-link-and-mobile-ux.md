# Saved-link Visibility and Mobile UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a returning subscriber their birthday and (on demand) their notification link, fix the filter panel's mobile column wrapping, and add swipe-to-navigate-month on the calendar for touch devices.

**Architecture:** Three independent, additive changes to the existing single-page Streamlit app (`src/app/ui.py`), its stylesheet (`src/app/styles.py`), and one new small module (`src/app/swipe.py`) for the swipe-navigation script. No new dependencies, no schema changes — everything needed (`token`, `ntfy_topic`, `birthday`) is already persisted per subscription and already read by `get_subscription()`.

**Tech Stack:** Python 3, Streamlit, Supabase (via `supabase-py`), plain CSS injected as a string, one vanilla-JS snippet injected via `streamlit.components.v1.html`.

## Global Constraints

- No new pip dependencies — Streamlit, Supabase client, and stdlib only (per `requirements.txt` conventions already in place).
- Every interactive control in the filter panel stays a plain `st.button`, never a stateful widget — established convention in `filters.py`'s module docstring; not touched by this plan but must not be violated by any change near it.
- New CSS selectors follow the existing `st-key-*` class-name convention (`app/styles.py`), matching on `[class*="st-key-<name>-"]` where the key already encodes state digits, so a rule can be written once without enumerating every state.
- The swipe script's dependency on Streamlit's internal DOM structure (`st-key-*` classes, `window.parent.document` access) is accepted as fragile per the spec — documented with a code comment, not engineered around.
- No editing of birthday or notification settings is introduced beyond what already exists (the "What counts as a match" expander) — the birthday display added in Task 1 is read-only.

---

## File Structure

- **Modify:** `src/app/ui.py` — returning-subscriber branch (currently lines 53–55) gets a birthday heading + notification-link expander; a new `render_swipe_nav()` call is added once near the calendar section.
- **Modify:** `src/app/styles.py` — one new CSS rule for `.aa-birthday` (mirroring the existing `.aa-age` accent style), and one new CSS block giving filter-panel rows the same mobile `nowrap` treatment the calendar grid already has.
- **Create:** `src/app/swipe.py` — holds the swipe-detection JS as a string constant and a `render_swipe_nav()` function that injects it via `streamlit.components.v1.html`. Kept out of `styles.py` (which is documented as "the single source of styling," not behavior) and out of `ui.py` (keeps the hack's fragility isolated to one small, clearly-labeled file, matching how `filters.py` and `links.py` are already split out by responsibility).

---

### Task 1: Saved-link page — birthday header + notification-link expander

**Files:**
- Modify: `src/app/ui.py:53-55`
- Modify: `src/app/styles.py` (insert after line 43, the existing `.aa-age b` rule)

**Interfaces:**
- Consumes: `subscription: Optional[Dict]` already resolved at `ui.py:51` via `core.db.get_subscription(token)`, with keys `birthday` (ISO date string), `token`, `ntfy_topic`. `subscription_link(token: str) -> str` already imported from `app.links` at `ui.py:30`.
- Produces: nothing consumed by later tasks — fully self-contained.

There is no automated test target here — `ui.py` is Streamlit script-level rendering code with no existing unit-test coverage (no `tests/test_ui.py` exists in the repo), so verification is manual, in the browser, per the steps below.

- [ ] **Step 1: Add the `.aa-birthday` CSS rule**

In `src/app/styles.py`, right after line 43 (`.aa-age b { border-bottom: 1px solid var(--aa-accent); color: var(--aa-accent); }`), add:

```css
.aa-birthday b { border-bottom: 1px solid var(--aa-accent); color: var(--aa-accent); }
```

- [ ] **Step 2: Replace the returning-subscriber branch in `ui.py`**

Replace `ui.py:53-55`:

```python
if subscription:
    birthdate = date.fromisoformat(subscription["birthday"])
    st.caption("Welcome back — this link remembers your birthday.")
```

with:

```python
if subscription:
    birthdate = date.fromisoformat(subscription["birthday"])
    st.markdown(
        f"<p class='aa-birthday'>Born <b>{birthdate.strftime('%B %d, %Y')}</b></p>",
        unsafe_allow_html=True,
    )
    with st.expander("Show my notification link"):
        link = subscription_link(subscription["token"])
        st.code(link, language=None)
        st.markdown(
            f"If you haven't already, install the [ntfy app](https://ntfy.sh) and "
            f"subscribe to the topic `{subscription['ntfy_topic']}` to get notified."
        )
```

- [ ] **Step 3: Start the app and create a test subscription**

Run: `venv/Scripts/streamlit.exe run src/app/ui.py --server.headless true --server.port 8517` (or use the `achievement-age-streamlit` preview config already defined in `.claude/launch.json`).

In the browser, load the app with no `?u=` token, enter a birthday, open "Get notified when your age matches an event", click "Get notified". Copy the link shown (e.g. `http://localhost:8517/?u=<token>`) and note the `ntfy_topic` shown in the instructions below it.

- [ ] **Step 4: Verify the returning-subscriber view**

Navigate to the copied `?u=<token>` link. Confirm:
- The page shows "Born <the birthday you entered>" at the top, styled like the existing "You are X years..." age line (bold with the red accent underline), with no date-input widget present anywhere on the page.
- A collapsed "Show my notification link" expander is present. Opening it shows `st.code` containing the exact same link you navigated to, and the same `ntfy_topic` you noted in Step 3.

- [ ] **Step 5: Commit**

```bash
git add src/app/ui.py src/app/styles.py
git commit -m "feat: show birthday and notification link on the saved-link page"
```

---

### Task 2: Filter-panel mobile fix

**Files:**
- Modify: `src/app/styles.py` (insert inside the "Filter panel" section, after the section-header comment at line 158)

**Interfaces:**
- Consumes: the existing `st-key-filter-row-*` class naming from `filters.py:73-75` (`_row_key`) — no Python changes needed, since the container keys already exist and only need a new CSS rule to match on them.
- Produces: nothing consumed by later tasks — fully self-contained, pure CSS.

No automated test — this is a pure CSS layout fix. Verification is a mobile-viewport screenshot comparison, mirroring how the calendar grid's own mobile fix (`styles.py:107-116`) would be verified.

- [ ] **Step 1: Reproduce the bug**

With the app running (from Task 1, Step 3, or restart it: `venv/Scripts/streamlit.exe run src/app/ui.py --server.headless true --server.port 8517`), resize the browser viewport to a mobile width (375px, e.g. the `mobile` preset) and open the "What counts as a match" expander. Screenshot it. Confirm the label / tag-popover / bell for at least one row are wrapping onto separate lines instead of staying on one row.

- [ ] **Step 2: Add the mobile CSS fix**

In `src/app/styles.py`, right after line 158 (`/* ---- Filter panel ---------------------------------------------------- */`), insert:

```css
/* Mobile fix: keep each row's label/tags/bell on one line. Same fix as the
   calendar grid above - Streamlit stacks stColumn to full-width below
   ~640px by default. */
[class*="st-key-filter-row-"] [data-testid="stHorizontalBlock"] {
    gap: 0 !important;
    flex-wrap: nowrap !important;
}
[class*="st-key-filter-row-"] [data-testid="stColumn"] {
    min-width: 0 !important;
    flex: 1 1 0 !important;
}
```

- [ ] **Step 3: Verify the fix**

Reload the app in the browser (Streamlit hot-reloads on file save, or restart it), keep the viewport at mobile width, reopen "What counts as a match". Screenshot it again. Confirm every row's label, tag-popover trigger (where present), and bell now stay on a single line, matching the desktop layout's proportions. Also re-check desktop width (e.g. 1280px) to confirm the panel is visually unchanged there.

- [ ] **Step 4: Commit**

```bash
git add src/app/styles.py
git commit -m "fix: keep filter panel rows on one line at mobile widths"
```

---

### Task 3: Calendar swipe navigation

**Files:**
- Create: `src/app/swipe.py`
- Modify: `src/app/ui.py` (import + one call site, after the calendar grid renders — end of file, after line 243)

**Interfaces:**
- Consumes: the existing `.st-key-calendar-grid` class (`styles.py:107`, `ui.py:202`) and the existing `.st-key-nav-prev` / `.st-key-nav-next` button containers (`ui.py:158-172`) — no changes to any of them.
- Produces: `render_swipe_nav() -> None` in `app.swipe`, called once from `ui.py`. Nothing else depends on it.

No automated test — this is a browser-side JS hack with no Python logic to unit test. Verification is manual, using touch-emulated drag gestures in the browser preview.

- [ ] **Step 1: Create `src/app/swipe.py`**

```python
"""Touch-swipe month navigation for the calendar, injected as raw JS.

Streamlit has no client-side gesture API and this project has no JS build
pipeline for a proper custom component, so this reaches across the
components iframe into the parent document (same-origin, so accessible)
and clicks the existing prev/next nav buttons on a qualifying swipe. See
docs/superpowers/specs/2026-08-23-saved-link-and-mobile-ux-design.md for
why this approach was chosen over a custom component build.

FRAGILE: depends on Streamlit's current internal DOM structure and the
`st-key-*` class naming it generates for keyed containers, neither of
which is a public API. If a future Streamlit version renames or
restructures these, swipe navigation silently stops working (the prev/next
buttons themselves are untouched, so tap navigation keeps working either
way).
"""

from __future__ import annotations

import streamlit.components.v1 as components

SWIPE_JS = """
<script>
(function () {
    function bindOnce(grid) {
        if (grid.dataset.aaSwipeBound) return;
        grid.dataset.aaSwipeBound = "1";
        var startX = 0, startY = 0;
        grid.addEventListener('touchstart', function (e) {
            startX = e.changedTouches[0].clientX;
            startY = e.changedTouches[0].clientY;
        }, { passive: true });
        grid.addEventListener('touchend', function (e) {
            var dx = e.changedTouches[0].clientX - startX;
            var dy = e.changedTouches[0].clientY - startY;
            if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) {
                return;
            }
            var doc = window.parent.document;
            var selector = dx < 0 ? '.st-key-nav-next button' : '.st-key-nav-prev button';
            var target = doc.querySelector(selector);
            if (target) target.click();
        }, { passive: true });
    }
    function tryBind() {
        var grid = window.parent.document.querySelector('.st-key-calendar-grid');
        if (grid) {
            bindOnce(grid);
            return true;
        }
        return false;
    }
    if (!tryBind()) {
        var poll = window.parent.setInterval(function () {
            if (tryBind()) window.parent.clearInterval(poll);
        }, 300);
    }
})();
</script>
"""


def render_swipe_nav() -> None:
    """Inject the swipe-to-navigate script once per page load."""
    components.html(SWIPE_JS, height=0)
```

- [ ] **Step 2: Wire it into `ui.py`**

Add to the import block near the other `app.*` imports (after `ui.py:30`):

```python
from app.swipe import render_swipe_nav
```

At the end of `ui.py`, right after the calendar grid's `with st.container(key="calendar-grid"):` block closes (i.e. after line 243, at the top level of the module), add:

```python
render_swipe_nav()
```

- [ ] **Step 3: Verify on desktop first (no regression)**

Restart or reload the app. On a normal desktop-width viewport, confirm the calendar renders exactly as before (no visible change — the injected component has `height=0`), and that clicking the existing ‹ / › buttons still navigates months normally.

- [ ] **Step 4: Verify swipe on a touch-emulated mobile viewport**

Resize the browser viewport to the `mobile` preset (this enables touch-event emulation). Reload the page so the mobile device gate re-runs. Perform a horizontal drag gesture starting inside the calendar grid (left-to-right, and separately right-to-left), each covering at least ~100px. Confirm each drag changes the displayed month in the expected direction (matching what clicking › or ‹ would do), and that the month/year heading text updates accordingly. Also confirm a vertical drag (scroll gesture) inside the grid does *not* change the month.

If the browser preview tool's touch emulation does not dispatch real `touchstart`/`touchend` events for a drag gesture, note this limitation explicitly rather than claiming the swipe was verified — in that case, fall back to confirming the script binds without JS errors (check the browser console for exceptions) and defer full swipe verification to a real touch device.

- [ ] **Step 5: Commit**

```bash
git add src/app/swipe.py src/app/ui.py
git commit -m "feat: add swipe-to-navigate-month on the calendar for touch devices"
```

---

### Task 4: Full regression check

**Files:** none (verification only)

**Interfaces:** none — this task confirms Tasks 1–3 together didn't break anything else.

- [ ] **Step 1: Run the full automated test suite**

Run: `venv/Scripts/pytest.exe` (or `pytest` if the venv is already activated) from the repo root.

Expected: all tests pass, same as before this plan (no test files were touched by Tasks 1–3, and no existing behavior they cover — matching, preferences, sequences, db, notifications — was changed).

- [ ] **Step 2: One combined manual pass**

With the app running, at a mobile viewport width: load the app fresh (no token) and confirm nothing regressed on the new-visitor flow (birthday input, "Get notified" expander still work as before). Then revisit via a saved `?u=` link and confirm the birthday header, notification-link expander, single-line filter rows, and swipe navigation all work together on the same page load.

- [ ] **Step 3: Final commit (if Step 2 needed any fixes)**

Only if Step 2 surfaced an issue requiring a code change:

```bash
git add -A
git commit -m "fix: address regression found in combined manual pass"
```

If no fixes were needed, skip this step — Task 3's commit is the last one.
