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
