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
    font-weight: 700;
}
.st-key-calendar-grid button[data-testid="stBaseButton-primary"],
.st-key-calendar-grid button[data-testid="stBaseButton-primary"] * { color: var(--aa-accent) !important; }
.st-key-calendar-grid button[data-testid="stBaseButton-primary"]::before {
    content: ''; position: absolute; top: 50%; left: 50%;
    width: 30px; height: 30px; margin: -15px 0 0 -15px;
    border: 2.5px solid var(--aa-accent); border-radius: 50%; transform: rotate(-8deg);
}

/* A day that is both today AND a match: black fill, still red-circled */
[class*="st-key-today-match-"] button[data-testid="stBaseButton-primary"] {
    background: var(--aa-ink) !important;
}
[class*="st-key-today-match-"] button[data-testid="stBaseButton-primary"],
[class*="st-key-today-match-"] button[data-testid="stBaseButton-primary"] * { color: var(--aa-bg) !important; }

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
    .st-key-calendar-grid button[data-testid="stBaseButton-primary"]::before {
        width: 24px; height: 24px; margin: -12px 0 0 -12px; border-width: 2px;
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
