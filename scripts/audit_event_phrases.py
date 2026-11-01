"""Audit every stored event_phrase for display bugs and size the re-phrasing backpass.

    python scripts/audit_event_phrases.py

Read-only (events are readable with the publishable key) and writes only a
local report, data/tmp/phrase_audit.json. It reports:

- rows the old display normalizer garbled - a phrase that was already
  name-onward but didn't match the literal " was when " (punctuation after
  "when", a line break, ...), so "{name} was when" got prepended and the hinge
  appeared twice. The display fix in core.matching renders these correctly
  now; the backpass rewrites them properly.
- rows failing ingest.enrichment.check_phrase_format, grouped by reason.
- how many rows the phrasing backpass would pick up (below
  REWORD_PROMPT_VERSION, not from a structured source), and the rest by source.
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Dict, List

from core.config import DATA_DIR
from core.db import get_client
from core.io import save_to_json
from core.matching import LEGACY_PHRASE_PREFIX, PHRASE_HINGE, normalize_name, phrase_shape
from ingest.backfill_event_enrichment import EVENTS_PAGE_SIZE, STRUCTURED_SOURCES
from ingest.enrichment import REWORD_PROMPT_VERSION, check_phrase_format

REPORT_PATH = DATA_DIR / "tmp" / "phrase_audit.json"


def _fetch_phrases(client) -> List[Dict]:
    rows: List[Dict] = []
    start = 0
    while True:
        page = (
            client.table("events")
            .select("id, name, text, event_phrase, reword_prompt_version, source")
            .range(start, start + EVENTS_PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < EVENTS_PAGE_SIZE:
            break
        start += EVENTS_PAGE_SIZE
    return rows


def old_display_doubled_hinge(event: Dict) -> bool:
    """Whether the pre-fix core.matching._phrase_body printed "was when" twice for this row.

    The old normalizer used a phrase as-is only when it contained the literal
    " was when " with `name` before it; anything else (bar the legacy prefix)
    got "{name} was when " prepended. That doubled the hinge whenever the
    phrase already contained one.
    """
    stripped = (event.get("event_phrase") or "").lstrip()
    lowered = stripped.lower()
    if lowered.startswith(LEGACY_PHRASE_PREFIX.lower()):
        return False
    hinge_at = lowered.find(PHRASE_HINGE)
    if hinge_at != -1 and normalize_name(event["name"]) in normalize_name(stripped[:hinge_at]):
        return False
    return "was when" in " ".join(lowered.split())


def audit(rows: List[Dict]) -> Dict:
    doubled: List[Dict] = []
    format_failures: List[Dict] = []
    format_reasons: Counter = Counter()
    shapes: Counter = Counter()
    by_source_version: Counter = Counter()
    backpass = 0

    for row in rows:
        if not row.get("event_phrase"):
            continue
        source = row.get("source") or "(none)"
        version = row.get("reword_prompt_version") or 0
        shapes[phrase_shape(row)] += 1
        by_source_version[f"{source} v{version}"] += 1
        if row.get("source") not in STRUCTURED_SOURCES and version < REWORD_PROMPT_VERSION:
            backpass += 1

        entry = {"id": row["id"], "name": row["name"], "source": source, "event_phrase": row["event_phrase"]}
        if old_display_doubled_hinge(row):
            doubled.append(entry)
        reason = check_phrase_format(row["event_phrase"], row["name"])
        if reason:
            # Group by the reason's fixed lead-in, not its row-specific detail.
            format_reasons[reason.split(":")[0].split(" names ")[0]] += 1
            format_failures.append({**entry, "reason": reason})

    return {
        "total_rows": len(rows),
        "backpass_pending": backpass,
        "shapes": dict(shapes),
        "by_source_and_version": dict(sorted(by_source_version.items())),
        "format_failure_reasons": dict(format_reasons.most_common()),
        "doubled_hinge_under_old_display": doubled,
        "format_failures": format_failures,
    }


def main() -> int:
    report = audit(_fetch_phrases(get_client()))
    save_to_json(REPORT_PATH, report)

    print(f"Events: {report['total_rows']}")
    print(f"Phrasing backpass would pick up: {report['backpass_pending']} (below v{REWORD_PROMPT_VERSION}, non-structured)")
    print(f"Shapes: {report['shapes']}")
    print("By source and prompt version:")
    for key, count in report["by_source_and_version"].items():
        print(f"  {key}: {count}")
    print(f"Doubled 'was when' under the old display: {len(report['doubled_hinge_under_old_display'])}")
    for entry in report["doubled_hinge_under_old_display"][:10]:
        print(f"  #{entry['id']} {entry['name']}: {entry['event_phrase'][:100]}")
    print(f"Format-check failures: {len(report['format_failures'])}")
    for reason, count in report["format_failure_reasons"].items():
        print(f"  {count:5d}  {reason}")
    print(f"Full report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
