# CLAUDE.md — Transit Optimizer

## What This Is

A research-grade solver for transit coverage optimization — starting with the London Tube Challenge (visit every Underground station as fast as possible). Charles holds the record at 13h40m vs the 14h17m official world record. This project formalizes that solve into a rigorous open-source framework that works on any city's GTFS data, with a provable lower bound on solution quality.

**Current phase: Phase 1** — get the algorithm formalized and working on a toy network, then real London topology (simplified, no timetables yet).

---

## Problem Formulation

**Covering Traveling Salesman Problem (Covering TSP) on a time-expanded graph.**

- Every `(station, departure_time)` pair is a node
- Edges: train legs (station_A, t_depart) → (station_B, t_arrive) and waiting arcs (station, t) → (station, t+1)
- Objective: find a path visiting at least one node per required station, minimizing total elapsed time
- NP-hard in general — we use a greedy + local search heuristic and compute an LP relaxation lower bound

**Key difference from standard TSP:** no return-to-start, visit a required subset of nodes (not all), and edge weights are time-dependent (you can only take a train when it departs).

---

## Phases

| Phase | Scope | Deadline | Status |
|---|---|---|---|
| **1** | Toy network + greedy solver + baselines + simplified London topology | Apr 20 | Not started |
| **2** | Real TfL GTFS feed, time-expanded graph (~500k nodes), solver on real data | Apr 20 | Not started |
| **3** | LP relaxation lower bound, optimality gap | After Phase 2 | Not started |
| **4** | Multi-city (NYC, Tokyo, Berlin, Chicago), deck.gl web UI, GPS leaderboard | Ongoing | Not started |

---

## Repo Structure

```
transit-optimizer/
├── src/
│   ├── graph/          # Graph data structures (static + time-expanded)
│   ├── gtfs/           # GTFS parser (Phase 2+)
│   ├── solver/         # Heuristic solver, local search, LP bound
│   └── viz/            # Route visualization (terminal first, web later)
├── data/
│   ├── toy/            # Hardcoded test network (20 stations, 3 lines)
│   └── london/         # London topology + eventually GTFS
├── tests/
├── benchmarks/
├── SPEC.md
└── CLAUDE_CODE_PROMPT.md
```

---

## Stack

- **Language:** Python (Phase 1–2). Consider Rust if time-expanded graph construction is a bottleneck at scale.
- **Graph:** `networkx` for prototyping. Numpy sparse matrices or Rust adjacency lists for Phase 2+ scale.
- **LP:** `scipy.optimize.linprog` or `cvxpy` for LP relaxation (Phase 3)
- **Testing:** `pytest`
- **CLI output:** `rich`
- **Web UI (Phase 4):** React + deck.gl (trips layer for animated route viz)
- **Data:** PostgreSQL for GTFS storage (Phase 2+)

---

## Phase 1 Build Order

Build in this order — don't skip ahead:

1. `src/graph/network.py` — `TransitNetwork` class: `add_station()`, `add_edge()`, `get_neighbors()`, `travel_time()`
2. `data/toy/network.json` — 20-station, 3-line test network (hardcoded, use it to validate fast)
3. `src/solver/greedy.py` — `GreedySolver.solve()` returns ordered station list + arrival times
4. CLI: `python -m transit_optimizer solve --network data/toy/network.json`
5. Baselines: random order, pure greedy (no lookahead), our solver — compare total times
6. Simplified London topology (average headways, not real timetables)

**Do not start:** web UI, GTFS parsing, LP bound, or Phase 2 features until Phase 1 checklist is done.

---

## Algorithmic Constraints

- **Time dependency matters even in Phase 1:** simulate missed trains with average headway wait times. Don't just sum hop counts.
- **Track cumulative time from start**, not per-leg time.
- **Routes must be physically valid:** only traverse edges that exist in the network.
- **Greedy with lookahead:** at each step, look ahead 3 stations (not just nearest unvisited).
- **Local search after greedy:** 2-opt and or-opt on the resulting route.

---

## Required Tests

```
test_toy_network         — solver finds valid route visiting all 20 toy stations
test_no_missing_stations — every required station appears in output
test_time_monotonic      — arrival times strictly increasing
test_greedy_vs_random    — greedy solution is faster than random order
```

---

## Key Concepts (interview-ready)

- **Time-expanded graphs:** why static graphs are insufficient when train schedules exist; memory implications at 500k nodes
- **Covering TSP vs TSP:** dropping return-to-start and required-subset-only changes hardness and approximation behavior
- **LP relaxation:** why solving the LP gives a valid lower bound on the IP; what the integrality gap means practically
- **Approximation guarantees:** why "my solution is good" is meaningless without a bound; what k-approximation means
- **GTFS format:** how stops.txt + stop_times.txt + trips.txt + calendar.txt compose into a queryable timetable

---

## Interview Depth Questions

Be ready to answer all of these fluently:
1. Why is this harder than standard TSP?
2. When do you need a time-expanded graph vs a static one?
3. Your heuristic found a good solution — how do you know it's good?
4. What does the LP relaxation tell you, and why is its bound valid?
5. How does complexity change with real timetable constraints vs simplified topology?
6. What would it take to guarantee optimality, and why didn't you go there?
7. How does your GTFS pipeline handle cancelled services and disruptions?

---

## Working Here

- Read `SPEC.md` and `CLAUDE_CODE_PROMPT.md` at the start of every session
- Stay in Phase 1 until all Phase 1 checkboxes are done
- Toy network first — always validate logic there before touching London data
- Prefer a working CLI over a clean architecture
- Mark `SPEC.md` checklist items with `[x]` when done
