# Newspaper-style visual redesign

**Status:** Approved 2026-08-06, ready for implementation planning.

## Purpose

The app (`src/app/ui.py`) currently uses stock Streamlit styling: default theme, `st.title`, plain buttons, and one inline hex color (`#1c83e1`) for the "today" marker. This spec redesigns the visual identity around an old-timey newspaper feel, and fixes a real mobile bug uncovered during the design pass: Streamlit collapses the 7-column calendar grid into a vertical list of stacked buttons below ~640px viewport width.

Scope: light theme only. A dark "archive" theme was explored and looks workable, but is explicitly deferred — not part of this pass.

## Visual direction

**Masthead-style header**, replacing `st.title`:
- Title "ACHIEVEMENT AGE" — bold serif, uppercase, letter-spaced (~0.04em)
- Italic tagline underneath: "— a calendar of coincidences —"
- Double horizontal rule below (thick 3px + thin 1px, both black), newspaper-masthead style

**Typography:** serif throughout (`Georgia, 'Times New Roman', serif` stack — no external font loading, so no CDN dependency/latency on Community Cloud). This applies to headings, body text, and the calendar grid alike; it's a deliberate departure from the earlier "serif headline + sans body" idea explored mid-brainstorm — the user's final direction was uniformly serif/newsprint.

**Palette (light only):**
- Background (newsprint): `#f2efe6`
- Ink (text/borders): `#1a1a1a`
- Accent (matches, "red pen"): `#a01f1f`

**Calendar grid:**
- Table-style with 1px black borders on every cell (`border-top`/`border-left` on the grid, `border-right`/`border-bottom` per cell) — reads like a printed table, not rounded "cards"
- Day-of-week header row: inverted (black background, newsprint-colored text), small-caps, letter-spaced
- Blank leading/trailing cells (days outside the month): subtle diagonal hatch texture (`repeating-linear-gradient`) instead of empty space
- **Match-day marker:** a hand-circled-in-red-pen mark — `2.5px solid #a01f1f` circle, ~30px diameter on desktop, tilted slightly (`rotate(-8deg)`) for a hand-drawn feel; the date number inside is bold and colored the same red. This was chosen after comparing against asterisk, underline, corner-stamp, dagger, and boxed-border alternatives — circle won on both rounds.
- **Today marker:** solid black fill with newsprint-colored bold text — visually distinct from the red match circle (no combined star+dot as in the old design)

**Navigation row:** minimal arrow glyphs (‹ ›, not full buttons) flanking the bold letter-spaced month/year (e.g. "MARCH 1873"), centered.

**Other widgets** (birthday date input, "Get notified" expander/button, month/year selects): restyled via scoped CSS to use serif type and thin black borders instead of Streamlit's default rounded/colored chrome, so they read as part of the same newsprint page rather than a generic web form. No functional changes to these widgets.

## Mobile fix (structural, not cosmetic)

**Root cause confirmed live** by inspecting the running app at 375px width: Streamlit applies `min-width: calc(100% - 24px)` to `[data-testid="stColumn"]` below its internal responsive breakpoint, which forces every column — including each day of the calendar — to full row width. The result: a month view becomes ~40 full-width stacked buttons instead of a 7-wide grid.

**Fix:** wrap the day-row `st.columns(7)` calls in `st.container(key="calendar-grid")` (Streamlit ≥1.31 gives keyed containers a stable `.st-key-calendar-grid` CSS class). Inject scoped CSS that overrides Streamlit's own responsive rule within that class only:

```css
.st-key-calendar-grid [data-testid="stColumn"] {
    min-width: 0 !important;
    flex-basis: calc(14.2857% - 6px) !important;
}
```

This keeps every day cell as a real `st.button` (so the existing `st.dialog` click-to-view-event flow needs no changes) while forcing the grid to stay 7-wide at any viewport width. Only the calendar's day-grid rows get this override — the nav row (prev/month/year/next) and weekday-header row are unaffected and can keep Streamlit's normal responsive behavior (they already read fine stacked or full-width, and changing them is out of scope).

At mobile width, cell font size and the match-circle diameter shrink slightly (circle ~24px vs ~30px desktop) via a `@media (max-width: 480px)` rule, confirmed in the mockup to keep tap targets comfortably above the ~44px guideline.

## Implementation approach

- All styling via one CSS block injected once near the top of `src/app/ui.py` with `st.markdown(..., unsafe_allow_html=True)` — no `.streamlit/config.toml` theme changes, since the masthead/grid look needs more control than Streamlit's theme config exposes.
- Replace `st.title(...)` with the custom masthead HTML block.
- Replace the inline `#1c83e1` today-circle styling and the `⭐`/`🔵` emoji-in-button-label approach with the new CSS-class-based cell rendering (still real `st.button`s for match days so `on_click`/dialog behavior is unchanged; today-only cells stay non-interactive `st.markdown` as they are now, just restyled).
- Wrap the day-grid loop's `st.columns(7)` call in the keyed container for the min-width override.

## Explicitly out of scope

- Dark theme (explored, deferred — revisit later as a separate pass using the "archive" palette explored during brainstorming: `#1e1b16` background, `#ece4d3` text, `#d1a24a` accent)
- Any change to the event-matching logic, data model, or `st.dialog` content — purely visual/layout
- Restyling Streamlit's own chrome (top toolbar, hamburger menu, "Deploy" button)
