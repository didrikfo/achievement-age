"""Parsing for Eurovision Song Contest winners (josago97/EurovisionDataset).

The dataset carries each contest's final date and every performance's
placing, so a winner is simply place 1 in the "final" round - four of them in
1969's tie.

Its scraper split hyphenated names at the hyphen, spilling the second half
into the song field: 1961's winner appears as artist "Jean", song "Claude
Pascal" (really Jean-Claude Pascal, "Nous les amoureux"), and likewise
1973's Anne-Marie David and 1975's Teach-In. Each contestant's lyrics[0].title
is unaffected, so repair_credit() rebuilds the pair from it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from ingest.sources.music import MUSIC_RAW_DIR, split_credit

EUROVISION_PATH = MUSIC_RAW_DIR / "eurovision.json"
COUNTRIES_PATH = MUSIC_RAW_DIR / "eurovision_countries.json"


def repair_credit(artist: str, song: str, lyrics_title: str | None) -> Tuple[str, str]:
    """Undo the hyphen split, using the lyrics title as the song's ground truth.

    - song is a prefix of the lyrics title ("Boom Bang" / "Boom Bang-a-Bang"):
      only the song was cut short, so take the full title.
    - song isn't in the title at all ("Claude Pascal" / "Nous les amoureux"):
      the song field holds the artist's second half, so rejoin it with a hyphen.
    """
    if not lyrics_title:
        return artist, song
    if lyrics_title.casefold().startswith(song.casefold()):
        return artist, lyrics_title
    return f"{artist}-{song}", lyrics_title


def load_eurovision_winners(path: Path = EUROVISION_PATH, countries_path: Path = COUNTRIES_PATH) -> List[Dict]:
    """One record per winning entry, dated to the night of the final."""
    with open(countries_path, "r", encoding="utf-8") as handle:
        country_names: Dict[str, str] = json.load(handle)
    with open(path, "r", encoding="utf-8") as handle:
        contests = json.load(handle)

    records: List[Dict] = []
    for contest in contests:
        finals = [round_ for round_ in contest.get("rounds", []) if round_.get("name") == "final"]
        if not finals or not finals[0].get("date"):
            continue
        final = finals[0]
        year, month, day = (int(part) for part in final["date"].split("-"))
        contestants = {contestant["id"]: contestant for contestant in contest["contestants"]}

        for performance in final.get("performances", []):
            if performance.get("place") != 1:
                continue
            contestant = contestants[performance["contestantId"]]
            lyrics = contestant.get("lyrics") or []
            artist, song = repair_credit(
                contestant["artist"], contestant["song"], lyrics[0].get("title") if lyrics else None
            )
            # Since 2016 the jury and televote tallies sit alongside the total,
            # so summing every entry would double-count.
            totals = [score.get("points") for score in performance.get("scores") or [] if score.get("name") == "total"]
            points = totals[0] if totals else None
            records.append(
                {
                    "kind": "eurovision_win",
                    "record_key": f"eurovision:{year}:{contestant['country']}",
                    "title": song,
                    "credit": artist,
                    "artist_structure": None,
                    "names": split_credit(artist),
                    "country_name": country_names.get(contestant["country"], contestant["country"]),
                    "host_city": contest["city"],
                    "points": points,
                    "expected_age_years": None,
                    "year": year,
                    "month": month,
                    "day": day,
                }
            )
    return records
