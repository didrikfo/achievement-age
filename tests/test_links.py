from app.links import base_url_from


def test_strips_query_string():
    assert base_url_from("https://almanac.streamlit.app/?u=abc123") == "https://almanac.streamlit.app"


def test_strips_fragment_and_query_together():
    assert base_url_from("https://example.com/app?u=x#section") == "https://example.com/app"


def test_strips_a_trailing_slash():
    assert base_url_from("https://example.com/") == "https://example.com"


def test_leaves_a_clean_base_url_alone():
    assert base_url_from("https://example.com/app") == "https://example.com/app"


def test_keeps_a_localhost_port():
    assert base_url_from("http://localhost:8517/?u=abc") == "http://localhost:8517"


def test_empty_input_gives_empty_output():
    assert base_url_from("") == ""
