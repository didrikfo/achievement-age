Phase 0 — Repo hygiene (quick wins)

Adopt a src/ layout

Actions:

Create src/{app,core,ingest}/__init__.py.

Move existing files into the closest match (see Phase 2/3).

Add pyproject.toml with project.scripts (CLI stubs) and dev tools.

Done when: pip install -e . works and you can python -c "import core".

Set up code quality defaults

Actions:

Add ruff, black, mypy configs in pyproject.toml.

Add pre-commit hooks (optional).

Done when: ruff ., black --check ., mypy src all pass (even if permissive).

Phase 1 — Core primitives (centralize shared logic)

Create core modules

Actions:

src/core/models.py: dataclasses Person, Event (computed age_days property).

src/core/age.py: one function age_in_days(date, birth_date) + (years, rem_days).

src/core/io.py: JSON helpers (load_json, save_json) using pathlib.Path.

src/core/matching.py: name/alias matching and “nearest age” logic in one place.

Done when: app & ingestion import only from core.* for these concerns.

Remove duplicated age/matching logic

Actions:

Replace scattered age calculations with core.age.

Replace substring/regex matchers with core.matching functions.

Done when: a search for def calculate_age or custom matchers returns only core/.

Phase 2 — Database: schema + bootstrap + migration

Create schema bootstrap

Actions:

Put the SQL DDL (source/person/person_alias/tag/person_tag/event/triggers) into scripts/schema.sql.

Write src/core/db.py with connect() context manager (PRAGMA foreign_keys=ON).

Add a CLI ages db init --db ages.sqlite --schema scripts/schema.sql.

Done when: running the CLI creates an ages.sqlite with all tables/triggers.

Migrate existing JSON into SQLite

Actions:

Write scripts/json_to_sqlite.py:

Load people & events JSON.

Insert a synthetic source (e.g., “LegacyJSON”).

Upsert persons (with aliases if available), then events (compute age_days via triggers).

Keep a backup copy of all input files under data/raw/legacy/.

Done when: row counts in SQLite roughly match JSON counts; a spot-check query returns expected results.

Add indices & verify performance

Actions:

Ensure ix_event_age exists.

Run a quick EXPLAIN QUERY PLAN on SELECT * FROM event WHERE age_days=? to confirm it uses the index.

Done when: exact-age queries are instantaneous (< a few ms locally).

Phase 3 — Ingestion: pluggable sources

Define ingestion protocol

Actions:

src/ingest/base.py with PersonRow, EventRow, IngestSource interface.

src/ingest/runner.py to orchestrate: create source, upsert person (aliases, tags), then event.

Done when: from ingest.runner import run_ingest ingests one mock source end-to-end.

Implement DB upserts

Actions:

src/core/db_ops.py with get_or_create_source, get_or_create_tags, upsert_person, upsert_event, _resolve_person_id (alias/name+birth-year fallback).

Done when: re-ingesting the same source is idempotent (no dupes).

Add two example adapters

Actions:

src/ingest/sources/musicians_csv.py (hardcode tag “music”).

src/ingest/sources/nobel_json.py (map category → tags; add prize events).

Done when: running both on sample files yields rows with correct tags and event ages.

Introduce an ingestion CLI

Actions:

ages ingest musicians --people <csv> --events <csv>.

ages ingest nobel --file <json>.

Done when: you can ingest with a single command per source.

Phase 4 — App updates (Streamlit or CLI/UI)

Switch app reads to SQLite

Actions:

Replace JSON loads with sqlite3 queries (via a small core/query.py).

Implement:

get_events_by_exact_age(age_days, tag_label=None).

get_events_near_age(age_days, window=7, tag_label=None).

Done when: the UI shows exact matches; if none, nearest matches appear with a banner.

Fix Streamlit correctness nits

Actions:

st.date_input(min_value=date(1900,1,1)) (use datetime.date, not strings).

Remove empty st.success() call or provide a message.

Use core.age to render “X years, Y days”.

Done when: no Streamlit warnings about bad types; age display matches tests.

Add tag filter UI

Actions:

Query SELECT label FROM tag ORDER BY label to populate a multi-select.

Pass selected tag (or None) into queries.

Done when: choosing a tag filters results immediately.

Phase 5 — LLM cleanup for display_text (robust & optional)

Harden LLM output parsing

Actions:

In llm_utils.py:

Update prompt to demand strict JSON (no code fences).

Replace ast.literal_eval with json.loads + basic schema checks.

Retry once with a stricter instruction if parsing fails.

Provide a deterministic fallback template if LLM fails.

Done when: malformed outputs don’t crash ingestion; events still get a usable display_text.

Batch & resume

Actions:

Process events in small chunks (e.g., 100).

Skip events that already have display_text.

Write intermediate artifacts to data/tmp/ for debugging.

Done when: re-running the job only processes new/empty rows.

Phase 6 — Disambiguation & audit

External IDs first

Actions:

Modify adapters to include stable IDs whenever the source provides them (Wikidata QID, MusicBrainz, Nobel ID, etc.).

Store in person.external_uid (per-source uniqueness).

Done when: majority of matches link by ID without fallback.

Blocking + aliases

Actions:

Normalize names (casefold, strip punctuation/diacritics) before matching.

Use blocking on (normalized_name, birth_year) and/or alias table.

Maintain person_alias during ingestion (include known nicknames).

Done when: fallback matches are deterministic and low-false-positive.

Add fuzzy matching (narrow scope)

Actions:

Optional: use rapidfuzz to score candidates returned by blocking; set a conservative threshold (e.g., ≥90).

Done when: you can resolve minor spelling variants without manual work.

LLM last-mile (optional, guarded)

Actions:

Only invoke when candidates ≤5 after blocking.

Prompt the LLM with the candidate rows; require it to return { "match_person_id": <id or null>, "confidence": 0..1 }.

Done when: a handful of tough cases are resolved automatically; no hallucinated IDs.

Audit trail

Actions:

Add match_audit table (as sketched earlier).

Log each non-trivial match with method and confidence.

Create a simple report CLI: ages audit low-confidence --threshold 0.7.

Done when: you can list edge cases to review manually.

Phase 7 — Tests, fixtures, and docs

Unit tests (pytest)

Actions:

tests/test_age.py: leap years, Feb 29 birthdays; (years, days) splits.

tests/test_matching.py: alias vs. substring; fuzzy thresholds.

tests/test_db.py: triggers compute age_days; unique upserts; tag joins.

tests/test_queries.py: exact/nearest age queries with/without tags.

Done when: pytest -q passes locally.

Tiny fixtures

Actions:

Add mini CSV/JSON files in tests/data/ for musicians and nobel.

Done when: tests don’t depend on network or big datasets.

README + CONTRIBUTING

Actions:

Document the data flow: ingest → db → queries → app.

Include “How to add a new source” with a short adapter template.

List the CLI commands.

Done when: you can copy-paste steps and get a working local setup.

Phase 8 — Nice-to-haves (defer if busy)

Full-text search (FTS5) for people

Actions:

Create person_fts virtual table and a trigger to sync names/aliases.

Add a quick search box in the app (optional).

Done when: you can search “marie sklodowska” and find Marie Curie.

Nearest-age caching

Actions:

If nearest-age queries are common, precompute a small windowed index or memoize in the app session state.

Done when: UI feels snappy even with sparse data.

Event-level tags (only if needed)