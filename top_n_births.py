import csv
import json

from utils import load_json

# Set number of top entries to retrieve
TOP_N = 1000

# Load Pantheon 1.0 data
def load_pantheon_data(tsv_file_path: str, skip_occupations: list[str] = []) -> list[dict]: 
    pantheon = []
    with open(tsv_file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            try:
                # Skip rows where 'occupation' is in skip_occupation
                if row['occupation'] not in skip_occupations:
                    row['HPI'] = float(row['HPI'])
                    pantheon.append(row)
            except ValueError:
                continue
    return pantheon

# Load historical births JSON
def load_births_data(json_file_path: str) -> list[dict]:
    with open(json_file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# Extract name from birth entry (everything before the first comma)
def extract_name(text: str) -> str:
    return text.split(',', 1)[0].strip()

# Match and collect top HPI entries from Pantheon that are also in births
def get_top_births(pantheon_data: list[dict], births_data: list[dict], top_n: int = TOP_N) -> list[dict]:
    births_by_name = {
        extract_name(birth["name"]): birth
        for birth in births_data
    }

    matched = []
    for row in sorted(pantheon_data, key=lambda x: x["HPI"], reverse=True):
        name = row["name"]
        if name in births_by_name:
            birth_entry = births_by_name[name]
            matched.append({
                "name": name,
                "year": birth_entry["year"],
                "month": birth_entry["month"],
                "day": birth_entry["day"],
                "text": birth_entry["text"],
                "occupation": row["occupation"],
                "industry": row["industry"],
                "domain": row["domain"]
            })
        if len(matched) == top_n:
            print(f"Found {len(matched)} matches, stopping at top {top_n}.")
            print(f"Last matched name: {matched[-1]['name']}")
            break
    return matched

def main():
    pantheon_file = 'data/legacy_pantheon.tsv'
    births_file = 'data/historical_births_cleaned.json'
    output_file = f'data/top_{TOP_N}_births.json'

    pantheon_data = load_pantheon_data(pantheon_file)
    print(f"Loaded {len(pantheon_data)} entries from Pantheon data.")
    births_data = load_births_data(births_file)
    print(f"Loaded {len(births_data)} entries from births data.")

    top_births = get_top_births(pantheon_data, births_data, top_n=TOP_N)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(top_births, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
