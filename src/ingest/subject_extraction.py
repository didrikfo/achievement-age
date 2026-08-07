"""Stage 2: ask a Claude Haiku subagent which person an event is actually about.

Two-phase like ingest.llm_utils, because a Claude Code subagent runs between
the two calls:

    python -c "from ingest.subject_extraction import prepare_subject_chunks; prepare_subject_chunks()"
    # ... dispatch a Haiku subagent per chunk file, using build_prompt() ...
    python -c "from ingest.subject_extraction import merge_subject_chunk; merge_subject_chunk('data/tmp/subject_chunks/chunk_0000.json', 'data/tmp/subject_chunks/chunk_0000_result.json')"

The subagent is never asked for a birth date - only for a name it can read in
the text. Everything it returns is validated in Python before use.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from core.config import DATA_DIR
from core.io import load_json, save_to_json
from ingest.match_events import SUBJECT_PENDING_PATH

PROMPT_TEMPLATE_PATH = Path(__file__).parent / "subject_prompt.md"
SUBJECT_CHUNK_DIR = DATA_DIR / "tmp" / "subject_chunks"
CHUNK_SIZE = 100


def build_prompt() -> str:
    """Read subject_prompt.md. No placeholders to fill - the instructions are static."""
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def prepare_subject_chunks(
    pending_path: Path = SUBJECT_PENDING_PATH,
    chunk_size: int = CHUNK_SIZE,
    chunk_dir: Path = SUBJECT_CHUNK_DIR,
) -> List[Path]:
    """Split the Stage 2 queue into numbered chunk files for a subagent to process."""
    pending = load_json(pending_path)

    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(pending), chunk_size)):
        path = chunk_dir / f"chunk_{index:04d}.json"
        save_to_json(path, pending[start : start + chunk_size])
        paths.append(path)
    return paths
