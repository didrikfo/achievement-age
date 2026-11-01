"""Resumable chunk batches for the work done by Claude Code subagents.

Four ingest steps hand records to subagents a chunk at a time: new-event
phrasing (ingest.llm_utils), the Supabase enrichment/phrasing backfill
(ingest.backfill_event_enrichment), subject extraction
(ingest.subject_extraction) and Nobel phrasing (ingest.nobel_llm_utils). The
orchestrating session can stop at any moment (a usage or session limit, a
closed laptop), so every chunk directory is a small state machine on disk
that a later session can pick up from:

    chunk_NNNN.json          waiting for a subagent
    chunk_NNNN_result.json   subagent finished; waiting to be merged
    merged/chunk_NNNN*.json  merged - never merged again
    batch.json               manifest: kind, mode, prompt version, model

Rules every step follows:

- prepare_* resumes rather than re-chunks while unmerged chunks exist, so a
  restart never strands finished subagent output or pairs a stale result file
  with a new chunk. fresh=True discards the unmerged batch and starts over.
- A merge needs a complete, parseable result file. A missing or truncated one
  (the subagent was cut off) merges nothing and leaves the chunk waiting, so it
  gets dispatched again. No fallback text is written in its place.
- Records a subagent left out of an otherwise valid result are not merged
  either. They stay pending at their source and come back on the next prepare.
- A merge whose manifest names a different prompt version than the code now
  has is refused, so output written under an old prompt is never stamped with
  the new version.
- Merges are idempotent (appends dedupe on the record key), and the chunk only
  moves to merged/ after its output is written, so a crash between the two
  just re-merges harmlessly.

Inspect or advance a batch from the command line:

    python -m ingest.agent_batches status [kind]
    python -m ingest.agent_batches merge  [kind]   # merge every chunk with a valid result
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.io import save_to_json

#: Agent-tool model alias for every subagent these batches dispatch: phrasing
#: (reword_prompt.md, nobel_reword_prompt.md) and subject extraction
#: (subject_prompt.md). "opus" resolves to Claude Opus 5.5.
SUBAGENT_MODEL = "opus"

MANIFEST_NAME = "batch.json"
MERGED_DIR_NAME = "merged"
_CHUNK_GLOB = "chunk_[0-9][0-9][0-9][0-9].json"


class BatchConflict(RuntimeError):
    """An unmerged batch in a chunk directory doesn't match what's being asked of it."""


def result_path_for(chunk_path: Path) -> Path:
    chunk_path = Path(chunk_path)
    return chunk_path.with_name(f"{chunk_path.stem}_result.json")


def unmerged_chunks(chunk_dir: Path) -> List[Path]:
    """Chunk files still in chunk_dir (not yet moved to merged/), in order."""
    chunk_dir = Path(chunk_dir)
    if not chunk_dir.is_dir():
        return []
    return sorted(chunk_dir.glob(_CHUNK_GLOB))


def read_manifest(chunk_dir: Path) -> Optional[Dict]:
    try:
        with open(Path(chunk_dir) / MANIFEST_NAME, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def load_results(result_path: Path) -> Optional[List[Dict]]:
    """A subagent's result list, or None if it's missing, truncated or the wrong shape.

    None means "nothing usable came back" - the caller merges nothing and leaves
    the chunk waiting for another dispatch. Non-object entries inside an
    otherwise valid list are dropped, and a list with no objects left counts
    as nothing usable.
    """
    try:
        with open(result_path, "r", encoding="utf-8-sig") as handle:
            results = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(results, list):
        return None
    results = [result for result in results if isinstance(result, dict)]
    # Chunks are never empty, so a result with no records in it is a failure too.
    return results or None


def _clear(chunk_dir: Path) -> None:
    for path in chunk_dir.glob("chunk_*.json"):
        path.unlink()
    (chunk_dir / MANIFEST_NAME).unlink(missing_ok=True)
    shutil.rmtree(chunk_dir / MERGED_DIR_NAME, ignore_errors=True)


def write_chunks(
    chunk_dir: Path,
    records: List[Dict],
    chunk_size: int,
    manifest: Dict,
    fresh: bool = False,
) -> List[Path]:
    """Split records into numbered chunk files, or resume the unmerged batch already there.

    Resuming returns the existing unmerged chunk paths untouched (records and
    chunk_size are ignored) as long as its manifest matches `manifest`; a
    mismatch - another mode, kind or prompt version - raises BatchConflict.
    Starting a batch (no unmerged chunks, or fresh=True) clears every old
    chunk, result, merged/ entry and manifest first.
    """
    chunk_dir = Path(chunk_dir)
    pending = unmerged_chunks(chunk_dir)
    if pending and not fresh:
        existing = read_manifest(chunk_dir) or {}
        mismatched = {key: (existing.get(key), value) for key, value in manifest.items() if existing.get(key) != value}
        if mismatched:
            raise BatchConflict(
                f"{chunk_dir} holds {len(pending)} unmerged chunk(s) from a different batch "
                f"(existing vs requested: {mismatched}). Finish it with "
                f"`python -m ingest.agent_batches merge`, or pass fresh=True to discard it."
            )
        return pending

    chunk_dir.mkdir(parents=True, exist_ok=True)
    _clear(chunk_dir)
    paths: List[Path] = []
    for index, start in enumerate(range(0, len(records), chunk_size)):
        path = chunk_dir / f"chunk_{index:04d}.json"
        save_to_json(path, records[start : start + chunk_size])
        paths.append(path)
    if paths:
        save_to_json(chunk_dir / MANIFEST_NAME, {**manifest, "model": SUBAGENT_MODEL, "chunks": len(paths)})
    return paths


def check_manifest(chunk_path: Path, kind: str, prompt_version: int) -> None:
    """Refuse to merge output written under another prompt version.

    A chunk with no manifest beside it (a hand-built chunk, or a test) passes.
    """
    manifest = read_manifest(Path(chunk_path).parent)
    if manifest is None:
        return
    if manifest.get("kind") == kind and manifest.get("prompt_version") != prompt_version:
        raise BatchConflict(
            f"{chunk_path} was prepared under {kind} prompt version {manifest.get('prompt_version')}, "
            f"but the code is now at {prompt_version}. Re-prepare with fresh=True."
        )


def mark_merged(chunk_path: Path, result_path: Optional[Path] = None) -> None:
    """Move a merged chunk (and its result file) into merged/ so it's never merged again."""
    chunk_path = Path(chunk_path)
    result_path = Path(result_path) if result_path else result_path_for(chunk_path)
    merged_dir = chunk_path.parent / MERGED_DIR_NAME
    merged_dir.mkdir(exist_ok=True)
    for path in (chunk_path, result_path):
        if path.exists():
            path.replace(merged_dir / path.name)


# --- status and merge-all, per batch kind -------------------------------------


@dataclass
class BatchKind:
    name: str
    chunk_dir: Callable[[], Path]
    prompt: str
    merge: Callable[[Path, Path, Dict], object]
    notes: List[str] = field(default_factory=list)


def _kinds() -> Dict[str, BatchKind]:
    # Imported lazily: those modules import this one.
    from core.config import DATA_DIR
    from ingest import backfill_event_enrichment, llm_utils, nobel_llm_utils, subject_extraction

    return {
        "reword": BatchKind(
            "reword",
            lambda: llm_utils.CHUNK_DIR,
            "ingest.enrichment.build_prompt()",
            lambda chunk, result, _m: llm_utils.merge_reworded_chunk(
                chunk, result, births_path=DATA_DIR / "historical_births_cleaned.json"
            ),
        ),
        "enrichment": BatchKind(
            "enrichment",
            lambda: backfill_event_enrichment.CHUNK_DIR,
            "ingest.enrichment.build_prompt()",
            lambda chunk, result, manifest: backfill_event_enrichment.merge_chunk(
                chunk, result, mode=manifest.get("mode", "tags")
            ),
            ["writes to Supabase - needs a write session"],
        ),
        "subject": BatchKind(
            "subject",
            lambda: subject_extraction.SUBJECT_CHUNK_DIR,
            "ingest.subject_extraction.build_prompt()",
            lambda chunk, result, _m: subject_extraction.merge_subject_chunk(chunk, result),
        ),
        "nobel": BatchKind(
            "nobel",
            lambda: nobel_llm_utils.CHUNK_DIR,
            "ingest.nobel_llm_utils.build_nobel_prompt()",
            lambda chunk, result, _m: nobel_llm_utils.merge_nobel_chunk(chunk, result),
        ),
    }


def batch_status(chunk_dir: Path) -> Dict:
    """Where a batch stands: chunks awaiting a subagent, ready to merge, and merged."""
    chunk_dir = Path(chunk_dir)
    awaiting: List[str] = []
    ready: List[str] = []
    for chunk in unmerged_chunks(chunk_dir):
        (ready if load_results(result_path_for(chunk)) is not None else awaiting).append(chunk.name)
    merged_dir = chunk_dir / MERGED_DIR_NAME
    merged = len(list(merged_dir.glob(_CHUNK_GLOB))) if merged_dir.is_dir() else 0
    return {"manifest": read_manifest(chunk_dir), "awaiting_agent": awaiting, "ready_to_merge": ready, "merged": merged}


def merge_ready(kind: str) -> List[str]:
    """Merge every chunk of `kind` that has a valid result file. Returns the chunk names merged."""
    spec = _kinds()[kind]
    chunk_dir = spec.chunk_dir()
    manifest = read_manifest(chunk_dir) or {}
    merged: List[str] = []
    for name in batch_status(chunk_dir)["ready_to_merge"]:
        chunk = chunk_dir / name
        spec.merge(chunk, result_path_for(chunk), manifest)
        merged.append(name)
    return merged


def _print_status(spec: BatchKind) -> None:
    chunk_dir = spec.chunk_dir()
    status = batch_status(chunk_dir)
    manifest = status["manifest"] or {}
    if not (status["awaiting_agent"] or status["ready_to_merge"] or status["merged"]):
        print(f"[{spec.name}] no batch in {chunk_dir}")
        return
    detail = ", ".join(f"{key}={manifest[key]}" for key in ("mode", "prompt_version", "model") if key in manifest)
    print(f"[{spec.name}] {chunk_dir} ({detail})")
    print(f"  merged: {status['merged']}  ready to merge: {len(status['ready_to_merge'])}  awaiting agent: {len(status['awaiting_agent'])}")
    for note in spec.notes:
        print(f"  note: {note}")
    if status["ready_to_merge"]:
        print(f"  next: python -m ingest.agent_batches merge {spec.name}")
    for name in status["awaiting_agent"]:
        chunk = chunk_dir / name
        print(f"  dispatch ({SUBAGENT_MODEL}): {chunk} -> {result_path_for(chunk).name}  prompt: {spec.prompt}")


def main(argv: List[str]) -> int:
    if not argv or argv[0] not in ("status", "merge"):
        print(__doc__)
        return 2
    command, names = argv[0], argv[1:]
    kinds = _kinds()
    unknown = [name for name in names if name not in kinds]
    if unknown:
        print(f"unknown kind(s) {unknown}; expected one of {sorted(kinds)}")
        return 2
    for name in names or sorted(kinds):
        if command == "merge":
            merged = merge_ready(name)
            print(f"[{name}] merged {len(merged)} chunk(s){': ' + ', '.join(merged) if merged else ''}")
        _print_status(kinds[name])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
