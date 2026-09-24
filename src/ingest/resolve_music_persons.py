"""Resolve the people behind music records to day-precision birth dates.

Runs after the source parsers and before ingest.migrate_music_to_supabase:

    python -m ingest.resolve_music_persons            # births file + Wikidata
    python -m ingest.resolve_music_persons --offline  # births file only

Two sources, in order:

1. The scraped births file (data/historical_births_cleaned.json), but only
   entries whose description reads as a musician's. The file has a single
   "Michael Jackson" - not the singer - so a bare name match is unsafe here.
2. Wikidata (ingest.sources.wikidata.lookup_birth_date), restricted to
   candidates described as musicians whose label or alias is exactly the
   credited name, with its own cache so these filtered outcomes never mix with
   the unfiltered cache the historical corpus uses. Wikidata covers far more
   musicians than the births file does (Madonna, Mariah Carey and Taylor Swift
   are all missing from it).

A resolved birth date must then give a plausible age at the event, and for a
Hot 100 solo act it must agree with the dataset's own hand-researched
front_person_age to within a year - the check that catches a same-name wrong
person that slipped through both filters above.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from core.matching import normalize_name
from ingest.enrichment import write_review_entries
from ingest.match_events import MAX_AGE_DAYS, WIDENED_BIRTHS_PATH
from ingest.pipeline import calculate_age
from ingest.sources import wikidata
from ingest.sources.billboard import (
    HOT_100_CHART_PATH,
    known_credit_structures,
    load_billboard200_number_ones,
    load_chart_number_ones,
    load_hot100_number_ones,
    verify_hot100_dates,
)
from ingest.sources.eurovision import load_eurovision_winners

RESOLVED_PATH = DATA_DIR / "tmp" / "music_resolved.json"
REVIEW_PATH = DATA_DIR / "tmp" / "music_resolution_review.json"
MUSIC_WIKIDATA_CACHE_PATH = DATA_DIR / "wikidata_musicians_cache.json"

#: Youngest plausible age for a chart-topper or contest winner. Stevie Wonder
#: was 13 at his first number one; anything under 5 is a wrong-person match.
MIN_AGE_DAYS = 5 * 365

#: How far a computed age may stray from the Hot 100 dataset's front_person_age.
#: The dataset sometimes uses an artist's publicly claimed birth year (it has
#: Jan Hammer and Del Shannon two years younger than they were), while a
#: wrong-person match is typically a decade or more out ("Ray Charles" in the
#: births file is 12 years older than the singer).
EXPECTED_AGE_TOLERANCE_YEARS = 2.0

MUSIC_DESCRIPTION = re.compile(
    r"singer|musician|rapper|songwriter|composer|guitarist|pianist|drummer|bassist|vocalist|"
    r"keyboardist|saxophonist|trumpeter|violinist|conductor|bandleader|band leader|disc jockey|\bDJ\b|"
    r"record producer|music|recording artist|entertainer|crooner|chansonnier|yodel",
    re.IGNORECASE,
)


def is_music_description(text: Optional[str]) -> bool:
    return bool(text) and bool(MUSIC_DESCRIPTION.search(text))


def musician_candidate_filter(name: str):
    """A wikidata.lookup_birth_date candidate_filter for one credited name.

    wbsearchentities matches prefixes, so a search for a band's name ("ABBA",
    "Lordi") can surface a person whose label merely starts with it. Requiring
    the matched label/alias to equal the credit - and the description to read
    as a musician's - leaves only people actually known by that name.
    """
    wanted = normalize_name(name)

    def accept(candidate: Dict) -> bool:
        matched = (candidate.get("match") or {}).get("text") or candidate.get("label") or ""
        return normalize_name(matched) == wanted and is_music_description(candidate.get("description"))

    return accept


# --- pure functions ---


def expand_to_people(records: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """One record per credited person; (people, group_records).

    Every person record keeps the full source record (names included, so the
    phrase can mention a duo partner) plus "name", the person it's about.
    """
    people: List[Dict] = []
    groups: List[Dict] = []
    for record in records:
        if not record["names"]:
            groups.append(record)
            continue
        people.extend({**record, "name": name} for name in record["names"])
    return people, groups


def build_music_births_lookup(births: List[Dict]) -> Dict[str, List[Dict]]:
    """normalized name -> the distinct musician birth records in the births file."""
    lookup: Dict[str, List[Dict]] = {}
    for birth in births:
        if not is_music_description(birth.get("text")):
            continue
        try:
            entry = {"year": int(birth["year"]), "month": int(birth["month"]), "day": int(birth["day"])}
        except (KeyError, TypeError, ValueError):
            continue
        entries = lookup.setdefault(normalize_name(birth["name"]), [])
        if entry not in entries:
            entries.append(entry)
    return lookup


def resolve_from_births(name: str, lookup: Dict[str, List[Dict]]) -> Dict:
    """A lookup_birth_date-shaped result from the births file alone."""
    entries = lookup.get(normalize_name(name), [])
    if len(entries) == 1:
        return {"status": "resolved", "name": name, **entries[0]}
    if len(entries) > 1:
        return {"status": "ambiguous", "name": name}
    return {"status": "not_found", "name": name}


def age_in_years(birth: Tuple[int, int, int], event: Tuple[int, int, int]) -> int:
    years = event[0] - birth[0]
    if (event[1], event[2]) < (birth[1], birth[2]):
        years -= 1
    return years


def check_resolved_record(record: Dict) -> Tuple[Optional[int], Optional[str]]:
    """(age_days, None) for a plausible birth date, or (None, reason)."""
    birth = (record["birth_year"], record["birth_month"], record["birth_day"])
    event = (record["year"], record["month"], record["day"])
    age_days = calculate_age(*birth, *event)
    if age_days is None or not (MIN_AGE_DAYS <= age_days <= MAX_AGE_DAYS):
        return None, f"implausible age ({age_days} days) at the event"

    expected = record.get("expected_age_years")
    if expected is not None:
        actual = age_in_years(birth, event)
        if abs(actual - expected) > EXPECTED_AGE_TOLERANCE_YEARS:
            return None, f"age {actual} disagrees with the dataset's front_person_age {expected:g}"
    return age_days, None


def _birth_fields(result: Dict, source: str) -> Dict:
    return {
        "birth_year": result["year"], "birth_month": result["month"], "birth_day": result["day"],
        "birth_source": source, "wikidata_qid": result.get("qid"),
    }


def resolve_people(
    people: List[Dict],
    births_lookup: Dict[str, List[Dict]],
    lookup_wikidata=None,
) -> Tuple[List[Dict], List[Dict]]:
    """Attach a birth date and age_days to each person record; (resolved, review).

    Candidate birth dates are tried per name, births file first, then
    lookup_wikidata(name, earliest_event_year) - None skips Wikidata entirely.
    A candidate is accepted only if it passes check_resolved_record for every
    one of that name's records: one failure proves it's someone else's birth
    date, and the other records (a Billboard 200 album has no front_person_age
    to check against) must not quietly keep it. A rejected births-file date
    falls through to Wikidata.
    """
    records_by_name: Dict[str, List[Dict]] = {}
    for person in people:
        records_by_name.setdefault(person["name"], []).append(person)

    resolved: List[Dict] = []
    review: List[Dict] = []
    for name in sorted(records_by_name):
        records = records_by_name[name]
        lookups = [("births_file", lambda: resolve_from_births(name, births_lookup))]
        if lookup_wikidata is not None:
            earliest_year = min(record["year"] for record in records)
            lookups.append(("wikidata", lambda: lookup_wikidata(name, earliest_year)))

        accepted: Optional[List[Dict]] = None
        for source, lookup in lookups:
            result = lookup()
            if result["status"] != "resolved":
                review.append(
                    {"stage": "music_birth_date", "issue_type": result["status"], "name": name, "detail": f"via {source}"}
                )
                continue
            birth = _birth_fields(result, source)
            checked = [({**record, **birth}, check_resolved_record({**record, **birth})) for record in records]
            failures = [(record, reason) for record, (_, reason) in checked if reason]
            if failures:
                record, reason = failures[0]
                review.append(
                    {
                        "stage": "music_age_check",
                        "issue_type": "rejected_birth_date",
                        "name": name,
                        "detail": (
                            f"{source} birth date {birth['birth_year']}-{birth['birth_month']:02d}-"
                            f"{birth['birth_day']:02d} rejected for all {len(records)} record(s): "
                            f"{record['record_key']}: {reason}"
                        ),
                    }
                )
                continue
            accepted = [{**record, "age_days": age_days} for record, (age_days, _) in checked]
            break

        if accepted:
            resolved.extend(accepted)
    return resolved, review


# --- orchestration (impure) ---


def load_music_records() -> Tuple[List[Dict], List[Dict]]:
    """(records, review) from every music source, parsed from data/raw_data/music/."""
    hot100, review = verify_hot100_dates(load_hot100_number_ones(), load_chart_number_ones(HOT_100_CHART_PATH))
    billboard200 = load_billboard200_number_ones(credit_structures=known_credit_structures(hot100))
    return hot100 + billboard200 + load_eurovision_winners(), review


def run(
    use_wikidata: bool = True,
    births_path: Path = WIDENED_BIRTHS_PATH,
    output_path: Path = RESOLVED_PATH,
    review_path: Path = REVIEW_PATH,
    cache_path: Path = MUSIC_WIKIDATA_CACHE_PATH,
) -> Dict[str, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    records, date_review = load_music_records()
    people, groups = expand_to_people(records)
    births_lookup = build_music_births_lookup(load_json(births_path))

    lookup = None
    cache: Dict = {}
    if use_wikidata:
        cache = wikidata.load_cache(cache_path)

        def lookup(name: str, event_year: int) -> Dict:
            return wikidata.lookup_birth_date(name, event_year, cache, candidate_filter=musician_candidate_filter(name))

    try:
        resolved, review = resolve_people(people, births_lookup, lookup)
    finally:
        if use_wikidata:
            wikidata.save_cache(cache, cache_path)

    review.extend(date_review)
    review.extend(
        {"stage": "music_credit", "issue_type": "group_credit", "name": record["credit"], "detail": record["record_key"]}
        for record in groups
    )
    write_review_entries(review, review_path)
    save_to_json(output_path, resolved)
    return {
        "records": len(records),
        "group_records": len(groups),
        "person_records": len(people),
        "distinct_people": len({person["name"] for person in people}),
        "resolved_person_records": len(resolved),
        "resolved_people": len({record["name"] for record in resolved}),
    }


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(run(use_wikidata="--offline" not in sys.argv[1:]))
