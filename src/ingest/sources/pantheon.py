"""Helpers for working with Pantheon 1.0 data."""

from __future__ import annotations

import csv
import json
from typing import Dict, List

TOP_N = 1000


def load_pantheon_data(tsv_file_path: str, skip_occupations: List[str] | None = None) -> List[Dict]:
    """Load Pantheon entries from the TSV export."""
    skip_occupations = skip_occupations or []
    pantheon: List[Dict] = []
    with open(tsv_file_path, "r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            try:
                if row["occupation"] not in skip_occupations:
                    row["HPI"] = float(row["HPI"])
                    pantheon.append(row)
            except ValueError:
                continue
    return pantheon


def load_births_data(json_file_path: str) -> List[Dict]:
    """Load births data previously harvested from MuffinLabs."""
    with open(json_file_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def extract_name(text: str) -> str:
    """Extract the name (everything before the first comma)."""
    return text.split(",", 1)[0].strip()


def get_top_births(pantheon_data: List[Dict], births_data: List[Dict], top_n: int = TOP_N) -> List[Dict]:
    """Match top Pantheon entries with known births."""
    births_by_name = {extract_name(birth["name"]): birth for birth in births_data}

    matched: List[Dict] = []
    for row in sorted(pantheon_data, key=lambda item: item["HPI"], reverse=True):
        name = row["name"]
        if name in births_by_name:
            birth_entry = births_by_name[name]
            matched.append(
                {
                    "name": name,
                    "year": birth_entry["year"],
                    "month": birth_entry["month"],
                    "day": birth_entry["day"],
                    "text": birth_entry["text"],
                    "occupation": row["occupation"],
                    "industry": row["industry"],
                    "domain": row["domain"],
                }
            )
        if len(matched) == top_n:
            print(f"Found {len(matched)} matches, stopping at top {top_n}.")
            print(f"Last matched name: {matched[-1]['name']}")
            break
    return matched


def main():  # pragma: no cover - manual helper
    pantheon_file = "data/legacy_pantheon.tsv"
    births_file = "data/historical_births_cleaned.json"
    output_file = f"data/top_{TOP_N}_births.json"

    pantheon_data = load_pantheon_data(pantheon_file)
    print(f"Loaded {len(pantheon_data)} entries from Pantheon data.")
    births_data = load_births_data(births_file)
    print(f"Loaded {len(births_data)} entries from births data.")

    top_births = get_top_births(pantheon_data, births_data, top_n=TOP_N)

    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(top_births, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":  # pragma: no cover - manual helper
    main()
