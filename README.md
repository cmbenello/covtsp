# Open Transit Optimizer

A research-grade Covering TSP solver for transit coverage optimization. Finds near-optimal routes through any city's transit network using real GTFS timetable data, with LP relaxation bounds proving solution quality.

Built from the London Tube Challenge — visiting all 272 Underground stations as fast as possible. The current Guinness World Record is 17h46m (Robin Otter & Thomas Sheat, August 2024). Our automated solver achieves 18h05m.

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

1. **Greedy with Lookahead** — Nearest-unvisited in time-space with beam search (k=3). Produces initial feasible solution.
2. **Local Search** — 2-opt, Or-opt, 3-opt moves on station visit order. Re-simulates full timed path after each move (required because the graph is time-dependent).
3. **LP Relaxation** — Flow-based LP on the static graph provides a provable lower bound. The optimality gap proves how close the heuristic is to optimal.

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
