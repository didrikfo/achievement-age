"""QA harness for the display `event_phrase` values stored in Supabase.

Same two-phase shape as ingest.llm_utils / ingest.backfill_event_enrichment,
because a Claude Code subagent runs in between the two calls:

    python -c "from scripts.qa_event_phrases import prepare_chunks; prepare_chunks()"
    # ... dispatch one reviewing subagent per chunk file ...
    python -c "from scripts.qa_event_phrases import merge_reviews; merge_reviews()"

Read-only against Supabase: prepare_chunks only selects, and nothing here
writes back. Applying an accepted correction is a separate, deliberate step
(see apply_corrections.py output of merge_reviews).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import DATA_DIR  # noqa: E402
from core.db import get_client  # noqa: E402
from ingest.enrichment import check_facts_preserved, check_phrase_format  # noqa: E402

CHUNK_SIZE = 50
EVENTS_PAGE_SIZE = 1000

QA_DIR = DATA_DIR / "tmp" / "qa"
CHUNK_DIR = QA_DIR / "chunks"
REPORT_PATH = QA_DIR / "qa_report.json"

SELECT = (
    "id, name, text, event_phrase, year, month, day, age_days, "
    "event_type, source, reword_prompt_version, detailed_description, "
    "event_tags(tags(name))"
)


def fetch_all_events() -> List[Dict]:
    """Every event row with the fields a reviewer needs, tags flattened."""
    client = get_client()
    events: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select(SELECT)
            .order("id")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        for row in page:
            tag_rows = row.pop("event_tags", None) or []
            row["tags"] = [t["tags"]["name"] for t in tag_rows if t.get("tags")]
            events.append(row)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return events


def prepare_chunks(chunk_size: int = CHUNK_SIZE) -> List[Path]:
    """Write every event out in review-sized chunks, plus a heuristic pre-pass.

    The two existing advisory validators (check_phrase_format,
    check_facts_preserved) are run here and their output attached to each
    record as `_hints`, so a reviewing subagent starts from the same triage
    signal the ingest pipeline already had rather than re-deriving it.
    """
    events = fetch_all_events()
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    for stale in CHUNK_DIR.glob("*.json"):
        stale.unlink()

    for event in events:
        hints = []
        reason = check_phrase_format(event.get("event_phrase", ""), event.get("name", ""))
        if reason:
            hints.append(f"format: {reason}")
        missing = check_facts_preserved(event.get("text", ""), event.get("event_phrase", ""))
        if missing:
            hints.append("missing tokens: " + ", ".join(missing[:8]))
        event["_hints"] = hints

    paths: List[Path] = []
    for index in range(0, len(events), chunk_size):
        path = CHUNK_DIR / f"chunk_{index // chunk_size:04d}.json"
        path.write_text(
            json.dumps(events[index : index + chunk_size], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths.append(path)

    summary = {
        "total_events": len(events),
        "chunks": len(paths),
        "by_source": Counter(e.get("source") or "unknown" for e in events),
        "by_prompt_version": Counter(str(e.get("reword_prompt_version")) for e in events),
        "with_hints": sum(1 for e in events if e["_hints"]),
    }
    (QA_DIR / "corpus_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return paths


def merge_reviews() -> Dict:
    """Collate every chunk_*_review.json into one report keyed by event id."""
    findings: List[Dict] = []
    for path in sorted(CHUNK_DIR.glob("chunk_*_review.json")):
        findings.extend(json.loads(path.read_text(encoding="utf-8")))

    findings.sort(key=lambda f: (f.get("severity", "zzz"), f.get("id", 0)))
    by_severity = Counter(f.get("severity", "unknown") for f in findings)
    by_issue = Counter(i for f in findings for i in (f.get("issue_types") or []))

    report = {
        "reviewed_chunks": len(list(CHUNK_DIR.glob("chunk_*_review.json"))),
        "flagged": len(findings),
        "by_severity": by_severity,
        "by_issue_type": by_issue,
        "findings": findings,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "findings"}, indent=2))
    return report


if __name__ == "__main__":
    prepare_chunks()
