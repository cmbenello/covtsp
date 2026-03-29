# Claude Code Prompt — Transit Optimizer

Copy this entire prompt when starting a new Claude Code session for this project.

---

## Project Brief

I am building the **Open Transit Optimizer** — a research-grade solver for the
London Tube Challenge and similar transit coverage problems. The Tube Challenge
is a real speedrun: visit every station on the London Underground as fast as possible.
I hold a record of 17h45m (vs the 17h46m Guinness world record).

This project has four phases. We are starting with **Phase 1: algorithm formalization
and a working solver on simplified data**, then Phase 2 adds real GTFS timetable data.

## Problem Formulation

The problem is a variant of the **Covering Traveling Salesman Problem (Covering TSP)**
on a **time-expanded graph**:

- **Nodes:** (station_id, time) pairs — every station at every relevant departure time
- **Edges:** 
  - Train legs: (station_A, depart_time) → (station_B, arrive_time), weight = travel time
  - Waiting arcs: (station, t) → (station, t+1), weight = 1 minute
- **Objective:** find a path through the time-expanded graph that visits at least one
  node for each required station, minimizing total time elapsed
- **Constraint:** you must physically travel (can't teleport between non-adjacent stations)

This is NP-hard in general. We use a heuristic approach (greedy nearest-neighbor with
lookahead + local search improvements) and compute an LP relaxation lower bound to
bound the optimality gap.

## Phase 1 Goals (build this first)

1. **Data model:** represent a transit network as a graph
   - Station nodes with line membership
   - Edge weights = travel time in minutes
   - For Phase 1: use a simplified static graph (no timetables yet, use average frequencies)

2. **Solver — greedy + local search:**
   - Start: greedy nearest-unvisited-station with lookahead (look ahead 3 stations)
   - Improvement: 2-opt and or-opt local search on the resulting route
   - Output: ordered list of stations with arrival times, total time

3. **Baseline comparisons:**
   - Random order (lower bound sanity check)
   - Pure greedy (no lookahead)
   - Our solver
   - For each: report total time, % stations visited, compute simple lower bound
     (sum of minimum spanning arborescence on required stations)

4. **Toy dataset:** hardcode a 20-station, 3-line test network so we can iterate fast
   before adding real data

5. **London dataset (simplified):** I will provide the London Underground topology.
   Use average headways (Central line: 2 min peak, District: 4 min, etc.)
   rather than real timetables for Phase 1.

## Phase 2 Goals (after Phase 1 works)

- Replace simplified graph with real **GTFS** data from TfL
- GTFS files: stops.txt, stop_times.txt, trips.txt, routes.txt, calendar.txt
- Build the proper time-expanded graph from GTFS
- Re-run solver on real timetable data

## Repo Structure

```
transit-optimizer/
├── src/
│   ├── graph/          # Graph data structures (static + time-expanded)
│   ├── gtfs/           # GTFS parser (Phase 2)
│   ├── solver/         # Heuristic solver, local search, LP bound
│   └── viz/            # Route visualization (terminal + eventually web)
├── data/
│   ├── toy/            # Hardcoded test network
│   └── london/         # London Underground topology + eventually GTFS
├── tests/
├── benchmarks/
└── SPEC.md
```

## Language / Stack

Use **Python** for Phase 1 (faster iteration). We will consider Rust for Phase 2
if the time-expanded graph construction becomes a bottleneck.

Dependencies:
- `networkx` for graph operations
- `scipy` for LP relaxation (scipy.optimize.linprog)
- `numpy` for matrix operations
- `pytest` for tests
- `rich` for terminal output / progress

## What to Build First

Start with:
1. `src/graph/network.py` — TransitNetwork class: add_station(), add_edge(), 
   get_neighbors(), travel_time()
2. `src/solver/greedy.py` — GreedySolver: solve() returns ordered station list + times
3. `data/toy/network.json` — a 20-station test network I can use to validate
4. A simple CLI: `python -m transit_optimizer solve --network data/toy/network.json`
   that prints the route and total time

Do NOT start with the web UI, GTFS parsing, or LP bound yet.
Get the core solver working and tested first.

## Key Algorithmic Constraints

- The solver must handle the **time dependency**: if you miss a train, you wait
  for the next one. Even in the simplified Phase 1 model, simulate this with
  average headway wait times.
- Track **cumulative time from start**, not just hop count
- The output route must be **physically valid**: you can only travel on edges
  that exist in the network

## Tests to Write

- `test_toy_network`: solver finds a valid route visiting all 20 toy stations
- `test_no_missing_stations`: every required station appears in output route
- `test_time_monotonic`: arrival times are strictly increasing
- `test_greedy_vs_random`: greedy solution is faster than random order (should always hold)

## Notes on the Real Problem

When we get to Phase 2 with real GTFS data:
- London has 272 stations across 11 lines
- The time-expanded graph will have ~500k nodes for a full day of service
- We'll need to be smarter about graph construction (don't build the whole day,
  build dynamically as the solver explores)
- The LP relaxation will be on the simplified static graph, not the full time-expanded graph

Let's start. Build Phase 1.
