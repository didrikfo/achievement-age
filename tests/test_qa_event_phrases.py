"""The QA harness's export-loading path, exercised without touching Supabase.

Only load_export/_parse_tags are under test: fetch_all_events talks to the
network and prepare_chunks is a thin wrapper over whichever of the two
produced the records.
"""

import json

import pytest

from scripts.qa_event_phrases import _parse_tags, load_export


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("{military,politics}", ["military", "politics"]),
        ('{"military","politics"}', ["military", "politics"]),
        ("{}", []),
        ("", []),
        (None, []),
        (["military"], ["military"]),
        ('["military", "science"]', ["military", "science"]),
    ],
)
def test_parse_tags_handles_every_export_shape(raw, expected):
    assert _parse_tags(raw) == expected


def test_load_export_reads_csv_and_coerces_integers(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text(
        "id,name,text,event_phrase,year,month,day,age_days,tags\n"
        '1,Ada Lovelace,"She published her notes, at last.",'
        'Ada Lovelace was when she published her notes.,1843,10,1,9862,"{science,technology}"\n',
        encoding="utf-8",
    )

    events = load_export(path)

    assert len(events) == 1
    assert events[0]["id"] == 1
    assert events[0]["year"] == 1843
    assert events[0]["age_days"] == 9862
    assert events[0]["tags"] == ["science", "technology"]
    assert events[0]["name"] == "Ada Lovelace"


def test_load_export_reads_json_and_leaves_native_types(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps([{"id": 7, "name": "Marie Curie", "year": 1903, "tags": ["science"]}]),
        encoding="utf-8",
    )

    events = load_export(path)

    assert events == [{"id": 7, "name": "Marie Curie", "year": 1903, "tags": ["science"]}]


def test_load_export_keeps_a_non_integer_field_rather_than_raising(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text("id,name,year,tags\n1,Anon,c. 1840,{}\n", encoding="utf-8")

    assert load_export(path)[0]["year"] == "c. 1840"
