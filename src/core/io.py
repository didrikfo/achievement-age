"""Lightweight JSON helpers used by the ingest pipeline."""

from __future__ import annotations

import json
from typing import Dict, List


def load_json(filename: str, sort_by_field: str | None = None) -> List[Dict]:
    """Load a JSON document and optionally sort it by sort_by_field.

    Uses utf-8-sig rather than plain utf-8 so a leading byte-order-mark
    (some tools/editors write one on Windows - observed from a subagent's
    Write tool when merging Stage 2 chunk results) doesn't make json.load
    raise. utf-8-sig strips a BOM if present and behaves identically to
    utf-8 otherwise, so this is a strict widening, not a behavior change.
    """
    with open(filename, "r", encoding="utf-8-sig") as handle:
        json_data = json.load(handle)
        if sort_by_field:
            json_data.sort(key=lambda item: len(item.get(sort_by_field, "")), reverse=True)
        return json_data


def save_to_json(filepath: str, data: List[Dict]) -> None:
    """Persist a list of dictionaries to filepath using UTF-8 encoding."""
    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def parse_llm_output(llm_output_string: str) -> List[Dict]:
    """Parse and validate the JSON array of objects produced by llm_utils helpers.

    Raises ValueError (rather than crashing on a bare assertion) so callers can
    catch a malformed response and retry or fall back.
    """
    cleaned = llm_output_string.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM output was not valid JSON: {exc}") from exc

    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError("LLM output must be a JSON array of objects.")

    return parsed
