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
from typing import Dict, List, Optional, Tuple

import requests

from core.config import DATA_DIR
from core.matching import normalize_name

API_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = "achievement-age/0.1 (https://github.com/didrikfo/achievement-age) ingest script"
REQUEST_DELAY_SECONDS = 0.2
SEARCH_LIMIT = 10
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


def _search_candidates(name: str) -> List[Dict]:
    response = _get_json(
        {"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": SEARCH_LIMIT}
    )
    return response.get("search", [])


def _fetch_birth_claims(qid: str) -> List[Dict]:
    response = _get_json({"action": "wbgetentities", "ids": qid, "props": "claims"})
    entity = response.get("entities", {}).get(qid, {})
    return entity.get("claims", {}).get("P569", [])


def _plausible_for_event(birth_year: int, event_year: Optional[int]) -> bool:
    """The person must have been born by the event and not impossibly long before it."""
    if event_year is None:
        return True
    return birth_year <= event_year <= birth_year + MAX_LIFESPAN_YEARS


def lookup_birth_date(name: str, event_year: Optional[int], cache: Dict) -> Dict:
    """Resolve one name to a day-precision birth date, caching every outcome.

    Returns a dict whose "status" is one of "resolved", "ambiguous",
    "not_found", or "insufficient_precision". "resolved" also carries year,
    month, day and qid. Cache hits make no network request.
    """
    key = normalize_name(name)
    if key in cache:
        return cache[key]

    candidates = _search_candidates(name)
    if not candidates:
        return _remember(cache, key, {"status": "not_found", "name": name})

    plausible: List[Dict] = []
    saw_coarse_precision = False
    for candidate in candidates:
        for claim in _fetch_birth_claims(candidate["id"]):
            status, parsed = parse_birth_claim(claim)
            if status != "resolved":
                saw_coarse_precision = True
                continue
            if _plausible_for_event(parsed["year"], event_year):
                plausible.append({**parsed, "qid": candidate["id"], "label": candidate.get("label")})
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
                "candidates": [{"qid": item["qid"], "label": item["label"]} for item in plausible],
            },
        )
    if saw_coarse_precision:
        return _remember(cache, key, {"status": "insufficient_precision", "name": name})
    return _remember(cache, key, {"status": "not_found", "name": name})


def _remember(cache: Dict, key: str, result: Dict) -> Dict:
    cache[key] = result
    return result
