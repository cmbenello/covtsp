#!/usr/bin/env python3
"""Generate best-day JSON files for all configured cities.

Usage:
    python scripts/update_best_day.py [--days 14]

Outputs:
    web/best-day-london.json
    web/best-day-nyc.json
    web/best-day-berlin.json
"""

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.best_day import compute_best_days

CONFIGS = [
    ("configs/london.yaml", "london"),
    ("configs/nyc.yaml", "nyc"),
    ("configs/berlin.yaml", "berlin"),
]


def main():
    parser = argparse.ArgumentParser(description="Generate best-day recommendations for all cities")
    parser.add_argument("--days", type=int, default=14, help="Days ahead to analyze")
    args = parser.parse_args()

    output_dir = Path("web")
    output_dir.mkdir(exist_ok=True)

    for config_path, city_slug in CONFIGS:
        config_file = Path(config_path)
        if not config_file.exists():
            print(f"  Skipping {city_slug}: config not found at {config_path}")
            continue

        cfg = load_config(config_file)
        if not cfg.data_dir.exists():
            print(f"  Skipping {city_slug}: GTFS data not found at {cfg.data_dir}")
            continue

        print(f"  Analyzing {cfg.city_name}...")
        result = compute_best_days(cfg, city_slug, days_ahead=args.days)

        output_path = output_dir / f"best-day-{city_slug}.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        best = result["days"][0] if result["days"] else None
        if best:
            print(f"    Best: {best['date']} ({best['day_of_week']}) — score {best['overall_score']}")
        print(f"    Written to {output_path}")

    print("Done.")


if __name__ == "__main__":
    main()
