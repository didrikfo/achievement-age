"""Wiring tests for the panel: a click reaches the right rule.

The rules themselves are unit-tested in test_preferences.py without a Streamlit
runtime; these only prove the buttons are connected to them.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent / "apps" / "filter_panel_app.py")


def _value(app, prefix):
    for element in app.text:
        if element.value.startswith(prefix):
            return element.value[len(prefix):]
    raise AssertionError(f"no text element starting with {prefix!r}")


def _button(app, key):
    # Looked up by iterating rather than by an accessor: AppTest's element lists
    # index positionally, and a key lookup is the stable thing to assert on.
    return next(button for button in app.button if button.key == key)


def _toggle(app, key):
    return next(toggle for toggle in app.toggle if toggle.key == key)


def _run():
    app = AppTest.from_file(APP)
    app.run()
    return app


def test_the_panel_starts_with_everything_marked_and_mirroring():
    app = _run()

    assert "Sport" in _value(app, "calendar_categories=")
    assert _value(app, "mirroring=") == "True"


def test_clicking_a_row_drops_it_from_the_calendar():
    app = _run()

    _button(app, "row-category-Sport").click().run()

    assert "Sport" not in _value(app, "calendar_categories=")
    assert "Disasters" in _value(app, "calendar_categories=")


def test_clicking_a_bell_mutes_the_row_and_breaks_the_mirror():
    app = _run()

    _button(app, "bell-category-Sport").click().run()

    assert _value(app, "mirroring=") == "False"
    assert "Sport" in _value(app, "calendar_categories=")
    assert "Sport" not in _value(app, "notify_categories=")


def test_clicking_a_dim_bell_turns_both_channels_on():
    app = _run()

    _button(app, "bell-sequence-Primes").click().run()

    assert "Primes" in _value(app, "calendar_sequences=")
    assert "Primes" in _value(app, "notify_sequences=")
    assert _value(app, "mirroring=") == "True"


def test_the_mirror_toggle_restores_mirroring():
    app = _run()
    _button(app, "bell-category-Sport").click().run()
    assert _value(app, "mirroring=") == "False"

    _toggle(app, "notify_mirror").set_value(True).run()

    assert _value(app, "mirroring=") == "True"
    assert "Sport" in _value(app, "notify_categories=")


def test_single_tag_categories_get_no_narrowing_popover():
    app = _run()
    keys = [button.key for button in app.button]

    # War & Conflict maps to exactly one tag, so a narrowing control is a no-op.
    assert not any(key == "tag-military" for key in keys)
    assert any(key == "tag-science" for key in keys)


def test_clicking_a_tag_narrows_only_that_tag():
    app = _run()

    # Science & Technology has four tags, so its popover is a real narrowing
    # control rather than a no-op.
    _button(app, "tag-science").click().run()

    assert "science" not in _value(app, "calendar_tags=").split(",")
    assert "technology" in _value(app, "calendar_tags=").split(",")
