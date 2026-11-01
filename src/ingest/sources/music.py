"""Shared pieces of the music-event sources (Billboard number ones, Eurovision
winners): provenance labels, the pinned download list, artist-credit
splitting, and the deterministic sentences stored as events.text and
events.event_phrase.

Every music record is a known fact about a named act on a known day - the same
shape as the Nobel source, not the scraped-text corpus - so, unlike Nobel, no
LLM phrasing pass is needed either: the sentence templates below read
naturally without pronouns, which is the one thing a template can't get right.
See docs/superpowers/specs/2026-09-24-music-events-ingestion-design.md.
"""

from __future__ import annotations

import calendar
import re
from pathlib import Path
from typing import Dict, List, Optional

import requests

from core.config import DATA_DIR

MUSIC_RAW_DIR = DATA_DIR / "raw_data" / "music"

#: Provenance labels written to events.source, one per dataset so a bad
#: import can be found and removed by source alone.
HOT_100_SOURCE = "billboard_hot_100_number_ones"
BILLBOARD_200_SOURCE = "billboard_200_number_ones"
EUROVISION_SOURCE = "eurovision_winners"
MUSIC_SOURCES = (HOT_100_SOURCE, BILLBOARD_200_SOURCE, EUROVISION_SOURCE)

#: Record kind -> events.source. The order is also the order same-day clauses
#: are joined in when one person has several records on one day.
KIND_SOURCES: Dict[str, str] = {
    "hot100_number_one": HOT_100_SOURCE,
    "billboard200_number_one": BILLBOARD_200_SOURCE,
    "eurovision_win": EUROVISION_SOURCE,
}
KIND_ORDER: List[str] = list(KIND_SOURCES)

#: The one tag every music event gets - deterministic, like NOBEL_CATEGORY_TAGS.
MUSIC_TAG = "music"

#: Raw files, pinned to a commit so a rerun reads exactly the data the design
#: was checked against. Bump a commit deliberately to pick up newer charts.
SOURCE_FILES: Dict[str, str] = {
    "hot100_number_ones.csv": (
        "https://raw.githubusercontent.com/rfordatascience/tidytuesday/"
        "8117f5ff9bb2b9d3dd68f2485ff1a4c4d7c7166e/data/2025/2025-08-26/billboard.csv"
    ),
    "hot100_chart.csv": (
        "https://raw.githubusercontent.com/utdata/rwd-billboard-data/"
        "e538bc1c3d700581ee70e5ff60351047c3a8da67/data-out/hot-100-current.csv"
    ),
    "billboard200.csv": (
        "https://raw.githubusercontent.com/utdata/rwd-billboard-data/"
        "e538bc1c3d700581ee70e5ff60351047c3a8da67/data-out/billboard-200-current.csv"
    ),
    "eurovision.json": (
        "https://raw.githubusercontent.com/josago97/EurovisionDataset/"
        "2c26a729c780986a4b5da22ae1dae39b4a14a7b5/dataset/eurovision.json"
    ),
    "eurovision_countries.json": (
        "https://raw.githubusercontent.com/josago97/EurovisionDataset/"
        "2c26a729c780986a4b5da22ae1dae39b4a14a7b5/dataset/countries.json"
    ),
}


def fetch_sources(raw_dir: Path = MUSIC_RAW_DIR, force: bool = False) -> List[Path]:
    """Download every SOURCE_FILES entry into raw_dir, skipping files already there."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for filename, url in SOURCE_FILES.items():
        path = raw_dir / filename
        if force or not path.exists():
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            path.write_bytes(response.content)
        paths.append(path)
    return paths


# --- artist credits ---

_FEATURING = re.compile(r"\s+(?:ft\.|feat\.|featuring|with)\s+", re.IGNORECASE)
_DUO_SEPARATOR = re.compile(r"\s+(?:&|and)\s+", re.IGNORECASE)
_ANY_SEPARATOR = re.compile(r"\s+(?:&|and|x|/)\s+|,\s+", re.IGNORECASE)
#: Bands a Wikidata alias ties to one person: "Stone Temple Pilots" is an alias
#: on its later singer's item, and Sade and Daughtry are bands named after their
#: singers. Only Billboard 200 credits need this - the Hot 100 data hand-codes
#: groups - and the age of a band's member is not the age of the band.
KNOWN_GROUPS = {"Big Tymers", "Daughtry", "Il Divo", "Sade", "Stone Temple Pilots"}
#: First words that mark a split-off part as a backing band, not a person.
_BAND_PART_OPENERS = {"the", "his", "her", "their"}


def lead_credit(credit: str) -> str:
    """The credited act without its featured guests: "Mariah Carey ft. Jay-Z" -> "Mariah Carey".

    A group-name prefix before a colon goes too: "¥$: Kanye West & Ty Dolla
    $ign" credits the two people after it.
    """
    lead = _FEATURING.split(credit.strip(), maxsplit=1)[0].strip()
    return lead.split(": ", 1)[1].strip() if ": " in lead else lead


def split_credit(credit: str, structure: Optional[str] = None) -> List[str]:
    """The individual people a credit names, or [] for a group act.

    structure is the Hot 100 dataset's artist_structure code when known: "1"
    solo, "2" duo, "0" group of three or more, each optionally suffixed ".5"
    for a featured guest. A group gets no event - its age in days has no
    meaning, and picking one member to stand for it would be a guess.

    Without a structure code (Billboard 200, Eurovision), the credit is split
    on "&"/"and"/","/"x"/"/" and a part is kept only if it could plausibly be
    one person's name: "The ..." parts are bands ("Bruce Springsteen & The E
    Street Band" keeps only Springsteen), and so are "His/Her/Their ..." parts
    ("Paul Mauriat And His Orchestra").

    Either way, single words from a split are dropped ("Simon & Garfunkel",
    "Earth, Wind & Fire", "Peaches & Herb") because a lone surname, noun or
    nickname can't be resolved to a person safely - the births file's
    "Peaches" is a different musician from Peaches & Herb's. An unsplit
    single-word credit ("Madonna", "Adele") is kept - it's a whole stage name.
    """
    lead = lead_credit(credit)
    if structure is not None:
        base = structure.split(".")[0]
        if base == "1":
            return [lead]
        if base == "2":
            return _multi_word([part.strip() for part in _DUO_SEPARATOR.split(lead) if part.strip()])
        return []

    if lead.lower().startswith("the ") or lead in KNOWN_GROUPS:
        return []
    parts = [part.strip() for part in _ANY_SEPARATOR.split(lead) if part.strip()]
    if len(parts) == 1:
        return parts
    return _multi_word([part for part in parts if part.split()[0].lower() not in _BAND_PART_OPENERS])


def _multi_word(parts: List[str]) -> List[str]:
    return [part for part in parts if len(part.split()) > 1]


# --- sentences ---


def format_date(year: int, month: int, day: int) -> str:
    return f"{calendar.month_name[month]} {day}, {year}"


def _weeks(count: int) -> str:
    return "1 week" if count == 1 else f"{count} weeks"


def _others(names: List[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def event_clause(record: Dict, person: str) -> str:
    """What happened, worded to follow "{person} was when " - no pronouns needed."""
    kind = record["kind"]
    others = [name for name in record["names"] if name != person]
    if kind == "hot100_number_one":
        title = f'"{record["title"]}"'
        if others:
            title += f", recorded with {_others(others)},"
        return f"{title} reached number one on the Billboard Hot 100"
    if kind == "billboard200_number_one":
        with_others = f", recorded with {_others(others)}," if others else ""
        return f"the album {record['title']}{with_others} reached number one on the Billboard 200"
    if kind == "eurovision_win":
        with_others = f", performed with {_others(others)}," if others else ""
        return (
            f'"{record["title"]}"{with_others} won the Eurovision Song Contest {record["year"]} '
            f"for {record['country_name']}"
        )
    raise ValueError(f"unknown music record kind {kind!r}")


def event_detail(record: Dict) -> str:
    """The stable raw-fact sentence stored in events.text, shown as the event's caption."""
    kind = record["kind"]
    date = format_date(record["year"], record["month"], record["day"])
    if kind == "hot100_number_one":
        return (
            f'"{record["title"]}" by {record["credit"]} reached number one on the Billboard Hot 100 chart '
            f"dated {date}, and spent {_weeks(record['weeks_at_number_one'])} at the top."
        )
    if kind == "billboard200_number_one":
        return (
            f"{record['title']} by {record['credit']} reached number one on the Billboard 200 album chart "
            f"dated {date}, and spent {_weeks(record['weeks_at_number_one'])} at the top."
        )
    if kind == "eurovision_win":
        points = f", scoring {record['points']} points" if record.get("points") else ""
        return (
            f'{record["credit"]} won the Eurovision Song Contest {record["year"]} for {record["country_name"]} '
            f'with "{record["title"]}" at the final in {record["host_city"]} on {date}{points}.'
        )
    raise ValueError(f"unknown music record kind {kind!r}")


def build_event_phrase(name: str, clauses: List[str]) -> str:
    """"{name} was when {clause}." - several same-day clauses are joined with "and"."""
    return f"{name} was when {_others(clauses)}."
