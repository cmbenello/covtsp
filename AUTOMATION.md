# E2E Automation: Adding a New Subway System

This doc explains every step the pipeline takes to go from "I want to solve Tokyo Metro" to a published results page. An LLM (or script) can follow these steps to fully automate a new city end-to-end.

---

## Overview

```
1. Create city config YAML
2. Download GTFS data
3. Compute walking transfers
4. Run the solver (deterministic + randomized search)
5. Compute LP lower bound
6. Export results JSON to web/
7. Generate route animation video
8. Update best-day forecasts
9. Add city to index.html
10. Deploy
```

---

## Step-by-Step

### 1. Create City Config (`configs/<city>.yaml`)

Every city needs a YAML config. Copy an existing one and modify:

```yaml
city_name: Tokyo Metro                    # Display name
gtfs_url: https://example.com/gtfs.zip    # URL to GTFS zip (or empty if manual)
gtfs_path: tokyo                          # Subdir under data/
station_count: 285                        # Expected required stations
route_type_filter: [1]                    # GTFS route_type: 1=metro, 400=U-Bahn, etc.
walking_speed_kmh: 5.0
max_walk_distance_m: 500                  # Max walking transfer distance
start_station: null                       # null = try all stations
time_window:
  start: "05:00"
  end: "01:00"
excluded_stations: []                     # Station IDs to skip (closed, etc.)
```

**Key decisions:**
- `route_type_filter`: Which GTFS `route_type` values count as "required" stations. See [GTFS spec](https://gtfs.org/schedule/reference/#routestxt). Common: `1` (subway), `400` (U-Bahn extended), `401` (metro extended).
- `max_walk_distance_m`: Larger = more walking transfer options = potentially faster routes but slower graph build.
- `excluded_stations`: For permanently closed stations or ones that shouldn't count.

**How to find the right route_type:**
```bash
# After downloading GTFS, inspect routes.txt
head -5 data/tokyo/routes.txt
# Look at route_type column — find which value(s) correspond to the metro system
cut -d',' -f5 data/tokyo/routes.txt | sort | uniq -c | sort -rn
```

### 2. Download GTFS Data

```python
from src.gtfs.download import download_gtfs
from src.config import load_config

cfg = load_config("configs/tokyo.yaml")
download_gtfs(cfg.gtfs_url, cfg.data_dir)
```

Or manually: download the GTFS zip, extract to `data/tokyo/`. Must contain at minimum:
- `stops.txt` — station locations
- `stop_times.txt` — timetable
- `trips.txt` — trip definitions
- `routes.txt` — route metadata
- `calendar.txt` — service schedules

Optional but helpful: `calendar_dates.txt` (service exceptions), `transfers.txt` (official transfers).

**Validation:**
```bash
# Check required files exist
ls data/tokyo/{stops,stop_times,trips,routes,calendar}.txt

# Check station count matches expectations
grep -c "," data/tokyo/stops.txt
```

### 3. Compute Walking Transfers

Walking transfers connect stations that are physically close but not connected by rail. This is critical for route quality.

```bash
python scripts/compute_all_walking.py --config configs/tokyo.yaml
```

This uses a KDTree spatial index to find all station pairs within `max_walk_distance_m`, then computes walking time at `walking_speed_kmh`. Results are cached in `data/tokyo/walking_transfers.json`.

**What it does internally:**
1. Loads all stops from `stops.txt`
2. Filters to required station parents only
3. Builds KDTree on (lat, lon) coordinates
4. For each station, finds all neighbors within radius
5. Computes haversine distance and walking time
6. Saves as JSON: `{from_id: {to_id: walk_seconds, ...}, ...}`

### 4. Run the Solver

The solver has three phases that progressively refine the solution.

#### 4a. Quick single-run test (verify setup works)

```python
from datetime import date
from src.backtest import backtest
from src.config import load_config

cfg = load_config("configs/tokyo.yaml")
results = backtest(cfg, date(2026, 4, 15), output_path="results/tokyo_test.json")
```

This runs the basic pipeline: GTFS parse → TEG build → greedy solve → local search → LP bound. Takes ~2-10 min depending on city size.

#### 4b. Full search (find the best route)

Create `scripts/search_tokyo.py` modeled on `scripts/search_best.py`. The search has three phases:

**Phase 1 — Deterministic sweep:** Try every station as a start, every 5 min from 05:00-09:00. Uses greedy solver with hard station pairings. ~15,000 runs for a 200-station city.

**Phase 2 — Randomized search:** Take the top 15 starts from Phase 1. For each, run 5000 trials with epsilon-greedy randomization at 6 epsilon values (0.05 to 0.4). This explores ~450,000 routes.

**Phase 3 — Fine-tune:** Take the single best start and sweep ±15 min in 1-min steps with more randomized trials around it.

**What the solver does at each trial:**
1. Start at (station, time) node in the time-expanded graph
2. At each step, look at reachable unvisited stations
3. Score candidates by: travel time + urgency (how hard to reach later) + randomness (epsilon-greedy)
4. Hard station pairings force detours to dead-end branches at optimal times
5. Record the route if it covers all required stations

**Runtime:** Phase 1 ~30 min, Phase 2 ~2-4 hours, Phase 3 ~1 hour. Total ~3-5 hours on a modern CPU.

**Key parameters to tune per city:**
- Time range to sweep (depends on when metro service starts)
- `urgency_weight` in the greedy scorer (0.3-0.7, higher = more aggressive about hard stations)
- `epsilon` values (higher = more random exploration)

### 5. Compute LP Lower Bound

The LP relaxation gives a provable lower bound — proof that no solution can be faster than X.

Already included in `backtest()`, but for a standalone run:

```python
from src.solver.lp_bound import compute_lp_bound, compute_optimality_gap

# Static graph LP (fast, looser bound)
lp_result = compute_lp_bound(static_network, required_station_ids, start_station)
print(f"LP bound: {lp_result['lp_bound_seconds']}s")
print(f"Optimality gap: {compute_optimality_gap(best_time, lp_result['lp_bound_seconds']):.1f}%")
```

The optimality gap = `(heuristic - LP_bound) / LP_bound * 100`. Lower is better. Under 100% is good.

### 6. Export Results JSON to `web/`

The solver output JSON (from `backtest()` or `search_*.py`) needs to be placed in `web/` for the website.

```bash
# Copy best result to web directory
cp results/tokyo_best.json web/tokyo.json
```

The JSON must contain these fields for the website to work:
```json
{
  "city": "Tokyo Metro",
  "date": "2026-04-15",
  "total_time_seconds": 43200,
  "total_time_formatted": "12h00m",
  "stations_visited": 285,
  "stations_required": 285,
  "optimality_gap_pct": 75.2,
  "lp_lower_bound_seconds": 24660,
  "stations": {
    "station_id": {"name": "Shibuya", "lat": 35.658, "lon": 139.701}
  },
  "route": [
    {"station_id": "...", "station_name": "...", "lat": 35.658, "lon": 139.701, "arrival": 18000, "departure": 18060}
  ]
}
```

### 7. Generate Route Animation Video

The ASCII background uses per-city route animation videos.

```bash
python scripts/gen_route_videos.py
```

**To add a new city**, edit `scripts/gen_route_videos.py`:
```python
CITIES = {
    'london': WEB / 'sample.json',
    'nyc':    WEB / 'nyc.json',
    'berlin': WEB / 'berlin.json',
    'tokyo':  WEB / 'tokyo.json',  # ADD THIS
}
```

Also add a label:
```python
CITY_LABELS = {
    ...
    'tokyo': 'TOKYO',
}
```

This generates `web/route-tokyo.mp4` — a 16-second animation of the route drawing through station dots.

**Dependencies:** matplotlib, scipy, ffmpeg (must be installed: `brew install ffmpeg`).

### 8. Update Best-Day Forecasts

```bash
python scripts/update_best_day.py
```

To add the new city, edit `scripts/update_best_day.py`:
```python
CONFIGS = [
    ("configs/london.yaml", "london"),
    ("configs/nyc.yaml", "nyc"),
    ("configs/berlin.yaml", "berlin"),
    ("configs/tokyo.yaml", "tokyo"),  # ADD THIS
]
```

This generates `web/best-day-tokyo.json` with per-day scores based on service levels, weather, and disruptions.

### 9. Update the Website (`web/index.html`)

#### Add to ASCII background playlist (`web/ascii-bg.js`):
```javascript
var playlist = [
    { src: 'tube-london.mp4', playbackRate: 0.4, dataFile: 'sample.json' },
    { src: 'tube-nyc.mp4',    playbackRate: 0.4, dataFile: 'nyc.json' },
    { src: 'tube-berlin.mp4', playbackRate: 0.4, dataFile: 'berlin.json' },
    { src: 'route-tokyo.mp4', playbackRate: 0.4, dataFile: 'tokyo.json' },  // ADD
];
```

The ASCII background will automatically render Tokyo station names as the character texture when the Tokyo video plays.

#### Add city card to `web/index.html`:
```html
<a href="results.html?city=tokyo" class="city-card active">
    <h3>Tokyo Metro</h3>
    <span class="city-stat">285 stations</span>
    <span class="city-status solved">12h00m</span>
    <span class="card-arrow">&rarr;</span>
</a>
```

#### Add best-day tab:
```html
<button class="bd-tab" data-city="tokyo">Tokyo</button>
```

#### Update hero stats:
Change "3 cities" to "4 cities" in the hero section.

#### Add to `web/best-day.js` data loading:
The best-day JS loads `best-day-{city}.json` automatically based on the tab `data-city` attribute. No code change needed if the JSON file follows naming convention.

### 10. Deploy

```bash
# Test locally
cd web && python -m http.server 8000
# Open http://localhost:8000

# Deploy (assuming GitHub Pages or similar)
git add web/ configs/tokyo.yaml
git commit -m "Add Tokyo Metro: Xh XXm solution"
git push
```

---

## Full Automation Script Template

Here's a complete script that does steps 2-8 automatically:

```python
#!/usr/bin/env python3
"""E2E solver for a new city. Usage: python scripts/solve_city.py configs/tokyo.yaml"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.gtfs.download import download_gtfs
from src.backtest import backtest

# --- Config ---
config_path = sys.argv[1]
cfg = load_config(config_path)
city_slug = cfg.gtfs_path  # e.g. "tokyo"
target_date = date.today() + timedelta(days=7)  # next week

# --- Step 2: Download GTFS ---
print(f"Downloading GTFS for {cfg.city_name}...")
download_gtfs(cfg.gtfs_url, cfg.data_dir)

# --- Step 3: Walking transfers (done automatically in parser) ---

# --- Step 4+5: Solve + LP bound ---
print(f"Running solver for {cfg.city_name} on {target_date}...")
results = backtest(
    cfg, target_date,
    output_path=f"web/{city_slug}.json",
    lookahead=3,
    local_search_iterations=500,
)

print(f"Result: {results['total_time_formatted']}")
print(f"Stations: {results['stations_visited']}/{results['stations_required']}")
if results.get('optimality_gap_pct'):
    print(f"Optimality gap: {results['optimality_gap_pct']:.1f}%")

# --- Step 7: Route video ---
print("Generating route video...")
import subprocess
subprocess.run([sys.executable, "scripts/gen_route_videos.py"], check=True)

# --- Step 8: Best day ---
print("Updating best-day forecasts...")
subprocess.run([sys.executable, "scripts/update_best_day.py"], check=True)

print(f"\nDone! Results at web/{city_slug}.json")
print(f"Now update web/index.html and web/ascii-bg.js to add {cfg.city_name}.")
```

---

## LLM Automation Notes

For an LLM to fully automate this:

1. **Input:** City name + GTFS URL (or transit agency name to look up)
2. **LLM creates:** `configs/<city>.yaml` with correct `route_type_filter` (inspect `routes.txt` after download)
3. **LLM runs:** The automation script above
4. **LLM edits:** `web/index.html`, `web/ascii-bg.js`, `scripts/gen_route_videos.py`, `scripts/update_best_day.py` to add the new city
5. **LLM verifies:** JSON output has all required fields, station count matches, route is valid
6. **LLM commits and pushes**

**Common failure modes:**
- Wrong `route_type_filter` — solution visits wrong stations or too few. Fix: inspect `routes.txt` manually.
- GTFS download URL expired — many agencies rotate URLs. Check agency website.
- Walking transfers too aggressive — `max_walk_distance_m` > 1000 makes graph huge. Keep at 500-1000.
- Low station coverage — some stations may have no service on the target date. Try a different date.
- Solver timeout — very large cities (500+ stations) may need the randomized search to run longer.

**Quality checklist:**
- [ ] All required stations visited (`stations_visited == stations_required`)
- [ ] Optimality gap computed and reasonable (< 200%)
- [ ] Route times are monotonically increasing
- [ ] JSON has valid lat/lon for all stations
- [ ] Route animation video generated
- [ ] City card shows on index.html
- [ ] ASCII background includes city's station names
