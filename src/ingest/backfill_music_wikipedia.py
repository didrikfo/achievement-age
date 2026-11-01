"""Fill persons.wikipedia_url for musicians backfill_person_wikipedia can't link.

    ./venv/Scripts/python.exe -m ingest.backfill_music_wikipedia

backfill_person_wikipedia looks a person's name up as an article title, which
fails for stage names that mean something else: "Prince", "Drake" and "Usher"
are all other articles. ingest.resolve_music_persons already tied these people
to a Wikidata item, so their English Wikipedia sitelink is the exact article
("Prince (musician)", "Pitbull (rapper)") with no title to guess.

Run after backfill_person_wikipedia, from data/tmp/music_resolved.json. Safe to
rerun: only rows without a URL are read, so a hand-corrected value survives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from core.db import get_client
from core.io import load_json
from ingest.backfill_person_wikipedia import fetch_persons_missing_url
from ingest.migrate_music_to_supabase import person_name
from ingest.resolve_music_persons import RESOLVED_PATH
from ingest.sources import wikidata


def qids_by_person_name(records: List[Dict]) -> Dict[str, str]:
    """persons.name -> Wikidata QID, for every resolved record that has one."""
    return {
        person_name(record["name"]): record["wikidata_qid"] for record in records if record.get("wikidata_qid")
    }


def plan_links(
    persons: List[Dict], qids: Dict[str, str], urls: Dict[str, str]
) -> Tuple[List[Tuple[int, str]], List[str]]:
    """([(person id, url)], names of musicians whose item has no English article)."""
    links: List[Tuple[int, str]] = []
    unlinked: List[str] = []
    for person in persons:
        qid = qids.get(person["name"])
        if qid is None:
            continue
        if qid in urls:
            links.append((person["id"], urls[qid]))
        else:
            unlinked.append(person["name"])
    return links, unlinked


def main(resolved_path: Path = RESOLVED_PATH) -> Dict[str, int]:
    client = get_client()
    qids = qids_by_person_name(load_json(resolved_path))
    persons = [person for person in fetch_persons_missing_url(client) if person["name"] in qids]

    urls = wikidata.fetch_enwiki_urls(sorted({qids[person["name"]] for person in persons}))
    links, unlinked = plan_links(persons, qids, urls)
    for person_id, url in links:
        client.table("persons").update({"wikipedia_url": url}).eq("id", person_id).execute()

    if unlinked:
        print(f"No English article for: {sorted(unlinked)}")
    return {"musicians_missing_a_link": len(persons), "written": len(links), "no_article": len(unlinked)}


if __name__ == "__main__":  # pragma: no cover - manual one-off script
    print(main())
