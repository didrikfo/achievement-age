"""Parsing for Billboard chart number ones: Hot 100 singles and Billboard 200 albums.

Hot 100: TidyTuesday's 2025-08-26 dataset (Chris Dalla Riva's "Billboard Hot
100 Number Ones"), one row per number-one song since the chart began on
1958-08-04, with the chart date it first hit number one and a hand-coded
artist_structure (solo/duo/group) and front_person_age - the latter is what
lets ingest.resolve_music_persons reject a same-name wrong person.

Billboard 200 and the Hot 100 archive: utdata/rwd-billboard-data's full
weekly chart archives (every position, every week). Billboard 200 number ones
are derived here - the first chart week each album was at position 1, and
how many weeks it spent there - and the Hot 100 archive is the authority the
TidyTuesday dates are verified against (verify_hot100_dates).

Chart dates are Billboard's own issue dates - the day the chart is dated, not
the sales week it measures - which is what Billboard, Wikipedia and every
number-one list use as "the date it hit number one".
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Dict, List, Set, Tuple

from core.matching import normalize_name
from ingest.sources.music import MUSIC_RAW_DIR, lead_credit, split_credit

HOT_100_PATH = MUSIC_RAW_DIR / "hot100_number_ones.csv"
HOT_100_CHART_PATH = MUSIC_RAW_DIR / "hot100_chart.csv"
BILLBOARD_200_PATH = MUSIC_RAW_DIR / "billboard200.csv"

#: Billboard 200 number ones credited to no performing act (soundtracks, cast
#: recordings, compilations) - there's no one whose age it could be.
_NON_ARTIST_MARKERS = ("soundtrack", "various artists", "original cast", "cast recording", "motion picture")


def _parse_iso_date(value: str) -> Tuple[int, int, int]:
    """"1958-08-04" or "1958-08-04T00:00:00Z" -> (1958, 8, 4)."""
    year, month, day = value[:10].split("-")
    return int(year), int(month), int(day)


def _optional_float(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_hot100_number_ones(path: Path = HOT_100_PATH) -> List[Dict]:
    """One record per Hot 100 number-one song, dated as the TidyTuesday file has it.

    Run the result through verify_hot100_dates before use - a handful of these
    dates are typos (see its docstring).
    """
    records: List[Dict] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            year, month, day = _parse_iso_date(row["date"])
            structure = row["artist_structure"].strip()
            # front_person_age averages everyone credited - a duo's two leads,
            # or a solo lead and their featured guest ("1.5") - so it only pins
            # down one specific person's age for a plain solo credit.
            expected_age = _optional_float(row["front_person_age"]) if structure == "1" else None
            records.append(
                {
                    "kind": "hot100_number_one",
                    "record_key": f"hot100:{row['song']}:{row['artist']}",
                    "title": row["song"],
                    "credit": row["artist"],
                    "artist_structure": structure,
                    "names": split_credit(row["artist"], structure),
                    "weeks_at_number_one": int(float(row["weeks_at_number_one"])),
                    "expected_age_years": expected_age,
                    "year": year,
                    "month": month,
                    "day": day,
                }
            )
    return records


def load_chart_number_ones(path: Path) -> Dict[Tuple[str, str], Tuple[str, int]]:
    """(title, performer) -> (first chart week at number one, weeks at number one).

    Works on either utdata weekly archive (Hot 100 or Billboard 200) - both
    have one row per chart position per week.
    """
    first_week: Dict[Tuple[str, str], str] = {}
    weeks_at_top: Dict[Tuple[str, str], int] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["current_week"] != "1":
                continue
            key = (row["title"], row["performer"])
            weeks_at_top[key] = weeks_at_top.get(key, 0) + 1
            if key not in first_week or row["chart_week"] < first_week[key]:
                first_week[key] = row["chart_week"]
    return {key: (week, weeks_at_top[key]) for key, week in first_week.items()}


def _title_key(title: str) -> str:
    return "".join(character for character in title.casefold() if character.isalnum())


def verify_hot100_dates(
    records: List[Dict], chart_number_ones: Dict[Tuple[str, str], Tuple[str, int]]
) -> Tuple[List[Dict], List[Dict]]:
    """Check each TidyTuesday date against the weekly chart archive; (verified, review).

    The TidyTuesday file mis-dates 8 of its ~1,180 songs - e.g. Whitney
    Houston's "I Wanna Dance with Somebody" as 1987-07-27, a Monday, when the
    chart that put it at number one was dated Saturday 1987-06-27. The archive
    is scraped chart by chart, so it's the authority on dates:

    - a date on which the archive has some song first reach number one is
      kept as is (only one song can first reach number one in a given week,
      and titles differ too much between the two files - "Come Together" vs.
      "Come Together/Something" - to demand a title match here);
    - otherwise the archive's date for the same title within 60 days replaces it;
    - otherwise the record is dropped and reported.
    """
    first_dates = {week for week, _ in chart_number_ones.values()}
    by_title: Dict[str, List[str]] = {}
    for (title, _performer), (week, _weeks) in chart_number_ones.items():
        by_title.setdefault(_title_key(title), []).append(week)

    verified: List[Dict] = []
    review: List[Dict] = []
    for record in records:
        claimed = date(record["year"], record["month"], record["day"])
        if claimed.isoformat() in first_dates:
            verified.append(record)
            continue

        title = _title_key(record["title"])
        nearby = sorted(
            week
            for key, weeks in by_title.items()
            if key.startswith(title) or title.startswith(key)
            for week in weeks
            if abs((date.fromisoformat(week) - claimed).days) <= 60
        )
        if not nearby:
            review.append(
                {
                    "stage": "hot100_dates",
                    "issue_type": "unverified_date",
                    "name": record["credit"],
                    "detail": f"{record['record_key']}: no chart archive number one near {claimed.isoformat()}",
                }
            )
            continue
        year, month, day = _parse_iso_date(nearby[0])
        verified.append({**record, "year": year, "month": month, "day": day})
        review.append(
            {
                "stage": "hot100_dates",
                "issue_type": "corrected_date",
                "name": record["credit"],
                "detail": f"{record['record_key']}: {claimed.isoformat()} -> {nearby[0]} per the chart archive",
            }
        )
    return verified, review


def known_credit_structures(hot100_records: List[Dict]) -> Dict[str, str]:
    """normalized lead credit -> artist_structure, from the hand-coded Hot 100 data.

    Lets the Billboard 200 parser tell a solo act from a group for any act that
    also had a Hot 100 number one - e.g. "The Weeknd" is one person despite the
    "The", and "Fleetwood Mac" is a band despite not having one.
    """
    return {normalize_name(lead_credit(record["credit"])): record["artist_structure"] for record in hot100_records}


def _is_non_artist(title: str, performer: str) -> bool:
    text = f"{title} {performer}".lower()
    return any(marker in text for marker in _NON_ARTIST_MARKERS)


def load_billboard200_number_ones(
    path: Path = BILLBOARD_200_PATH, credit_structures: Dict[str, str] | None = None
) -> List[Dict]:
    """One record per album that reached number one on the Billboard 200.

    Keyed by (title, performer): an album that returns to number one later
    keeps its first date, with every week at number one counted.
    """
    credit_structures = credit_structures or {}
    records: List[Dict] = []
    number_ones = load_chart_number_ones(path)
    for (title, performer), (chart_week, weeks) in sorted(number_ones.items(), key=lambda item: (item[1][0], item[0])):
        if _is_non_artist(title, performer):
            continue
        year, month, day = _parse_iso_date(chart_week)
        structure = credit_structures.get(normalize_name(lead_credit(performer)))
        records.append(
            {
                "kind": "billboard200_number_one",
                "record_key": f"billboard200:{title}:{performer}",
                "title": title,
                "credit": performer,
                "artist_structure": structure,
                "names": split_credit(performer, structure),
                "weeks_at_number_one": weeks,
                "expected_age_years": None,
                "year": year,
                "month": month,
                "day": day,
            }
        )
    return records


def group_credits(records: List[Dict]) -> Set[str]:
    """Credits that name no individual person - reported, not ingested."""
    return {record["credit"] for record in records if not record["names"]}
