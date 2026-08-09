import json

from core.io import load_json


def test_load_json_reads_a_plain_utf8_file(tmp_path):
    path = tmp_path / "plain.json"
    path.write_text(json.dumps([{"name": "Marie Curie"}]), encoding="utf-8")

    assert load_json(path) == [{"name": "Marie Curie"}]


def test_load_json_reads_a_file_with_a_utf8_bom(tmp_path):
    path = tmp_path / "bom.json"
    path.write_text(json.dumps([{"name": "Marie Curie"}]), encoding="utf-8-sig")

    assert load_json(path) == [{"name": "Marie Curie"}]


def test_load_json_sorts_by_field_length(tmp_path):
    path = tmp_path / "sortable.json"
    path.write_text(json.dumps([{"name": "Bob"}, {"name": "Alexandra"}]), encoding="utf-8")

    result = load_json(path, sort_by_field="name")

    assert [entry["name"] for entry in result] == ["Alexandra", "Bob"]
