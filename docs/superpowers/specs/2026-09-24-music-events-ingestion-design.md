# Music events ingestion

## Context

The calendar's corpus is general history plus Nobel Prize laureates. This pass adds a music focus:
roughly 1,000–2,000 day-precise events about famous musicians. The ask named album releases, band
formations, premieres of symphonies/musicals/shows, music awards, and famous live performances.

Like Nobel (`2026-08-28-nobel-prize-ingestion-design.md`), these are structured sources where the
subject is already named. The pipeline copies Nobel's shape: parse → resolve birth dates → persons
upsert and guards → insert. There is one simplification (no LLM phrasing pass, see below) and one new
guard (birth-date conflicts, see below).

### What the sources could and couldn't be

This was researched from a sandbox whose network policy allowed only GitHub (`raw.githubusercontent.com`
and `git clone` of public repos). Wikidata (both `query.wikidata.org` and `www.wikidata.org`),
Wikipedia, MusicBrainz, Discogs, Kaggle, Hugging Face, Zenodo, DBpedia and the Internet Archive were
all blocked, and so were web fetches of them. That shaped the choice.

- **Chosen: chart number ones and Eurovision winners.** Every date is exact and independently
  checkable, and these three GitHub-hosted datasets were verified against each other:
  - *Billboard Hot 100 number ones.* Source is TidyTuesday 2025-08-26, Chris Dalla Riva's hand-coded
    dataset: 1,177 songs from 1958–2025. It records the chart date each song first hit number one, a
    solo/duo/group code (`artist_structure`), and the lead's age (`front_person_age`).
  - *Billboard 200 number-one albums.* Derived from `utdata/rwd-billboard-data`'s full weekly archive:
    1,132 albums with a performing artist, once soundtracks and cast recordings are dropped.
  - *Eurovision Song Contest winners.* From `josago97/EurovisionDataset`: 71 winners from 1956–2024,
    including the four-way tie of 1969, dated to the night of the final.
- **Rejected: day-precise album release dates.** The one day-precision source reachable was
  MusicOSet's Spotify release dates. Spot-checked against well-known albums, only about 60% were right.
  Graceland, Nevermind, Pet Sounds and The Joshua Tree are all off by days to months, because Spotify
  often lists a reissue date. Chart number ones stand in: a dated, verifiable milestone for the same
  albums.
- **Deferred: band formations, premieres, Grammys, famous concerts.** Every usable source for these
  is Wikidata/Wikipedia (inception P571, date of first performance P1191, award P166 + point in time,
  concert items) or MusicBrainz. GitHub holds only year-precision copies: Grammy winners by year with
  no ceremony dates, and Rock Hall inductees by year. See Follow-ups.

### What's actually in the files (things the design corrects for)

- **TidyTuesday mis-dates 8 Hot 100 number ones.** Examples: Whitney Houston's "I Wanna Dance with
  Somebody" is given as 1987-07-27, a Monday; the chart that put it at number one was dated Saturday
  1987-06-27. Bon Jovi's "Livin' on a Prayer" is 10 days off, and Silver Convention's "Fly, Robin,
  Fly" 20 days. The utdata weekly archive is the date authority (see `verify_hot100_dates`). 1,100
  dates agree exactly by title. The other 69 agree by date but differ in title formatting ("Come
  Together" vs. "Come Together/Something"). After correction, none are left unverified.
- **`front_person_age` averages everyone credited.** That includes a featured guest (`artist_structure`
  "1.5"), so it only pins down one person for a plain solo credit ("1"). It also sometimes follows an
  artist's publicly claimed birth year: Jan Hammer and Del Shannon come out two years younger than
  they were.
- **The Eurovision scraper split hyphenated names at the hyphen.** 1961's winner is stored as artist
  "Jean", song "Claude Pascal"; the real entry is Jean-Claude Pascal, "Nous les amoureux". The same
  happened to 1973's Anne-Marie David and 1975's Teach-In. Song titles were cut short too ("Boom Bang"
  for "Boom Bang-a-Bang"). Each contestant's `lyrics[0].title` is intact, and `repair_credit` rebuilds
  the pair from it.
- **Eurovision scores since 2016 list jury and televote beside the total.** Summing them doubled
  Jamala's 534 points, so only the `total` entry is used.
- **The scraped births file is a poor source for musicians.** It lacks Madonna, Mariah Carey, Taylor
  Swift, John Lennon and Elvis Presley. Its one "Michael Jackson" is not the singer, and its "Ray
  Charles" (born 1918) is a different Ray Charles from the singer (born 1930).

## Architecture

### `src/ingest/sources/music.py` — shared pieces

- `MUSIC_SOURCES`: one `events.source` label per dataset (`billboard_hot_100_number_ones`,
  `billboard_200_number_ones`, `eurovision_winners`), so a bad import can be removed by source alone.
- `SOURCE_FILES` and `fetch_sources()`: the raw files, pinned to commit SHAs so a rerun reads exactly
  the data this design was checked against. They are downloaded into `data/raw_data/music/`, which is
  gitignored like the Nobel CSV.
- `split_credit(credit, structure)`: who a credit names.
  - With a Hot 100 structure code: solo gives the lead, duo splits the lead, group gives nobody.
  - Without one: split on `&`/`and`/`,`/`x`/`/`, drop "The …" and "His/Her/Their …" backing-band
    parts, and drop single words produced by a split ("Simon & Garfunkel", "Peaches & Herb"). The
    births file's "Peaches" is a different musician. An unsplit single-word credit ("Madonna") is a
    whole stage name and is kept.
  - A group gets no event. Its age in days is meaningless, and picking a member to stand for it would
    be a guess.
- `event_clause`, `event_detail`, `build_event_phrase`: the deterministic sentences (next section).

### Phrasing: templates, not an LLM pass

Nobel needed an LLM to turn a citation into prose. These records don't: every template reads
naturally without a pronoun, which is the one thing a template can't get right. For example:

> Whitney Houston was when "I Wanna Dance with Somebody (Who Loves Me)" reached number one on the
> Billboard Hot 100.

A duo partner is named in the clause ("…, recorded with Bruno Mars, …"). `events.text`, shown as the
caption, carries the fuller fact: the full credit, the chart date, weeks at number one, and the
Eurovision host city and points. Every generated phrase passes `ingest.enrichment.check_phrase_format`
(a test covers this). Tags are deterministic: every music event gets `music`.

Skipping the LLM pass removes the chunk/dispatch/merge step. The music rows therefore keep
`reword_prompt_version` at its default of 0, which is exactly what the generic re-phrasing backfill
selects on, so they need the same protection Nobel rows have (see the backfill section).

### `src/ingest/sources/billboard.py` and `src/ingest/sources/eurovision.py` — parsing only

- `load_hot100_number_ones` reads TidyTuesday. `expected_age_years` is set only for `artist_structure
  == "1"`.
- `load_chart_number_ones` reads either utdata archive into `(title, performer) -> (first week at
  number one, weeks at number one)`.
- `verify_hot100_dates` works in three steps:
  1. A TidyTuesday date that is a first-number-one date in the archive is kept. Only one song can first
     reach number one in a week, so no title match is needed.
  2. Otherwise, the archive date for the same title within 60 days replaces it.
  3. Otherwise the record is dropped and reported.
- `load_billboard200_number_ones` derives number ones from the archive and looks up each performer's
  hand-coded structure from the Hot 100 data, via `known_credit_structures`. That's how "The Weeknd"
  counts as one person and "Fleetwood Mac" as a band.
- `load_eurovision_winners` takes place 1 in the `final` round, applying `repair_credit` and the
  `total` score.

### `src/ingest/resolve_music_persons.py` — birth dates

Candidate birth dates are tried per name, in order:

1. **Wikidata.** Uses `lookup_birth_date` with two new arguments. `candidate_filter`
   (`musician_candidate_filter`) keeps only search results whose description reads as a musician's,
   before any entity is fetched. `require_exact_name` then keeps a candidate only if the credit equals
   its label, the alias the search matched, or any English alias. `wbsearchentities` matches prefixes,
   so "ABBA" must not match a person whose name starts with it. It also reports only its best-matching
   term, so checking that term alone misses Beyoncé (it matched "Beyoncé Giselle Knowles Carter") and
   Brandy (label "Brandy Norwood", alias "Brandy"). The lookup uses its own cache,
   `data/wikidata_musicians_cache.json`, because filtered outcomes differ from the unfiltered cache
   the historical corpus shares.
2. **Births file, musicians only.** Only entries whose description reads as a musician's
   (`MUSIC_DESCRIPTION`) are used, so the file's footballer "Michael Jackson" can never match.

Wikidata goes first because only it knows death dates (next subsection). A name resolved from the
births file alone is listed as `death_date_unknown` for a manual posthumous check.

A candidate is accepted only if it passes `check_resolved_record` for **every** record of that name:

- the age at the event is between 5 and 120 years, and
- for a solo Hot 100 act, the age is within 2 years of `front_person_age`.

One failure proves the date belongs to someone else. A Billboard 200 album has no age to check against,
so it must not quietly keep that date either. This is how the births file's 1918 "Ray Charles" would
be rejected for all four of the singer's records. A rejected Wikidata date does *not* fall through to
the births file. Wikidata found an exact-name musician, so a births-file date for the same name would
only be a guess between two sources that disagree. Ernie K-Doe is the one case: Wikidata says 1936,
the births file and `front_person_age` say 1933, and he is left out.

An ambiguous Wikidata result (several exact-name musicians) goes through `settle_ambiguous`:

1. Keep the candidates that pass every record's age check.
2. Drop any candidate whose death would make every record posthumous. The 1914-born Ken Carson, who
   died in 1994, had no 2020s number ones.
3. If several remain, accept the most-linked candidate, but only if it has at least 10 Wikidata
   sitelinks and at least 4× the runner-up's. A chart-topper is famous, and a namesake rarely is:
   Chris Brown has 78 sitelinks to his namesake's 0, and John Mayer 61 to 7. Bobby Lewis, 9 to 3, is
   not a clear winner and stays unresolved.

`front_person_age` is plain wrong for two songs, found when the Wikidata run rejected the right person.
George Harrison was 44 at "Got My Mind Set On You", not 47, and Carly Rae Jepsen was 26 at "Call Me
Maybe", not 31. `FRONT_PERSON_AGE_ERRATA` in `billboard.py` skips the age check for those records.

#### Posthumous records

A chart can crown a record after its artist's death: Otis Redding's "Dock of the Bay", John Lennon's
"(Just Like) Starting Over", Aaliyah's last album, and many rap albums. An age at such an event is
meaningless ("Otis Redding was 26 years old when…" is false), so `lookup_birth_date` also returns the
person's death date (P570). It comes from the same entity fetch, so it costs no extra request. Any
record after the death is dropped and listed as `posthumous_record`. A year- or month-precision death
date rules out any event that could have followed it. The TidyTuesday `posthumous` column can't do
this job. It flags the song, not a person: "Slow Motion" is flagged for its featured guest Soulja Slim,
not for Juvenile. It also covers only the Hot 100.

#### One name per person

The charts spell some acts differently: "Beyonce" on the Billboard 200 and "Beyoncé" on the Hot 100.
`persons` is keyed by exact name, so names that resolve to the same QID are merged by
`canonicalize_names`. The merged name is the spelling that equals the Wikidata label, or else the one
with the most records.

`--offline` skips Wikidata. Run offline in the sandbox, the births file alone resolves 115 people
across 233 records.

### `src/ingest/migrate_music_to_supabase.py` — guards and the write

- **`merge_same_day`: one event per person per day, built before anything touches Supabase.** A
  person can top both charts in one week. Taylor Swift's Midnights and "Anti-Hero" were both number
  one on the charts dated 2022-11-05, and Rod Stewart's "Maggie May" and Every Picture Tells a Story
  both on 1971-10-02. Their clauses are joined with "and".
- **Normalized-name collisions.** `find_normalized_name_collisions` is reused from the Nobel module
  unchanged.
- **Birth-date conflicts (new).** `persons` has no birth-date column and is upserted by exact name, so
  a musician sharing a name with someone already in the calendar would silently merge into them. An
  existing person's events pin down whose birthday their row stands for (event date minus
  `age_days`). This is the same inference `backfill_person_wikipedia` already relies on. If our
  resolved date isn't among them, the name is flagged in `data/tmp/music_person_review.json` and
  skipped (detect-don't-guess). The existing corpus's "Ray Charles" is the expected first hit.
- **Duplicate-day guard.** An events row already present for the person that day blocks the new one,
  logged to `data/tmp/music_duplicate_review.json`. The "on this day" corpus already covers a few of
  these, such as ABBA's Eurovision win.
- **Two phases.** `prepare` writes `data/music_displayable.json` for review. `insert` writes `events`
  and `event_tags`, and has the same rerun guard as `insert_nobel_events`.

### `backfill_event_enrichment.py`: `STRUCTURED_SOURCES`

The Nobel-only exclusion becomes `STRUCTURED_SOURCES = {NOBEL_SOURCE, *MUSIC_SOURCES}`, in both
`pending_events` (tags mode) and `pending_phrasing_events`. Without it, the next generic phrasing pass
would re-word every music row (they sit at version 0) and re-tag them by LLM choice.

## Expected yield

There are 2,380 source records; 618 of them credit only groups. The remaining 1,816 person-records
collapse to 1,773 person-days, the upper bound. 1,130 of those belong to acts the Hot 100 data
hand-codes as solo or duo, which are real individuals whose birth dates Wikidata should resolve. Most of
the rest are solo album artists without a Hot 100 number one (Bob Dylan, Bruce Springsteen, Garth
Brooks), plus band names that will correctly fail to resolve. The realistic landing is about
1,100–1,400 events.

**Actual, with Wikidata reachable (2026-09-24):** 588 of 769 credited names resolve, covering 1,493
person-records and 1,451 person-days. 1,488 records come from Wikidata and 5 from the births file.
Excluded:
- 28 posthumous records;
- 1 name left ambiguous (Bobby Lewis);
- 1 name whose sources contradict each other (Ernie K-Doe);
- 334 names not found. Nearly all are band names; the few people among them are Monica, Nas,
  "Weird Al" Yankovic via Wikidata, and malformed credits such as "Jay Z Kanye West".

The duplicate-day and birth-date-conflict guards in `prepare` trim the final count a little further.

## Testing

- `tests/test_music.py`: credit splitting (with the real Peaches & Herb and Springsteen cases), every
  sentence template, the phrase format check, and `fetch_sources` skipping existing files.
- `tests/test_billboard.py`: parsing, archive number-one derivation, all three `verify_hot100_dates`
  outcomes (including the real Whitney Houston typo), and Billboard 200 structure lookup. Real-file
  tests skip when the files aren't downloaded.
- `tests/test_eurovision.py`: `repair_credit` (the real 1961 case), total-only points, ties, and a
  contest with no final.
- `tests/test_resolve_music_persons.py`: the musician-only births lookup, the Wikidata candidate
  filter, the age tolerance, the all-records rejection, and fall-through to Wikidata (the real Ray
  Charles case).
- `tests/test_migrate_music_to_supabase.py`: same-day merge (the real Midnights/"Anti-Hero" week),
  birth-date conflicts, the duplicate-day guard, and both orchestrators against a mocked client.
- `tests/test_wikidata.py` and `tests/test_backfill_event_enrichment.py` gain the `candidate_filter`
  and music-exclusion cases.

## Rollout order

1. `fetch_sources()`: downloads about 70 MB, pinned.
2. `python -m ingest.resolve_music_persons`: needs Wikidata access; rerunnable from cache.
3. `python -m ingest.migrate_music_to_supabase prepare`: persons upsert and guards. Review the files it
   writes.
4. `python -m ingest.migrate_music_to_supabase insert`.
5. Optionally, `python -m ingest.backfill_person_wikipedia` gives the new persons their links. No change
   is needed: it already verifies against event-implied birth years.

## Follow-ups

- **Wikidata-backed premieres, band formations, awards and concerts.** A SPARQL source would cover the
  categories this pass couldn't, with QIDs and sitelink counts as a fame filter. It could not be built
  and verified here because every Wikidata endpoint was blocked. With `query.wikidata.org` allowed, it
  slots into this same pipeline as more record kinds.
- **Band events.** A Wikidata membership (P463/P527) lookup could attribute a group's number one to
  its members, recovering the 618 group records.
