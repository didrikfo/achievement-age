"""Wikidata birth-date lookup for people absent from the scraped births data.

The scraped births file only contains whoever appeared on a Wikipedia "on this
day" births section. Wikidata covers far more people, and is the only source
here that can push past that ceiling.

Only day-precision birth dates are usable, because the app compares ages in
days. Coarser precision is reported, not silently dropped - for older figures
it is a large and predictable category.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from core.config import DATA_DIR
from core.matching import normalize_name

API_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = "achievement-age/0.1 (https://github.com/didrikfo/achievement-age) ingest script"
REQUEST_DELAY_SECONDS = 0.2
SEARCH_LIMIT = 10
ENTITY_BATCH_SIZE = 50
MAX_LIFESPAN_YEARS = 120
DAY_PRECISION = 11

CACHE_PATH = DATA_DIR / "wikidata_persons_cache.json"


def load_cache(path: Path = CACHE_PATH) -> Dict:
    """Load the name -> outcome cache, or an empty dict if it isn't usable.

    A truncated file (from a run killed mid-write before save_cache became
    atomic) is treated like a missing one: Stage 3 is meant to be resumable, and
    re-fetching is far better than crashing every subsequent run.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache: Dict, path: Path = CACHE_PATH) -> None:
    """Persist the cache. Written as a JSON object, not the array core.io expects.

    Written to a temp file in the same directory and then os.replace'd into
    place, so interrupting a long Stage 3 run can never leave a half-written
    cache behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _get_json(params: Dict) -> Dict:
    """The only network call in this module - monkeypatched in tests.

    Rate-limited and identified by User-Agent, per Wikidata's API etiquette.
    """
    time.sleep(REQUEST_DELAY_SECONDS)
    response = requests.get(
        API_URL,
        params={**params, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def parse_birth_claim(claim: Dict) -> Tuple[str, Optional[Dict]]:
    """Turn one P569 claim into ("resolved", {year, month, day}) or ("insufficient_precision", None).

    Wikidata precision: 11 = day, 10 = month, 9 = year. Only 11 is usable. BC
    dates (a leading "-") are rejected too, since datetime.date can't hold them.
    """
    try:
        value = claim["mainsnak"]["datavalue"]["value"]
        if int(value["precision"]) != DAY_PRECISION:
            return "insufficient_precision", None
        time_string = value["time"]
    except (KeyError, TypeError, ValueError):
        return "insufficient_precision", None

    if not time_string.startswith("+"):
        return "insufficient_precision", None

    try:
        date_part = time_string[1:].split("T", 1)[0]
        year, month, day = (int(part) for part in date_part.split("-"))
    except (ValueError, IndexError):
        return "insufficient_precision", None

    if month == 0 or day == 0:
        return "insufficient_precision", None
    return "resolved", {"year": year, "month": month, "day": day}


def fetch_enwiki_urls(qids: List[str]) -> Dict[str, str]:
    """QID -> English Wikipedia article URL, for each item that has one."""
    urls: Dict[str, str] = {}
    for start in range(0, len(qids), ENTITY_BATCH_SIZE):
        response = _get_json(
            {
                "action": "wbgetentities",
                "ids": "|".join(qids[start : start + ENTITY_BATCH_SIZE]),
                "props": "sitelinks/urls",
                "sitefilter": "enwiki",
            }
        )
        for qid, entity in response.get("entities", {}).items():
            url = entity.get("sitelinks", {}).get("enwiki", {}).get("url")
            if url:
                urls[qid] = url
    return urls


def parse_death_claim(claim: Dict) -> Optional[Dict]:
    """One P570 claim -> {year, month, day, precision}, or None if it carries no usable date.

    Unlike a birth date, a coarse death date is still worth keeping: "died in
    1970" is enough to know a 1971 event came after it. month and day are None
    below day precision.
    """
    try:
        value = claim["mainsnak"]["datavalue"]["value"]
        precision = int(value["precision"])
        time_string = value["time"]
        year, month, day = (int(part) for part in time_string[1:].split("T", 1)[0].split("-"))
    except (KeyError, TypeError, ValueError):
        return None
    if not time_string.startswith("+") or precision < 9:
        return None
    return {
        "year": year,
        "month": month if precision >= 10 and month else None,
        "day": day if precision >= DAY_PRECISION and day else None,
        "precision": precision,
    }


def _english_names(entity: Dict) -> List[str]:
    """The entity's English label and aliases."""
    names = [entity.get("labels", {}).get("en", {}).get("value")]
    names.extend(alias.get("value") for alias in entity.get("aliases", {}).get("en", []))
    return [name for name in names if name]


def _search_candidates(name: str) -> List[Dict]:
    response = _get_json(
        {"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": SEARCH_LIMIT}
    )
    return response.get("search", [])


def _fetch_entity(qid: str) -> Dict:
    """One entity's claims, English label and aliases, and sitelinks - a single request."""
    response = _get_json(
        {"action": "wbgetentities", "ids": qid, "props": "claims|labels|aliases|sitelinks", "languages": "en"}
    )
    return response.get("entities", {}).get(qid, {})


def _plausible_for_event(birth_year: int, event_year: Optional[int]) -> bool:
    """The person must have been born by the event and not impossibly long before it."""
    if event_year is None:
        return True
    return birth_year <= event_year <= birth_year + MAX_LIFESPAN_YEARS


def lookup_birth_date(
    name: str,
    event_year: Optional[int],
    cache: Dict,
    candidate_filter: Optional[Callable[[Dict], bool]] = None,
    require_exact_name: bool = False,
) -> Dict:
    """Resolve one name to a day-precision birth date, caching every outcome.

    Returns a dict whose "status" is one of "resolved", "ambiguous",
    "not_found", or "insufficient_precision". "resolved" also carries year,
    month, day and qid. Cache hits make no network request.

    candidate_filter, when given, is called with each wbsearchentities result
    (id, label, description, match) and keeps only those it accepts - e.g. only
    musicians for a chart credit like "Prince" or "Madonna", where an
    unfiltered search is full of plausible-age namesakes. Its outcomes differ
    from an unfiltered lookup's for the same name, so a caller passing one must
    also pass its own cache dict.

    require_exact_name keeps a candidate only if the name equals its label, the
    alias the search matched, or any of its English aliases. wbsearchentities
    matches prefixes, so a search for "ABBA" can surface a person whose name
    merely starts with it - and it reports only the best-matching term, so a
    stage name that is one alias among several ("Brandy" for Brandy Norwood)
    needs the aliases fetched to be seen.

    Every plausible candidate also carries "death" (parse_death_claim of its
    first P570 claim, or None) and "sitelinks" (how many Wikimedia sites have
    a page on it - a fame measure). A resolved result has them at the top
    level, and an ambiguous one lists each candidate's, so a caller holding
    more evidence (an age at the event) can pick between them.
    """
    key = normalize_name(name)
    if key in cache:
        return cache[key]

    candidates = _search_candidates(name)
    if candidate_filter is not None:
        candidates = [candidate for candidate in candidates if candidate_filter(candidate)]
    if not candidates:
        return _remember(cache, key, {"status": "not_found", "name": name})

    plausible: List[Dict] = []
    saw_coarse_precision = False
    for candidate in candidates:
        entity = _fetch_entity(candidate["id"])
        if require_exact_name:
            names = [candidate.get("label"), (candidate.get("match") or {}).get("text"), *_english_names(entity)]
            if key not in {normalize_name(other) for other in names if other}:
                continue
        claims = entity.get("claims", {})
        for claim in claims.get("P569", []):
            status, parsed = parse_birth_claim(claim)
            if status != "resolved":
                saw_coarse_precision = True
                continue
            if _plausible_for_event(parsed["year"], event_year):
                deaths = [parse_death_claim(death) for death in claims.get("P570", [])]
                plausible.append(
                    {
                        **parsed,
                        "qid": candidate["id"],
                        "label": candidate.get("label"),
                        "death": next((death for death in deaths if death), None),
                        "sitelinks": len(entity.get("sitelinks", {})),
                    }
                )
            break

    if len(plausible) == 1:
        return _remember(cache, key, {"status": "resolved", "name": name, **plausible[0]})
    if len(plausible) > 1:
        return _remember(
            cache,
            key,
            {
                "status": "ambiguous",
                "name": name,
                "candidates": plausible,
            },
        )
    if saw_coarse_precision:
        return _remember(cache, key, {"status": "insufficient_precision", "name": name})
    return _remember(cache, key, {"status": "not_found", "name": name})


def _remember(cache: Dict, key: str, result: Dict) -> Dict:
    cache[key] = result
    return result
