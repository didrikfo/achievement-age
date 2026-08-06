# Newspaper-Style Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the Streamlit app (`src/app/ui.py`) as an old-timey newspaper (masthead, serif type, hand-circled-in-red match markers) and fix a real mobile bug where the 7-column calendar grid collapses into a vertical list below ~640px viewport width.

**Architecture:** All CSS and static HTML fragments live in a new `src/app/styles.py` module, injected once via `st.markdown(..., unsafe_allow_html=True)`. `ui.py` keeps its existing control flow (same `st.button`/`st.dialog` interaction model) but swaps inline styles/emoji for CSS classes and wraps the calendar grid in a keyed `st.container` so CSS can target it specifically. No changes to `core/` or `ingest/` — this is a presentation-only change.

**Tech Stack:** Streamlit 1.45.1, plain CSS injected via `unsafe_allow_html=True`. No new dependencies.

## Global Constraints

- Light theme only. Dark theme is explicitly out of scope (deferred to a future pass).
- Font stack is `Georgia, 'Times New Roman', serif` everywhere — no external/Google Fonts (avoids a CDN dependency on Streamlit Community Cloud).
- Palette: background `#f2efe6`, ink `#1a1a1a`, accent `#a01f1f`.
- `st.container(key="calendar-grid")` produces a wrapper `<div>` with CSS class `st-key-calendar-grid` — verified live against Streamlit 1.45.1 in this repo's venv.
- Match-day buttons use `type="primary"` and render with `data-testid="stBaseButton-primary"`; all other buttons (`type` unset/`"secondary"`) render `data-testid="stBaseButton-secondary"` — verified live.
- Every match day must remain a real `st.button` (not a static div) so `show_event_dialog` still fires on click. Only non-match days (blank padding + plain + today-only) may be static `st.markdown` divs, matching current behavior.
- No change to `core.age`, `core.matching`, `core.db`, or any test in `tests/` — this plan touches only `src/app/ui.py` and the new `src/app/styles.py`. No test file currently imports `app.ui` (confirmed via grep), so `pytest` must still pass unmodified at the end.
- Verification in this plan is browser-based (via the project's Streamlit dev server, `.claude/launch.json` config `achievement-age-streamlit`, port 8517) rather than pytest, since this is a pure styling/layout change with no unit-testable logic. Each task's verification step gives exact DOM/computed-style assertions to check, not just "looks right".

---

### Task 1: `styles.py` scaffold + masthead + base typography

**Files:**
- Create: `src/app/styles.py`
- Modify: `src/app/ui.py:1-38` (imports + title/intro block)

**Interfaces:**
- Produces: `PAGE_CSS: str` (a `<style>...</style>` HTML string), `MASTHEAD_HTML: str` (masthead `<div>`/`<hr>` HTML string) — both consumed directly by `ui.py` via `st.markdown(X, unsafe_allow_html=True)`. Later tasks append more rules to `PAGE_CSS`'s string body in place (it stays one constant for the whole app).

- [ ] **Step 1: Create `src/app/styles.py` with base CSS and masthead HTML**

```python
"""CSS and static HTML fragments for the newspaper-style visual theme.

Injected once by ``ui.py`` via ``st.markdown(..., unsafe_allow_html=True)``.
Later sections of PAGE_CSS are appended by later tasks in this file; keep
this as the single source of styling for the app.
"""

from __future__ import annotations

PAGE_CSS = """
<style>
:root {
    --aa-bg: #f2efe6;
    --aa-ink: #1a1a1a;
    --aa-accent: #a01f1f;
}

[data-testid="stAppViewContainer"] {
    background-color: var(--aa-bg);
}
[data-testid="stAppViewContainer"] * {
    font-family: Georgia, 'Times New Roman', serif;
    color: var(--aa-ink);
}

/* Masthead */
.aa-masthead { text-align: center; margin-bottom: 4px; }
.aa-masthead-title {
    font-size: 34px; font-weight: 700; letter-spacing: .04em;
    text-transform: uppercase; margin: 0;
}
.aa-masthead-tagline { font-style: italic; font-size: 13px; opacity: .7; margin-top: 2px; }
.aa-rule-thick { border: none; border-top: 3px solid var(--aa-ink); margin: 10px 0 3px 0; }
.aa-rule-thin { border: none; border-top: 1px solid var(--aa-ink); margin: 0 0 14px 0; }

/* Age line accent */
.aa-age b { border-bottom: 1px solid var(--aa-accent); color: var(--aa-accent); }
</style>
"""

MASTHEAD_HTML = """
<div class="aa-masthead">
    <p class="aa-masthead-title">Achievement Age</p>
    <p class="aa-masthead-tagline">&mdash; a calendar of coincidences &mdash;</p>
</div>
<hr class="aa-rule-thick">
<hr class="aa-rule-thin">
"""
```

- [ ] **Step 2: Wire the CSS/masthead into `ui.py` and replace `st.title`**

In `src/app/ui.py`, add the import alongside the existing `core`/`ingest`-style absolute imports (after the `from core.matching import ...` line):

```python
from app.styles import MASTHEAD_HTML, PAGE_CSS
```

Replace:

```python
st.title("Achievement Age Calendar")

st.write(
    "Enter your birthday, then browse the calendar for days when you were "
    "(or will be) the same age as someone famous at a notable moment."
)
```

with:

```python
st.markdown(PAGE_CSS, unsafe_allow_html=True)
st.markdown(MASTHEAD_HTML, unsafe_allow_html=True)

st.write(
    "Enter your birthday, then browse the calendar for days when you were "
    "(or will be) the same age as someone famous at a notable moment."
)
```

Also replace the age-display line so the age numbers get the red-underline accent class:

```python
st.markdown(f"You are **{years} years, {months} months, and {days} days** old today.")
```

becomes:

```python
st.markdown(
    f"<p class='aa-age'>You are <b>{years} years, {months} months, and {days} days</b> old today.</p>",
    unsafe_allow_html=True,
)
```

- [ ] **Step 3: Verify in the browser**

Start the dev server (`.claude/launch.json` config `achievement-age-streamlit`, port 8517) and open it. Run in the page console (or via an equivalent DOM check):

```js
getComputedStyle(document.querySelector('[data-testid="stAppViewContainer"]')).backgroundColor
// expect: "rgb(242, 239, 230)"  (== #f2efe6)

document.querySelector('.aa-masthead-title').textContent
// expect: "Achievement Age"

getComputedStyle(document.querySelector('.aa-masthead-title')).fontFamily
// expect: starts with "Georgia"
```

Confirm no Python exceptions in the Streamlit server log/preview_logs, and that the birthday input / rest of the page below the masthead still renders (unstyled below this point — that's expected until later tasks).

- [ ] **Step 4: Commit**

```bash
git add src/app/styles.py src/app/ui.py
git commit -m "Add newspaper-style masthead and base typography"
```

---

### Task 2: Calendar grid — table borders, match-circle marker, today fill

**Files:**
- Modify: `src/app/styles.py` (append to `PAGE_CSS`)
- Modify: `src/app/ui.py:71, 139-172` (caption text, weekday header, day-cell rendering)

**Interfaces:**
- Consumes: `PAGE_CSS` from Task 1 (this task appends more CSS text to the same constant — edit the file, don't create a second constant).
- Produces: CSS classes `aa-cal-dow`, `aa-cal-cell`, `aa-cal-cell.aa-blank`, `aa-cal-cell.aa-today` and the `calendar-grid` container key, all consumed by `ui.py`'s calendar-rendering loop (this task) and reused unchanged by Task 3 (mobile) and Task 4 (nav).

- [ ] **Step 1: Append calendar-grid CSS to `src/app/styles.py`**

Insert this block before the closing `</style>` tag of `PAGE_CSS` (i.e. edit the existing string, don't add a second `<style>` block):

```css
/* Calendar grid: table borders */
.st-key-calendar-grid { border-top: 1px solid var(--aa-ink); border-left: 1px solid var(--aa-ink); }
.st-key-calendar-grid [data-testid="stColumn"] {
    border-right: 1px solid var(--aa-ink);
    border-bottom: 1px solid var(--aa-ink);
}

/* Day-of-week header cells */
.aa-cal-dow {
    text-align: center; font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
    padding: 5px 0; background: var(--aa-ink); color: var(--aa-bg);
}

/* Plain / blank / today calendar cells (non-interactive divs) */
.aa-cal-cell {
    aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
    font-size: 14px; width: 100%;
}
.aa-cal-cell.aa-blank {
    background: repeating-linear-gradient(135deg, transparent, transparent 4px, #1a1a1a08 4px, #1a1a1a08 5px);
}
.aa-cal-cell.aa-today { background: var(--aa-ink); color: var(--aa-bg); font-weight: 700; }

/* Match-day buttons (type="primary") get the hand-circled-in-red-pen mark */
.st-key-calendar-grid button[data-testid="stBaseButton-primary"] {
    position: relative; aspect-ratio: 1; width: 100%;
    background: transparent !important; border: none !important; box-shadow: none !important;
    color: var(--aa-accent) !important; font-weight: 700;
}
.st-key-calendar-grid button[data-testid="stBaseButton-primary"]::before {
    content: ''; position: absolute; top: 50%; left: 50%;
    width: 30px; height: 30px; margin: -15px 0 0 -15px;
    border: 2.5px solid var(--aa-accent); border-radius: 50%; transform: rotate(-8deg);
}

/* A day that is both today AND a match: black fill, still red-circled */
[class*="st-key-today-match-"] button[data-testid="stBaseButton-primary"] {
    background: var(--aa-ink) !important; color: var(--aa-bg) !important;
}
```

- [ ] **Step 2: Update `ui.py`'s caption text**

Replace:

```python
st.caption("⭐ marks a day that matches a historical event - click it for details. 🔵 marks today.")
```

with:

```python
st.caption("A red circle marks a day that matches a historical event — click it for details. A filled black date marks today.")
```

- [ ] **Step 3: Wrap the weekday header + day grid in the keyed container, and restyle cells**

Replace:

```python
weekday_cols = st.columns(7)
for col, weekday_name in zip(weekday_cols, calendar.day_abbr):
    col.markdown(f"**{weekday_name}**")

for week in calendar.monthcalendar(view_year, view_month):
    week_cols = st.columns(7)
    for col, day in zip(week_cols, week):
        if day == 0:
            col.write("")
            continue

        day_date = date(view_year, view_month, day)
        age_days = (day_date - birthdate).days
        day_matches = EVENTS_BY_AGE.get(age_days, [])
        is_today = day_date == today

        if day_matches:
            label = f"{day} ⭐" + (" \U0001f535" if is_today else "")
            if col.button(
                label,
                key=f"day_{view_year}_{view_month}_{day}",
                type="primary",
                use_container_width=True,
            ):
                show_event_dialog(day_date, day_matches)
        elif is_today:
            col.markdown(
                "<div style='text-align:center; border-radius:50%; "
                "background-color:#1c83e1; color:white; padding:4px 0;'>"
                f"{day}</div>",
                unsafe_allow_html=True,
            )
        else:
            col.markdown(f"<div style='text-align:center;'>{day}</div>", unsafe_allow_html=True)
```

with:

```python
with st.container(key="calendar-grid"):
    weekday_cols = st.columns(7)
    for col, weekday_name in zip(weekday_cols, calendar.day_abbr):
        col.markdown(f"<div class='aa-cal-dow'>{weekday_name}</div>", unsafe_allow_html=True)

    for week in calendar.monthcalendar(view_year, view_month):
        week_cols = st.columns(7)
        for col, day in zip(week_cols, week):
            if day == 0:
                col.markdown("<div class='aa-cal-cell aa-blank'></div>", unsafe_allow_html=True)
                continue

            day_date = date(view_year, view_month, day)
            age_days = (day_date - birthdate).days
            day_matches = EVENTS_BY_AGE.get(age_days, [])
            is_today = day_date == today

            if day_matches and is_today:
                with col.container(key=f"today-match-{view_year}-{view_month}-{day}"):
                    if st.button(
                        str(day),
                        key=f"day_{view_year}_{view_month}_{day}",
                        type="primary",
                        use_container_width=True,
                    ):
                        show_event_dialog(day_date, day_matches)
            elif day_matches:
                if col.button(
                    str(day),
                    key=f"day_{view_year}_{view_month}_{day}",
                    type="primary",
                    use_container_width=True,
                ):
                    show_event_dialog(day_date, day_matches)
            elif is_today:
                col.markdown(f"<div class='aa-cal-cell aa-today'>{day}</div>", unsafe_allow_html=True)
            else:
                col.markdown(f"<div class='aa-cal-cell'>{day}</div>", unsafe_allow_html=True)
```

Note: only the cell that is simultaneously today AND a match gets an extra `st.container(key=...)` wrapper — this is the one edge case that needs a distinct CSS hook (`today-match` selector in Step 1) so it can be both black-filled and red-circled. Ordinary match-only days keep calling `col.button(...)` directly, same as the original code.

- [ ] **Step 4: Verify in the browser**

Reload the app (desktop width). Find a day that has a match (any `⭐`-worthy day under the old caption — check `EVENTS_BY_AGE` for the current test birthdate, or temporarily pick a birthdate known to produce a match this month). Run:

```js
const btn = document.querySelector('.st-key-calendar-grid button[data-testid="stBaseButton-primary"]');
getComputedStyle(btn, '::before').borderColor
// expect: "rgb(160, 31, 31)"  (== #a01f1f)
getComputedStyle(btn, '::before').borderRadius
// expect: "50%"
```

Also confirm the day-of-week row renders as a dark bar (`getComputedStyle(document.querySelector('.aa-cal-dow')).backgroundColor` → `"rgb(26, 26, 26)"`), and that clicking a circled day still opens the event dialog (unchanged behavior — click it and confirm `show_event_dialog` content appears).

- [ ] **Step 5: Commit**

```bash
git add src/app/styles.py src/app/ui.py
git commit -m "Restyle calendar grid as a bordered table with red-circle match markers"
```

---

### Task 3: Mobile fix — keep the grid 7-wide below 640px

**Files:**
- Modify: `src/app/styles.py` (append to `PAGE_CSS`)

**Interfaces:**
- Consumes: `.st-key-calendar-grid` scope and cell classes from Task 2 — no `ui.py` changes in this task, CSS-only.

- [ ] **Step 1: Append the mobile-fix and responsive rules to `PAGE_CSS`**

Root cause (confirmed live against this repo's Streamlit 1.45.1 at 375px width): Streamlit sets `min-width: calc(100% - 24px)` on `[data-testid="stColumn"]` below its internal breakpoint, which forces every column to full row width. Additionally, Streamlit's default 16px inter-column gap doesn't fit 7 columns in ~343px of usable width even if min-width is removed — so the gap must also go to 0 (which suits the "bordered table" look anyway, where cells should touch, not float apart). Both fixes were verified live in a throwaway probe script before writing this plan: at 375px, all 7 columns render at 44-49px each in a single row with these rules applied.

Append inside `PAGE_CSS`, before `</style>`:

```css
/* Mobile fix: keep the calendar grid 7-wide at any viewport width.
   Streamlit stacks stColumn to full-width below ~640px by default. */
.st-key-calendar-grid [data-testid="stHorizontalBlock"] {
    gap: 0 !important;
    flex-wrap: nowrap !important;
}
.st-key-calendar-grid [data-testid="stColumn"] {
    min-width: 0 !important;
    flex: 1 1 0 !important;
}

@media (max-width: 480px) {
    .aa-masthead-title { font-size: 24px; }
    .aa-cal-cell,
    .st-key-calendar-grid button[data-testid="stBaseButton-primary"] {
        font-size: 12px;
    }
    .st-key-calendar-grid button[data-testid="stBaseButton-primary"]::before {
        width: 24px; height: 24px; margin: -12px 0 0 -12px; border-width: 2px;
    }
}
```

- [ ] **Step 2: Verify in the browser at 375px width**

Resize the preview viewport to 375x812 (mobile preset) and reload. Run:

```js
const cols = document.querySelectorAll('.st-key-calendar-grid [data-testid="stColumn"]');
// Take one full week's worth (7 consecutive columns after the 7 dow-header columns)
const week = Array.from(cols).slice(7, 14).map(c => {
    const r = c.getBoundingClientRect();
    return { w: Math.round(r.width), x: Math.round(r.x), y: Math.round(r.y) };
});
JSON.stringify(week);
```

Expected: all 7 entries have the same `y` value (same row) and strictly increasing `x` values with no gaps or overlaps — i.e. the grid did not stack into a vertical list. This matches the exact check used during brainstorming (verified there with a standalone probe: 7 columns at ~44-49px each, same row).

- [ ] **Step 3: Commit**

```bash
git add src/app/styles.py
git commit -m "Fix calendar grid collapsing to a vertical list on mobile widths"
```

---

### Task 4: Nav arrows and remaining widgets (date input, selects, expander, button)

**Files:**
- Modify: `src/app/styles.py` (append to `PAGE_CSS`)
- Modify: `src/app/ui.py:100-112` (nav prev/next buttons)

**Interfaces:**
- Consumes: `stBaseButton-secondary` testid (verified in Global Constraints) for the nav arrows; `[data-testid="stDateInput"]`, `[data-testid="stSelectbox"]`, `[data-testid="stExpander"]` (all verified live against this repo's Streamlit version) for the generic widget restyle.

- [ ] **Step 1: Append widget CSS to `PAGE_CSS`**

```css
/* Nav prev/next arrows: minimal, no button chrome */
.st-key-nav-prev button[data-testid="stBaseButton-secondary"],
.st-key-nav-next button[data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-size: 15px;
    opacity: .6;
}

/* Birthday date input, month/year selects, notify expander: thin ink borders */
[data-testid="stDateInput"] [data-baseweb="base-input"],
[data-testid="stSelectbox"] [data-baseweb="select"] > div {
    border: 1px solid var(--aa-ink) !important;
    border-radius: 2px !important;
    box-shadow: none !important;
}
[data-testid="stExpander"] {
    border: 1px solid var(--aa-ink) !important;
    border-radius: 2px !important;
}
```

- [ ] **Step 2: Wrap the nav prev/next buttons in keyed containers in `ui.py`**

Replace:

```python
with nav_prev:
    if st.button("◀", use_container_width=True):
        st.session_state.view_month -= 1
        if st.session_state.view_month < 1:
            st.session_state.view_month = 12
            st.session_state.view_year -= 1

with nav_next:
    if st.button("▶", use_container_width=True):
        st.session_state.view_month += 1
        if st.session_state.view_month > 12:
            st.session_state.view_month = 1
            st.session_state.view_year += 1
```

with:

```python
with nav_prev:
    with st.container(key="nav-prev"):
        if st.button("◀", use_container_width=True):
            st.session_state.view_month -= 1
            if st.session_state.view_month < 1:
                st.session_state.view_month = 12
                st.session_state.view_year -= 1

with nav_next:
    with st.container(key="nav-next"):
        if st.button("▶", use_container_width=True):
            st.session_state.view_month += 1
            if st.session_state.view_month > 12:
                st.session_state.view_month = 1
                st.session_state.view_year += 1
```

- [ ] **Step 3: Verify in the browser**

```js
const prevBtn = document.querySelector('.st-key-nav-prev button');
getComputedStyle(prevBtn).backgroundColor
// expect: "rgba(0, 0, 0, 0)" (transparent)

const dateBorder = document.querySelector('[data-testid="stDateInput"] [data-baseweb="base-input"]');
getComputedStyle(dateBorder).borderColor
// expect: "rgb(26, 26, 26)" (== #1a1a1a)
```

Also click prev/next a couple of times to confirm month navigation still works exactly as before (no functional change intended, styling only).

- [ ] **Step 4: Commit**

```bash
git add src/app/styles.py src/app/ui.py
git commit -m "Restyle nav arrows, birthday input, selects, and notify expander"
```

---

### Task 5: Full QA pass

**Files:** none (verification only, plus removing the gitignored scratch state if any leaked in).

- [ ] **Step 1: Run the existing test suite to confirm no regressions**

```bash
pytest
```

Expected: all tests pass, same count as before this plan (this plan touches no code any test imports).

- [ ] **Step 2: Desktop visual pass**

Open the app at desktop width (1280px). Confirm against the approved spec (`docs/superpowers/specs/2026-08-06-newspaper-style-design.md`):
- Masthead reads "ACHIEVEMENT AGE" with italic tagline and double rule beneath it
- Calendar renders as a bordered table, day-of-week row inverted (black bg)
- At least one match day shows the thick red circle around a bold red date number
- Today (non-match) shows solid black fill
- Blank leading/trailing cells show the diagonal hatch texture
- Clicking a circled day opens the event dialog with correct content (spot-check one)

- [ ] **Step 3: Mobile visual pass**

Resize to 375x812. Confirm:
- Calendar grid stays 7 columns wide, no stacking (re-run the Task 3 Step 2 check if in doubt)
- Masthead title and cell numbers are legible, not overflowing/wrapping awkwardly
- Nav row (prev/month select/year select/next) is usable, even if stacked vertically (acceptable per spec)
- Tap targets on match-day buttons are reasonably sized (not sliver-thin)

- [ ] **Step 4: Confirm no leftover scratch artifacts**

```bash
git status
```

Expected: only the files touched by Tasks 1-4 show as committed; `.superpowers/` remains gitignored and untracked (added to `.gitignore` during brainstorming, already committed).

- [ ] **Step 5: Final commit if any fixups were needed during QA**

If Steps 2-3 surfaced any small fixes, commit them:

```bash
git add src/app/styles.py src/app/ui.py
git commit -m "Fix visual QA findings from newspaper-style redesign pass"
```

If nothing needed fixing, skip this step — Task 4's commit is the last one.
