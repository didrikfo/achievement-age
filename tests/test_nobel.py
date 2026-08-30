import csv
import json

from core.config import TAG_TAXONOMY
from ingest.sources.nobel import (
    NOBEL_CATEGORY_TAGS,
    NOBEL_SOURCE,
    build_event_text,
    category_display_name,
    load_nobel_records,
    split_by_birth_data,
)

_HEADER = "award_year,date_awarded,laureate_id,known_name,category,motivation,birth_date,wikipedia_url"


def _write_csv(tmp_path, rows, bom=True):
    lines = [_HEADER] + rows
    content = "\n".join(lines)
    path = tmp_path / "nobel.csv"
    # The real export carries a BOM on the first header; utf-8-sig strips it.
    path.write_text(("﻿" if bom else "") + content, encoding="utf-8")
    return path


def test_load_nobel_records_parses_iso_birth_date(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            '1911,12/10/1911,6,Marie Curie,Chemistry,"in recognition of...",1867-11-07,https://en.wikipedia.org/wiki/Marie_Curie'
        ],
    )
    records = load_nobel_records(path)
    assert len(records) == 1
    record = records[0]
    assert record["laureate_id"] == "6"
    assert record["name"] == "Marie Curie"
    assert record["category"] == "Chemistry"
    assert (record["award_year"], record["award_month"], record["award_day"]) == (1911, 12, 10)
    assert (record["birth_year"], record["birth_month"], record["birth_day"]) == (1867, 11, 7)
    assert record["wikipedia_url"] == "https://en.wikipedia.org/wiki/Marie_Curie"


def test_load_nobel_records_parses_us_format_birth_date(tmp_path):
    # Real row: Enrico Fermi, born September 29 1901 - not the 9th of an invalid
    # 29th month. Confirms MM/DD/YYYY, matching date_awarded's confirmed format.
    path = _write_csv(
        tmp_path,
        ['1938,11/10/1938,66,Enrico Fermi,Physics,"for his work",9/29/1901,'],
    )
    record = load_nobel_records(path)[0]
    assert (record["birth_year"], record["birth_month"], record["birth_day"]) == (1901, 9, 29)


def test_load_nobel_records_treats_blank_birth_date_as_missing(tmp_path):
    path = _write_csv(
        tmp_path,
        ['1973,10/17/1973,394,Henry Kissinger,Peace,"for negotiating peace",,'],
    )
    record = load_nobel_records(path)[0]
    assert record["birth_year"] is None
    assert record["birth_month"] is None
    assert record["birth_day"] is None
    assert record["wikipedia_url"] is None


def test_load_nobel_records_strips_the_bom_from_the_first_header(tmp_path):
    path = _write_csv(
        tmp_path,
        ['1921,11/9/1922,26,Albert Einstein,Physics,"for his services",1879-03-14,'],
        bom=True,
    )
    # Would raise KeyError on "award_year" if the BOM leaked into the header name.
    record = load_nobel_records(path)[0]
    assert record["award_year"] == 1922


def test_split_by_birth_data_separates_thin_records():
    records = [
        {"name": "Has Birth", "birth_year": 1900, "birth_month": 1, "birth_day": 1},
        {"name": "No Birth", "birth_year": None, "birth_month": None, "birth_day": None},
    ]
    with_birth, missing = split_by_birth_data(records)
    assert [r["name"] for r in with_birth] == ["Has Birth"]
    assert [r["name"] for r in missing] == ["No Birth"]


def test_category_display_name_special_cases_economic_sciences():
    assert category_display_name("Economic Sciences") == "the Nobel Memorial Prize in Economic Sciences"
    assert category_display_name("Physics") == "the Nobel Prize in Physics"


def test_category_display_name_special_cases_peace():
    # "the Nobel Prize in Peace" is not how anyone actually says it - the
    # universal real-world name is "the Nobel Peace Prize".
    assert category_display_name("Peace") == "the Nobel Peace Prize"


def test_nobel_category_tags_covers_every_real_category():
    # Every category actually present in the CSV must have a tag mapping -
    # a missing entry would KeyError deep in the merge/insert pipeline.
    assert set(NOBEL_CATEGORY_TAGS) == {
        "Physics", "Chemistry", "Physiology or Medicine", "Literature", "Peace", "Economic Sciences",
    }
    assert NOBEL_CATEGORY_TAGS["Peace"] == "politics"
    assert NOBEL_CATEGORY_TAGS["Physiology or Medicine"] == "health"


def test_nobel_category_tags_values_are_all_valid_taxonomy_tags():
    # A category mapped to a tag outside TAG_TAXONOMY would be silently
    # dropped by ingest.enrichment.build_tag_rows, producing a Nobel event
    # with no tag rows at all.
    assert set(NOBEL_CATEGORY_TAGS.values()) <= set(TAG_TAXONOMY)


def test_build_event_text_uses_the_category_display_name():
    record = {"name": "Marie Curie", "category": "Chemistry", "motivation": "in recognition of her work"}
    assert build_event_text(record) == (
        'Marie Curie won the Nobel Prize in Chemistry: "in recognition of her work"'
    )


def test_build_event_text_special_cases_economic_sciences():
    record = {"name": "Milton Friedman", "category": "Economic Sciences", "motivation": "for his achievements"}
    assert build_event_text(record) == (
        'Milton Friedman won the Nobel Memorial Prize in Economic Sciences: "for his achievements"'
    )


def test_build_event_text_special_cases_peace():
    record = {"name": "Henry Kissinger", "category": "Peace", "motivation": "for negotiating peace"}
    assert build_event_text(record) == (
        'Henry Kissinger won the Nobel Peace Prize: "for negotiating peace"'
    )


def test_nobel_source_constant():
    assert NOBEL_SOURCE == "nobel_prize_dataset"


def test_load_nobel_records_against_the_real_csv_has_no_duplicate_row_keys():
    # Regression test for the (laureate_id, award_year) uniqueness this plan
    # relies on for keying LLM chunk records - verified against the live file
    # during design (John Bardeen, Frederick Sanger, and K. Barry Sharpless each
    # won the SAME category twice in different years, so (laureate_id, category)
    # would NOT be unique - only (laureate_id, award_year) is).
    from ingest.sources.nobel import NOBEL_CSV_PATH

    records = load_nobel_records(NOBEL_CSV_PATH)
    keys = [(r["laureate_id"], r["award_year"]) for r in records]
    assert len(keys) == len(set(keys))
    assert len(records) == 995
