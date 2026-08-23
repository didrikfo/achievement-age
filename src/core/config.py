"""Project paths and shared constants."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# config.py lives in src/core so we step three levels up to reach the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

#: The fixed tag taxonomy. Lives here rather than in ingest.enrichment because
#: core.matching needs it for filtering, and a core module must not import from
#: ingest. ingest.enrichment re-exports it, so existing imports still work.
TAG_TAXONOMY: List[str] = [
    "military", "politics", "science", "technology", "exploration", "space", "arts", "music",
    "film", "sports", "religion", "royalty", "economics", "law", "disaster", "health", "social",
    "education", "philosophy", "engineering",
]

#: Coarse categories over TAG_TAXONOMY, used as the default level of filtering.
#: Every tag belongs to exactly one category (asserted by a test), and the key
#: order IS the precedence order used to give an event a single category:
#: most-specific subject first, most-general background theme last. An event
#: tagged military+politics is a war event someone excluding war expects to
#: lose; an event tagged law+politics is ordinary politics.
#: WARNING: these keys are persisted verbatim into subscribers'
#: subscriptions.excluded_categories (matched by exact string in
#: core.matching.included_from_excluded). Renaming a key here silently
#: orphans existing subscribers' exclusions unless paired with an alias or a
#: data migration - see SUPABASE_SETUP.md section 11.
TAG_CATEGORIES: Dict[str, List[str]] = {
    "Sport": ["sports"],
    "Disasters": ["disaster"],
    "Exploration & Space": ["exploration", "space"],
    "Arts & Culture": ["arts", "music", "film", "philosophy"],
    "Science & Technology": ["science", "technology", "engineering", "health"],
    "Society & Belief": ["religion", "social", "education"],
    "War & Conflict": ["military"],
    "Politics & Power": ["politics", "law", "royalty", "economics"],
}

#: The category taxonomy, in precedence order. The categories counterpart of
#: TAG_TAXONOMY - what a filter selection is inverted against.
CATEGORY_NAMES: List[str] = list(TAG_CATEGORIES)

#: Integer sequences a subscriber can have their age in days checked against.
#: Unrelated to TAG_TAXONOMY/TAG_CATEGORIES above: these describe a property of
#: the number itself, not of any event, so they are deliberately a separate
#: taxonomy rather than a ninth category.
#: WARNING: these names are persisted verbatim into subscribers'
#: subscriptions.included_sequences and matched by exact string - renaming one
#: silently drops it from every subscriber who chose it. Same hazard as
#: TAG_CATEGORIES above; see SUPABASE_SETUP.md section 13.
SEQUENCE_TAXONOMY: List[str] = [
    "Powers of 2",
    "Powers of 10",
    "Triangle numbers",
    "Fibonacci numbers",
    "Primes",
    "Perfect squares",
    "Cubes",
    "Catalan numbers",
]

__all__ = [
    "CATEGORY_NAMES",
    "DATA_DIR",
    "PROJECT_ROOT",
    "SEQUENCE_TAXONOMY",
    "TAG_CATEGORIES",
    "TAG_TAXONOMY",
]
