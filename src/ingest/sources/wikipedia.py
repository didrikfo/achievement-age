"""English Wikipedia article lookup for the people in the events corpus.

Resolves a person's name to their Wikipedia article, and exposes the birth year
Wikidata holds for that article's subject so a caller can check the article is
about the right person. The failure that matters is not a missing link but a
wrong one - "John Smith" resolving to a disambiguation page or to a different
John Smith - so every candidate is verifiable against a birth year the caller
already knows.

Batching note: title resolution takes 50 names per request, so the whole corpus
costs ~34 requests. Birth years are fetched one item at a time on purpose. The
batched alternatives were measured and rejected: `action=wbgetentities` with
`props=claims` returns roughly 300 KB per entity, and the SPARQL endpoint
answers a ten-item VALUES query with HTTP 429.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

API_URL = "https://en.wikipedia.org/w/api.php"
STATEMENTS_URL = "https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/{qid}/statements"
USER_AGENT = "achievement-age/0.1 (https://github.com/didrikfo/achievement-age) ingest script"
REQUEST_DELAY_SECONDS = 0.2
TITLE_BATCH_SIZE = 50
BIRTH_DATE_PROPERTY = "P569"

#: Redirect chains are short; the cap only exists so a cyclic chain can't hang.
MAX_REDIRECT_HOPS = 5


def _get_json(url: str, params: Dict) -> Dict:
    """The only network call in this module - monkeypatched in tests.

    Rate-limited and identified by User-Agent, per Wikimedia's API etiquette.
    """
    time.sleep(REQUEST_DELAY_SECONDS)
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.json()


def _alias_map(query: Dict) -> Dict[str, str]:
    """from -> to for every title rewrite the API applied (normalization, redirects)."""
    alias: Dict[str, str] = {}
    for entry in query.get("normalized") or []:
        alias[entry["from"]] = entry["to"]
    for entry in query.get("redirects") or []:
        alias[entry["from"]] = entry["to"]
    return alias


def _final_title(name: str, alias: Dict[str, str]) -> str:
    """Follow a requested name through normalization and redirects to its final title."""
    title = name
    for _ in range(MAX_REDIRECT_HOPS):
        next_title = alias.get(title)
        if next_title is None or next_title == title:
            break
        title = next_title
    return title


def _missing(title: Optional[str] = None) -> Dict:
    return {"status": "missing", "title": title, "url": None, "qid": None}


def _resolve_batch(names: List[str]) -> Dict[str, Dict]:
    payload = _get_json(
        API_URL,
        {
            "action": "query",
            "titles": "|".join(names),
            "redirects": 1,
            "prop": "pageprops|info",
            "inprop": "url",
            "ppprop": "wikibase_item|disambiguation",
            "format": "json",
        },
    )
    query = payload.get("query") or {}
    alias = _alias_map(query)
    pages_by_title = {
        page.get("title"): page for page in (query.get("pages") or {}).values() if page.get("title")
    }

    results: Dict[str, Dict] = {}
    for name in names:
        page = pages_by_title.get(_final_title(name, alias))
        if page is None or "missing" in page or "invalid" in page:
            results[name] = _missing(page.get("title") if page else None)
            continue
        props = page.get("pageprops") or {}
        results[name] = {
            "status": "disambiguation" if "disambiguation" in props else "found",
            "title": page.get("title"),
            "url": page.get("fullurl"),
            "qid": props.get("wikibase_item"),
        }
    return results


def resolve_titles(names: List[str]) -> Dict[str, Dict]:
    """Resolve article titles for names, keyed by the name that was requested.

    Each value is {"status", "title", "url", "qid"}, where status is one of
    "found", "missing" or "disambiguation". A name the API never mentions is
    reported as missing rather than omitted, so callers can rely on every
    requested name having an entry.
    """
    results: Dict[str, Dict] = {}
    for start in range(0, len(names), TITLE_BATCH_SIZE):
        results.update(_resolve_batch(names[start : start + TITLE_BATCH_SIZE]))
    return results


def _year_from_time(time_string: str) -> Optional[int]:
    """Year from a Wikidata time literal like "+1879-03-14T00:00:00Z".

    None for BC dates (a leading "-") and anything unparseable. Precision is
    ignored: even a year-precision claim carries the year, which is all this
    module compares.
    """
    if not time_string.startswith("+"):
        return None
    try:
        return int(time_string[1:].split("-", 1)[0])
    except ValueError:
        return None


def fetch_birth_year(qid: str) -> Optional[int]:
    """The birth year on a Wikidata item, or None if it has no usable one."""
    payload = _get_json(STATEMENTS_URL.format(qid=qid), {"property": BIRTH_DATE_PROPERTY})
    for statement in payload.get(BIRTH_DATE_PROPERTY) or []:
        content = ((statement.get("value") or {}).get("content")) or {}
        year = _year_from_time(content.get("time", "")) if isinstance(content, dict) else None
        if year is not None:
            return year
    return None
