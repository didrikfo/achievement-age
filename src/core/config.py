"""Configuration helpers for locating project resources."""

from __future__ import annotations

from pathlib import Path
from typing import List

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

__all__ = ["DATA_DIR", "PROJECT_ROOT", "TAG_TAXONOMY"]
