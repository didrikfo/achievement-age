"""Resolve the people behind music records to day-precision birth dates.

Runs after the source parsers and before ingest.migrate_music_to_supabase:

    python -m ingest.resolve_music_persons            # Wikidata + births file
    python -m ingest.resolve_music_persons --offline  # births file only

Two sources, in order:

1. Wikidata (ingest.sources.wikidata.lookup_birth_date), restricted to
   candidates described as musicians whose label or an English alias is
   exactly the credited name, with its own cache so these filtered outcomes
   never mix with the unfiltered cache the historical corpus uses. Wikidata
   covers far more musicians than the births file does (Madonna, Mariah Carey
   and Taylor Swift are all missing from it), and it alone knows death dates.
2. The scraped births file (data/historical_births_cleaned.json), but only
   entries whose description reads as a musician's. The file has a single
   "Michael Jackson" - not the singer - so a bare name match is unsafe here.

A resolved birth date must then give a plausible age at the event, and for a
Hot 100 solo act it must agree with the dataset's own hand-researched
front_person_age to within two years - the check that catches a same-name
wrong person that slipped through both filters above. Records dated after
the person's death are dropped: an age at a posthumous event means nothing.
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

#: An ambiguous name goes to its most-linked Wikidata candidate only when that
#: one is this many times as linked as the runner-up and has at least
#: MIN_FAME_SITELINKS. A chart-topper is famous: the singer Chris Brown has 78
#: sitelinks to his namesake's 0, John Mayer 61 to 7. Bobby Lewis of "Tossin'
#: and Turnin'" (9 to 3) is not a clear enough winner and stays unresolved.
FAME_DOMINANCE_RATIO = 4
MIN_FAME_SITELINKS = 10

MUSIC_DESCRIPTION = re.compile(
    r"singer|musician|rapper|songwriter|composer|guitarist|pianist|drummer|bassist|vocalist|"
    r"keyboardist|saxophonist|trumpeter|violinist|conductor|bandleader|band leader|disc jockey|\bDJ\b|"
    r"record producer|music|recording artist|entertainer|crooner|chansonnier|yodel|R&B|hip[ -]hop",
    re.IGNORECASE,
)


def is_music_description(text: Optional[str]) -> bool:
    return bool(text) and bool(MUSIC_DESCRIPTION.search(text))


def musician_candidate_filter(candidate: Dict) -> bool:
    """A wikidata.lookup_birth_date candidate_filter: only people described as musicians.

    Checked before any entity is fetched, so the towns, spirits and albums a
    search for "Brandy" or "Future" returns cost nothing. The exact-name check
    (require_exact_name) runs after the fetch, since it needs the aliases.
    """
    return is_music_description(candidate.get("description"))


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
        "wikidata_label": result.get("label"), "death": result.get("death"),
    }


def _check_all(records: List[Dict], birth: Dict) -> Tuple[List[Dict], Optional[Tuple[Dict, str]]]:
    """(records with age_days, None) if birth passes for every record, else ([], first failure)."""
    accepted: List[Dict] = []
    for record in records:
        age_days, reason = check_resolved_record({**record, **birth})
        if reason:
            return [], (record, reason)
        accepted.append({**record, **birth, "age_days": age_days})
    return accepted, None


def is_posthumous(record: Dict, death: Optional[Dict]) -> bool:
    """Whether the record's event came after the person's death.

    A coarse death date counts against an event that could have followed it:
    died "1970" rules out any 1970 or later event, died "October 1970" any
    event from October 1970 on. An age at a posthumous event means nothing.
    """
    if not death:
        return False
    event = (record["year"], record["month"], record["day"])
    if death.get("day") is not None:
        return event > (death["year"], death["month"], death["day"])
    if death.get("month") is not None:
        return event[:2] >= (death["year"], death["month"])
    return event[0] >= death["year"]


def settle_ambiguous(records: List[Dict], candidates: List[Dict], source: str) -> Optional[Dict]:
    """The one candidate an ambiguous name belongs to, or None if the evidence doesn't settle it.

    Candidates are narrowed to those passing every record's age check, then
    to those alive for at least one record (a 1914-born Ken Carson who died in
    1994 had no 2020s number ones). If several remain, a clear fame winner is
    taken - see FAME_DOMINANCE_RATIO.
    """
    passing = []
    for candidate in candidates:
        checked, _ = _check_all(records, _birth_fields(candidate, source))
        if checked and not all(is_posthumous(record, candidate.get("death")) for record in checked):
            passing.append(candidate)
    if len(passing) == 1:
        return passing[0]
    if len(passing) > 1:
        ranked = sorted(passing, key=lambda candidate: candidate.get("sitelinks") or 0, reverse=True)
        top, runner_up = ranked[0].get("sitelinks") or 0, ranked[1].get("sitelinks") or 0
        if top >= MIN_FAME_SITELINKS and top >= FAME_DOMINANCE_RATIO * runner_up:
            return ranked[0]
    return None


def _format_death(death: Dict) -> str:
    return "-".join(f"{part:02d}" for part in (death["year"], death.get("month"), death.get("day")) if part)


def canonicalize_names(resolved: List[Dict]) -> List[Dict]:
    """One name per Wikidata person, so one persons row per person.

    The charts spell the same act differently ("Beyonce" on the Billboard 200,
    "Beyoncé" on the Hot 100). Names that resolved to the same QID take the
    spelling that equals the Wikidata label, else the one with most records.
    """
    names_by_qid: Dict[str, Dict[str, int]] = {}
    labels: Dict[str, str] = {}
    for record in resolved:
        qid = record.get("wikidata_qid")
        if qid:
            counts = names_by_qid.setdefault(qid, {})
            counts[record["name"]] = counts.get(record["name"], 0) + 1
            labels[qid] = record.get("wikidata_label") or ""

    renames: Dict[str, str] = {}
    for qid, counts in names_by_qid.items():
        if len(counts) < 2:
            continue
        by_count = max(sorted(counts), key=lambda name: counts[name])
        canonical = labels[qid] if labels[qid] in counts else by_count
        renames.update({name: canonical for name in counts if name != canonical})

    return [
        {
            **record,
            "name": renames.get(record["name"], record["name"]),
            "names": [renames.get(name, name) for name in record["names"]],
        }
        for record in resolved
    ]


def resolve_people(
    people: List[Dict],
    births_lookup: Dict[str, List[Dict]],
    lookup_wikidata=None,
) -> Tuple[List[Dict], List[Dict]]:
    """Attach a birth date and age_days to each person record; (resolved, review).

    Candidate birth dates are tried per name: Wikidata first -
    lookup_wikidata(name, earliest_event_year), None skips it - because only
    Wikidata also knows when someone died; then the births file.

    A candidate is accepted only if it passes check_resolved_record for every
    one of that name's records: one failure proves it's someone else's birth
    date, and the other records (a Billboard 200 album has no front_person_age
    to check against) must not quietly keep it. An ambiguous Wikidata result
    is settled by settle_ambiguous when the evidence allows.
    A rejected births-file date falls through to nothing; a rejected Wikidata
    date does not fall through to the births file either, since the two
    sources would then disagree about one person.

    Records dated after the person's death (Otis Redding's "Dock of the Bay",
    Aaliyah's last album) are dropped and reported. A births-file date carries
    no death date, so with Wikidata on, those names are reported for a manual
    posthumous check.
    """
    records_by_name: Dict[str, List[Dict]] = {}
    for person in people:
        records_by_name.setdefault(person["name"], []).append(person)

    resolved: List[Dict] = []
    review: List[Dict] = []
    for name in sorted(records_by_name):
        records = records_by_name[name]
        lookups = []
        if lookup_wikidata is not None:
            earliest_year = min(record["year"] for record in records)
            lookups.append(("wikidata", lambda: lookup_wikidata(name, earliest_year)))
        lookups.append(("births_file", lambda: resolve_from_births(name, births_lookup)))

        accepted: List[Dict] = []
        for source, lookup in lookups:
            result = lookup()
            if result["status"] == "ambiguous" and result.get("candidates"):
                chosen = settle_ambiguous(records, result["candidates"], source)
                if chosen is not None:
                    result = {"status": "resolved", "name": name, **chosen}
            if result["status"] != "resolved":
                review.append(
                    {"stage": "music_birth_date", "issue_type": result["status"], "name": name, "detail": f"via {source}"}
                )
                continue
            birth = _birth_fields(result, source)
            accepted, failure = _check_all(records, birth)
            if failure:
                record, reason = failure
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
                if source == "wikidata":
                    # Wikidata found this exact-name musician, but the dataset contradicts
                    # it - a births-file date for the same name would only be a guess
                    # between two disagreeing sources (Ernie K-Doe: 1936 vs. 1933).
                    break
                continue
            if source == "births_file" and lookup_wikidata is not None:
                review.append(
                    {
                        "stage": "music_death_date",
                        "issue_type": "death_date_unknown",
                        "name": name,
                        "detail": "resolved from the births file only - check by hand for posthumous records",
                    }
                )
            break

        for record in accepted:
            if is_posthumous(record, record["death"]):
                review.append(
                    {
                        "stage": "music_death_date",
                        "issue_type": "posthumous_record",
                        "name": name,
                        "detail": (
                            f"{record['record_key']} on {record['year']}-{record['month']:02d}-{record['day']:02d} "
                            f"is after the death on {_format_death(record['death'])}"
                        ),
                    }
                )
            else:
                resolved.append(record)
    return canonicalize_names(resolved), review


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
    # write_review_entries appends; this run is the file's only writer, so start it fresh.
    review_path.unlink(missing_ok=True)

    records, date_review = load_music_records()
    people, groups = expand_to_people(records)
    births_lookup = build_music_births_lookup(load_json(births_path))

    lookup = None
    cache: Dict = {}
    if use_wikidata:
        cache = wikidata.load_cache(cache_path)

        def lookup(name: str, event_year: int) -> Dict:
            return wikidata.lookup_birth_date(
                name, event_year, cache, candidate_filter=musician_candidate_filter, require_exact_name=True
            )

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
