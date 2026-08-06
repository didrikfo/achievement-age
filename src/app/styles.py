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
