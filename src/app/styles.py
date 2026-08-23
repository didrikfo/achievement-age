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
/* Streamlit renders icons (e.g. the expander's toggle chevron) as ligature
   text in this font - the blanket font-family override above breaks the
   ligature and shows the raw icon name ("keyboard_arrow_down") instead. */
[data-testid="stAppViewContainer"] [data-testid="stIconMaterial"] {
    font-family: 'Material Symbols Rounded' !important;
}

/* Masthead */
.aa-masthead { text-align: center; margin-bottom: 4px; }
.aa-masthead-title {
    font-size: 34px !important; font-weight: 700; letter-spacing: .04em;
    text-transform: uppercase; margin: 0;
}
.aa-masthead-tagline { font-style: italic; font-size: 13px !important; opacity: .7; margin-top: 2px; }
.aa-rule-thick { border: none; border-top: 3px solid var(--aa-ink); margin: 10px 0 3px 0; }
.aa-rule-thin { border: none; border-top: 1px solid var(--aa-ink); margin: 0 0 14px 0; }

/* Age line accent */
.aa-age b { border-bottom: 1px solid var(--aa-accent); color: var(--aa-accent); }
.aa-birthday b { border-bottom: 1px solid var(--aa-accent); color: var(--aa-accent); }

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

/* Day buttons: shared geometry for any marked day, whichever mark it carries. */
.st-key-calendar-grid button[data-testid="stBaseButton-primary"] {
    position: relative; aspect-ratio: 1; width: 100%;
    background: transparent !important; border: none !important; box-shadow: none !important;
    font-weight: 700;
}
.st-key-calendar-grid button[data-testid="stBaseButton-primary"],
.st-key-calendar-grid button[data-testid="stBaseButton-primary"] * { color: var(--aa-accent) !important; }

/* Historical match: the hand-circled-in-red-pen mark. Scoped to the marker
   class rather than to every primary button, so an anniversary-only day
   doesn't inherit a circle it hasn't earned. */
[class*="st-key-mark-event-1-"] button[data-testid="stBaseButton-primary"]::before {
    content: ''; position: absolute; top: 50%; left: 50%;
    width: 30px; height: 30px; margin: -15px 0 0 -15px;
    border: 2.5px solid var(--aa-accent); border-radius: 50%; transform: rotate(-8deg);
}

/* Mathematical anniversary: a triangle drawn around the same centre, so a day
   carrying both marks gets both superimposed with no extra element. An SVG data
   URI rather than the CSS border trick, which can only produce a FILLED
   triangle - the mark has to be an outline to read as the same pen as the
   circle. The stroke colour is baked in because a data URI can't reference
   --aa-accent; keep %23a01f1f in step with it. */
[class*="st-key-mark-anniv-1-"] button[data-testid="stBaseButton-primary"]::after {
    content: ''; position: absolute; top: 50%; left: 50%;
    width: 36px; height: 36px; margin: -19px 0 0 -18px;
    background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Cpolygon points='20,4 36,34 4,34' fill='none' stroke='%23a01f1f' stroke-width='2.5' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat;
    transform: rotate(3deg);
    pointer-events: none;
}

/* A marked day that is also today: black fill, marks still in red. */
[class*="st-key-mark-today-1-"] button[data-testid="stBaseButton-primary"] {
    background: var(--aa-ink) !important;
}
[class*="st-key-mark-today-1-"] button[data-testid="stBaseButton-primary"],
[class*="st-key-mark-today-1-"] button[data-testid="stBaseButton-primary"] * { color: var(--aa-bg) !important; }

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
    .aa-masthead-title { font-size: 24px !important; }
    .aa-cal-cell,
    .st-key-calendar-grid button[data-testid="stBaseButton-primary"] {
        font-size: 12px;
    }
    [class*="st-key-mark-event-1-"] button[data-testid="stBaseButton-primary"]::before {
        width: 24px; height: 24px; margin: -12px 0 0 -12px; border-width: 2px;
    }
    [class*="st-key-mark-anniv-1-"] button[data-testid="stBaseButton-primary"]::after {
        width: 30px; height: 30px; margin: -16px 0 0 -15px;
    }
}

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
[data-testid="stSelectbox"] [role="group"] {
    border: 1px solid var(--aa-ink) !important;
    border-radius: 2px !important;
    box-shadow: none !important;
}
[data-testid="stExpander"] {
    border: 1px solid var(--aa-ink) !important;
    border-radius: 2px !important;
}

.aa-cal-heading {
    font-weight: 700; letter-spacing: .05em; text-align: center; margin: 0 0 10px 0;
}

/* ---- Filter panel ---------------------------------------------------- */

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

/* Rows are flat buttons, not chrome: the panel should read as a list. */
[class*="st-key-filter-row-"] button[data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    text-align: left !important;
    justify-content: flex-start !important;
    padding: 2px 4px !important;
    min-height: 0 !important;
}

/* A row that is off: dimmed and struck through, so it reads as "you turned this
   off" rather than "this is unavailable". Both digits zero. */
[class*="st-key-filter-row-00-"] button[data-testid="stBaseButton-secondary"] {
    opacity: .34;
}
[class*="st-key-filter-row-00-"] button[data-testid="stBaseButton-secondary"] p {
    text-decoration: line-through;
    text-decoration-thickness: 1px;
}

/* Marked rows carry their name in full weight. */
[class*="st-key-filter-row-11-"] button[data-testid="stBaseButton-secondary"] p,
[class*="st-key-filter-row-10-"] button[data-testid="stBaseButton-secondary"] p {
    font-weight: 600;
}

/* Bells live in their own keyed container, so they can be targeted without a
   positional selector - the label and the bell sit in different st.columns, and
   each is the only button in its own column. */
[class*="st-key-filter-bell-"] button[data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 2px !important;
    min-height: 0 !important;
}

/* The bell for a row that is off the calendar: dim, but still clickable - it
   turns the row on. */
[class*="st-key-filter-bell-00-"] button[data-testid="stBaseButton-secondary"] {
    opacity: .25;
}

/* Marked but muted: a red slash across the bell, in the same ink as the
   calendar's circle. Drawn rather than swapped to the 🔕 glyph, which renders
   grey on every platform and so reads as "disabled" instead of "deliberately
   muted". The pale outline keeps it legible over the emoji beneath.
   FALLBACK: if the overlay proves fragile across platforms, delete this rule
   and render the label as 🔕 in filters.py instead - it is a local change. */
[class*="st-key-filter-bell-10-"] button[data-testid="stBaseButton-secondary"] {
    position: relative;
}
[class*="st-key-filter-bell-10-"] button[data-testid="stBaseButton-secondary"]::after {
    content: '';
    position: absolute; top: 50%; left: 50%;
    width: 22px; height: 2px; margin: -1px 0 0 -11px;
    background: var(--aa-accent);
    box-shadow: 0 0 0 1px rgba(242, 239, 230, .75);
    border-radius: 2px;
    transform: rotate(-45deg);
    pointer-events: none;
}

/* Tag popover trigger: a quiet hint, not a button. */
[class*="st-key-filter-row-"] [data-testid="stPopover"] button {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    opacity: .5;
    font-size: 11px;
}
</style>
"""

MASTHEAD_HTML = """
<div class="aa-masthead">
    <p class="aa-masthead-title">Almanac of You</p>
    <p class="aa-masthead-tagline">&mdash; a calendar of matching moments &mdash;</p>
</div>
<hr class="aa-rule-thick">
<hr class="aa-rule-thin">
"""
