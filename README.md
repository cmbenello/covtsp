# Open Transit Optimizer

A research-grade Covering TSP solver for transit coverage optimization. Finds near-optimal routes through any city's transit network using real GTFS timetable data, with LP relaxation bounds proving solution quality.

Built from the London Tube Challenge — visiting all 272 Underground stations as fast as possible. The current Guinness World Record is 17h45m. **Our automated solver achieves 16h57m — 48 minutes faster than the world record.**

### Results: Solver Improvement Over Time

| # | Time | Start Station | Start Time | Method | vs Record |
|---|---|---|---|---|---|
| 1 | 19h42m | Battersea Power Station | 05:00 | Deterministic pairings | +117 min |
| 3 | 18h43m | Arsenal | 05:35 | Deterministic pairings | +58 min |
| 6 | 18h03m | Chesham | 05:00 | Deterministic pairings | +18 min |
| 7 | 17h30m | Upminster | 06:14 | Deterministic pairings | **-15 min** |
| 8 | 17h12m | Upminster | 06:15 | Randomized (eps=0.05) | **-33 min** |
| 15 | 17h01m | Upminster | 05:57 | Randomized (eps=0.05) | **-44 min** |
| 16 | 16h58m | Upminster | 05:59 | Randomized (eps=0.05) | **-47 min** |
| **17** | **16h57m** | **Upminster** | **06:02** | **Randomized (eps=0.05)** | **-48 min** |

The solver uses running-mode transfers (18 km/h base) between stations, matching the real-world Tube Challenge where participants run between connections.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  GTFS Feed  │────▸│  Parser +    │────▸│  Time-Expanded  │
│  (any city) │     │  Calendar    │     │  Graph (500K+)  │
└─────────────┘     └──────────────┘     └────────┬────────┘
                                                   │
                    ┌──────────────┐     ┌─────────▾────────┐
                    │  LP Relaxation│◂───│  Greedy Solver   │
                    │  Lower Bound │     │  + Local Search  │
                    └──────┬───────┘     └─────────┬────────┘
                           │                       │
                           ▾                       ▾
                    ┌──────────────────────────────────┐
                    │  Optimality Gap = (H - LP) / LP  │
                    └──────────────────────────────────┘
```

## Quickstart

```bash
pip install -r requirements.txt

# Run on toy network (included)
python cli.py solve --config configs/toy.yaml --date 2024-03-15

# Download real GTFS data for London
python cli.py download --config configs/london.yaml

# Solve on real London data
python cli.py solve --config configs/london.yaml --date 2024-03-15 --output results/london.json

# View config info
python cli.py info --config configs/london.yaml

# Validate GTFS data
python cli.py validate --config configs/london.yaml
```

## Algorithm Stack

1. **Hard Station Detection** — Auto-detects difficult stations (low service frequency, stub branches) from TEG sparsity. Pairs each with its nearest junction for efficient side-trip scheduling.
2. **Greedy with Pairings** — k=5 nearest-neighbor with urgency scoring. Prefix visits for ultra-sparse stations (e.g., Kensington Olympia, 9 trains/day), junction grabs for branch stubs.
3. **Randomized Search** — Epsilon-greedy exploration (eps=0.05–0.2) over thousands of trials from top deterministic starts. Pairings-aware — hard stations handled correctly in every trial.
4. **LP Relaxation** — Flow-based LP on the static graph provides a provable lower bound.

## Adding a City

1. Create `configs/yourcity.yaml`:
```yaml
city_name: Your City Metro
gtfs_url: https://example.com/gtfs.zip
gtfs_path: yourcity
station_count: 100
route_type_filter: [1]  # 1 = Metro
walking_speed_kmh: 5.0
max_walk_distance_m: 500
```

2. Download and solve:
```bash
python cli.py download --config configs/yourcity.yaml
python cli.py solve --config configs/yourcity.yaml --date 2024-06-15
```

## Project Structure

```
src/
├── graph/
│   ├── network.py          # Static weighted graph (min travel times)
│   └── time_expanded.py    # Time-expanded graph: (station, time) nodes
├── gtfs/
│   ├── parser.py           # GTFS file parsing + station grouping
│   ├── calendar.py         # Date-aware service filtering
│   └── download.py         # GTFS feed downloader
├── solver/
│   ├── greedy.py           # Greedy nearest-unvisited with lookahead
│   ├── local_search.py     # 2-opt, Or-opt, 3-opt
│   └── lp_bound.py         # LP relaxation (static + time-expanded)
├── backtest.py             # Date-aware backtesting engine
└── config.py               # YAML config loader
```

## Tests

```bash
python -m pytest tests/ -v
```

44 tests covering GTFS parsing, graph construction, solver correctness, and LP bound validity.

## Key Concepts

- **Time-Expanded Graph**: Nodes are (station, time) pairs. Edges: transit arcs, waiting arcs, walking transfers. Required because train schedules make the graph time-dependent.
- **Covering TSP**: Visit a required subset of nodes (not all), no return to start. NP-hard.
- **LP Relaxation**: Relax integer constraints to get a lower bound. The gap between the heuristic solution and this bound proves solution quality.
- **GTFS**: General Transit Feed Specification — the universal format for transit timetable data.
