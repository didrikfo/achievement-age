"""One-off backfill: fill persons.wikipedia_url with verified article links.

Run after the events corpus exists (persons rows are created by the migration
and enrichment scripts):

    ./venv/Scripts/python.exe -m ingest.backfill_person_wikipedia

The failure that matters here is a *wrong* link, not a missing one. Every
candidate article is checked against the person's real birth year - derivable
from any of their events, since an event row carries both its own date and the
person's age in days at the time - and only verified links are written. Anything
rejected goes to data/tmp/person_wikipedia_review.json with a reason rather than
being guessed at.

Safe to rerun: rows that already have a URL are never fetched or overwritten
(so a hand-corrected value survives), and every lookup outcome is cached by
normalized name, so a second run makes no network requests for names already
attempted.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.db import fetch_events, get_client
from core.matching import normalize_name
from ingest.enrichment import write_review_entries
from ingest.sources import wikipedia
from ingest.sources.wikidata import load_cache, save_cache

CACHE_PATH = DATA_DIR / "wikipedia_person_cache.json"
REVIEW_PATH = DATA_DIR / "tmp" / "person_wikipedia_review.json"
PERSONS_PAGE_SIZE = 1000

#: A Julian/Gregorian or timezone edge can shift a January or December birth
#: date across a year boundary. That is not a wrong-person signal.
YEAR_TOLERANCE = 1

#: How often to persist the cache during the birth-year phase, so an interrupted
#: run keeps most of its progress.
CACHE_SAVE_EVERY = 25


def birth_years_by_person(events: List[Dict]) -> Tuple[Dict[int, int], List[int]]:
    """Map person_id -> birth year, plus the person_ids whose events disagree.

    An event's date minus the person's age in days at that event is their birth
    date. A person whose events imply two different birth dates is not
    verifiable and is reported rather than averaged away - that is a data bug
    worth seeing. Rows with an unusable date (month or day 0, as scraped source
    data sometimes has) are skipped: one bad row must not take out the run.
    """
    birth_dates: Dict[int, set] = {}
    for event in events:
        person_id = event.get("person_id")
        if person_id is None:
            continue
        try:
            event_date = date(int(event["year"]), int(event["month"]), int(event["day"]))
            birth_date = event_date - timedelta(days=int(event["age_days"]))
        except (KeyError, TypeError, ValueError):
            continue
        birth_dates.setdefault(person_id, set()).add(birth_date)

    years = {
        person_id: next(iter(dates)).year
        for person_id, dates in birth_dates.items()
        if len(dates) == 1
    }
    conflicting = [person_id for person_id, dates in birth_dates.items() if len(dates) > 1]
    return years, conflicting


def year_matches(expected_year: int, found_year: Optional[int]) -> bool:
    """Whether a Wikidata birth year confirms the person we expected."""
    return found_year is not None and abs(found_year - expected_year) <= YEAR_TOLERANCE


def fetch_persons_missing_url(client) -> List[Dict]:
    """Every persons row without a wikipedia_url, paginated past the PostgREST cap."""
    persons: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("persons")
            .select("id, name")
            .is_("wikipedia_url", "null")
            .range(start, start + PERSONS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        persons.extend(page)
        if len(page) < PERSONS_PAGE_SIZE:
            break
        start += PERSONS_PAGE_SIZE
    return persons


def main(cache_path: Path = CACHE_PATH, review_path: Path = REVIEW_PATH) -> Dict[str, int]:
    client = get_client()

    persons = fetch_persons_missing_url(client)
    birth_years, conflicting = birth_years_by_person(fetch_events())
    cache = load_cache(cache_path)
    counts: Counter = Counter()
    review: List[Dict] = []

    # Phase 1: resolve every uncached name in batches of 50.
    uncached = [person["name"] for person in persons if normalize_name(person["name"]) not in cache]
    print(f"{len(persons)} person(s) without a link; {len(uncached)} to resolve, rest cached.")
    for name, resolved in wikipedia.resolve_titles(uncached).items():
        cache[normalize_name(name)] = dict(resolved, birth_year_checked=False)
    save_cache(cache, cache_path)

    # Phase 2: verify each candidate's birth year, then write or reject.
    for index, person in enumerate(persons, start=1):
        key = normalize_name(person["name"])
        entry = cache.get(key) or {"status": "missing", "title": None, "url": None, "qid": None}

        if person["id"] in conflicting:
            counts["conflicting_local_birth_dates"] += 1
            review.append({"name": person["name"], "status": "conflicting_local_birth_dates",
                           "detail": "this person's events imply more than one birth date",
                           "candidate_url": entry.get("url")})
            continue

        expected_year = birth_years.get(person["id"])
        if expected_year is None:
            counts["no_local_birth_date"] += 1
            review.append({"name": person["name"], "status": "no_local_birth_date",
                           "detail": "no event row to derive a birth date from",
                           "candidate_url": entry.get("url")})
            continue

        if entry.get("status") != "found" or not entry.get("url"):
            counts[entry.get("status", "missing")] += 1
            review.append({"name": person["name"], "status": entry.get("status", "missing"),
                           "detail": f"title lookup returned {entry.get('status')!r}",
                           "candidate_url": entry.get("url")})
            continue

        if not entry.get("birth_year_checked"):
            entry["birth_year"] = wikipedia.fetch_birth_year(entry["qid"]) if entry.get("qid") else None
            entry["birth_year_checked"] = True
            cache[key] = entry
            if index % CACHE_SAVE_EVERY == 0:
                save_cache(cache, cache_path)

        if not year_matches(expected_year, entry.get("birth_year")):
            counts["year_mismatch"] += 1
            review.append({"name": person["name"], "status": "year_mismatch",
                           "detail": f"expected birth year {expected_year}, "
                                     f"article subject has {entry.get('birth_year')}",
                           "candidate_url": entry.get("url")})
            continue

        client.table("persons").update({"wikipedia_url": entry["url"]}).eq("id", person["id"]).execute()
        counts["verified"] += 1
        if counts["verified"] % 100 == 0:
            print(f"  written {counts['verified']} link(s)...")

    save_cache(cache, cache_path)
    write_review_entries(review, review_path)

    print(f"Done. {counts['verified']} link(s) written.")
    for status, count in sorted(counts.items()):
        if status != "verified":
            print(f"  {status}: {count}")
    if review:
        print(f"{len(review)} person(s) need a manual look - see {review_path}.")
    return dict(counts)


if __name__ == "__main__":  # pragma: no cover - manual one-off script
    main()
