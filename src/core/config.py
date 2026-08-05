"""Configuration helpers for locating project resources."""

from __future__ import annotations

from pathlib import Path

# config.py lives in src/core so we step three levels up to reach the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

__all__ = ["DATA_DIR", "PROJECT_ROOT"]
