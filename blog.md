# Solving the London Tube Challenge with Algorithms: 18h05m and Counting

## The Hook

I hold an unofficial record for the London Tube Challenge — visiting all 272 Underground stations in 13 hours and 40 minutes, compared to the official Guinness World Record of 17h46m (set by Robin Otter & Thomas Sheat in August 2024). That attempt was planned by hand: spreadsheets, local knowledge, and gut instinct.

This project asks: **can an algorithm do it?** And more importantly — can we *prove* how good the solution is?

The answer so far: an automated solver completes the challenge in **18h05m** — just 19 minutes behind the world record — using nothing but public timetable data and a greedy heuristic. And we're not done.

---

## The Problem: Covering TSP on a Time-Expanded Graph

The Tube Challenge isn't a standard Traveling Salesman Problem. It's harder in three specific ways:

1. **No return to start.** You just need to visit every station and stop — you don't need to come back.
2. **Time-dependent edges.** You can only board a train when it departs. Miss the 20:40 shuttle and you're waiting until morning. The graph is dynamic.
3. **Covering variant.** Each station exists at multiple time points throughout the day. You need to visit *at least one* node per required station, not every node.

This is a **Covering TSP on a time-expanded graph (TEG)**. NP-hard in general.

### What is a Time-Expanded Graph?

In a static graph, the edge from station A to station B has a fixed weight (say, 3 minutes). But in reality, if you arrive at A at 8:07 and the next train departs at 8:12, you wait 5 minutes — the *real* cost is 8 minutes, not 3.

A time-expanded graph makes this explicit. Every node is a **(station, departure_time)** pair. Edges are:
- **Transit legs:** (Station A, 8:12) -> (Station B, 8:15) — a train departure
- **Waiting arcs:** (Station A, 8:07) -> (Station A, 8:12) — waiting for the next train
- **Walking/running transfers:** (Station A, 8:07) -> (Station B, 8:20) — running between nearby stations

For London on a weekday, the TEG has **160,069 nodes** and **1,262,131 edges**. The solver's job is to find a path through this graph that touches at least one node per required station, minimizing total elapsed time.

### Why Not a Static Graph?

A static graph treats every train as equally available — it can't represent the fact that Kensington Olympia has 9 trains per day while Oxford Circus has one every 90 seconds. Time-dependency is the core challenge of this problem. Without a TEG, the solver can't reason about catching specific trains, service windows closing, or the cascading effect of missing a connection.

---

## The Algorithm Stack

The solver uses a three-layer approach:

### Layer 1: GTFS Timetable Ingestion
Input: TfL's public GTFS feed (stops.txt, stop_times.txt, trips.txt, routes.txt, calendar.txt). The parser filters by date and route type (Underground only), groups stations (merging duplicates like Paddington H&C into main Paddington), and builds the full TEG.

### Layer 2: Greedy Heuristic with Lookahead
The core solver is a **k-nearest-neighbor** with beam search lookahead:
- At each step, evaluate the top k=5 nearest unvisited stations
- Use single-source Dijkstra with early termination to compute exact travel times on the TEG (~0.02s per step)
- Score candidates by `travel_time + urgency_weight * time_until_last_service(station)` (more on urgency below)
- Simulate the full route, tracking cumulative time from start

### Layer 3: LP Relaxation Lower Bound
A min-cost flow formulation on the static graph:
- Variables: flow x_ij >= 0 on each directed edge
- Objective: minimize sum(weight_ij * x_ij)
- Constraints: flow conservation, coverage (each required station receives flow >= 1), start node outflow
- Solver: PuLP + CBC
- Gives a provable lower bound on the optimal solution — currently ~7h43m
- Optimality gap: (heuristic - bound) / bound

The LP bound is loose because it allows fractional flow (the "solver" can split itself) and doesn't account for time-dependency. Tightening it is Phase 3 work.

---

## The Kensington Olympia Breakthrough: 18h45m to 18h05m

The single biggest improvement — 40 minutes — came from one station.

**Kensington Olympia (K.O.)** is served by a shuttle from Earl's Court that runs only **9 times per day**: 5 morning shuttles (05:49-07:20) and 4 evening shuttles (20:01-20:40). That's a 13-hour dead zone with zero service.

### The Old Approach (18h45m)
The baseline k=1 nearest-neighbor started at Ealing Broadway at 06:33 and worked greedily through the network. By the time it reached Earl's Court, it was **20:41 — one minute after the last K.O. shuttle**. The solver had to take a 60+ minute detour to reach K.O. via a roundabout route.

### The New Approach (18h05m)
**Force the solver to visit K.O. first thing in the morning** (prefix strategy):
1. Start at Finchley Road at 05:41
2. Take the Metropolitan line to Earl's Court
3. Catch the 05:49 morning shuttle to K.O.
4. Continue greedy nearest-neighbor from there

This eliminates the late-night detour entirely. Net savings: ~40 minutes.

### Why This Matters Algorithmically
K.O. has only **9 TEG nodes** for the entire day. Compare that to Oxford Circus with hundreds. The greedy heuristic has no way to "see" that it needs to reach Earl's Court by 20:40 — it's optimizing locally, not planning 14 hours ahead. The prefix strategy is a form of **hard station scheduling**: identify bottleneck stations with constrained service windows and handle them explicitly before letting the greedy solver run.

---

## Urgency Scoring and Phase Transitions

K.O. isn't the only time-constrained station. **Mill Hill East** is at the end of a single-track Northern line branch with limited evening service. The standard greedy solver often defers it until too late, finishing at 271/272.

The fix: an **urgency score** that biases the nearest-neighbor toward stations whose service windows are closing:

```
score = travel_time + urgency_weight * time_until_last_service(station)
```

Stations with early last-service times get priority. The urgency weight has a **sharp phase transition**:
- From Euston Square with K.O. prefix: `urgency < 0.16` -> 271/272 (misses Mill Hill East), `urgency >= 0.16` -> 272/272 (but +21 min)
- From Finchley Road with K.O. prefix: urgency weight doesn't matter — the route naturally covers Mill Hill East regardless

This phase transition is interesting: the solver's behavior flips discontinuously at a specific parameter threshold. It's not a gradual tradeoff — it's binary.

---

## Hard Station Detection

The solver auto-detects hard stations by analyzing TEG structure:

- **Sparsity:** stations with < 20 TEG nodes/day are flagged (K.O. has 9)
- **Stub depth:** stations at the end of branch lines with no through service
- **Service window clustering:** stations where all service is concentrated in narrow time bands

A hardness score combines these factors: `sparsity * depth_penalty * window_penalty`. The three hardest London stations are:
1. **Kensington Olympia** — 9 trains/day, 13h service gap
2. **Heathrow Terminal 4** — separate Piccadilly loop, requires specific routing via Hatton Cross
3. **Mill Hill East** — single-track branch, limited evening service

The solver handles these via prefix strategies (K.O.), station-triggered injections (T4 via Hatton Cross at max 10 min extra cost), and urgency scoring (Mill Hill East).

---

## Results

### London Underground (272 stations)

| Solver Variant | Coverage | Time | Start Station | Notes |
|---|---|---|---|---|
| **K.O. prefix + urgency NN** | **272/272** | **18h05m** | Finchley Road 05:41 | Best result |
| K.O. prefix + urgency NN | 272/272 | 18h06m | Euston Square 05:44 | Second best |
| K.O. prefix + NN (no urgency) | 271/272 | 17h45m | Euston Square 05:44 | Misses Mill Hill East |
| k=1 NN (old baseline) | 272/272 | 18h45m | Ealing Broadway 06:33 | Previous best |
| Urgency NN + T4 inject | 271/272 | 17h47m | Ealing Broadway 06:33 | Misses K.O. |
| Urgency NN (urg=0.6) | 271/272 | 17h27m | Ealing Broadway 06:33 | Misses T4 |

**Key insight:** there's a sharp tradeoff between coverage and speed. Dropping just one station (271/272) saves 20-40 minutes, because the last few stations are always the hardest — they're on branch ends with infrequent service.

### Multi-City Results

| City | Stations | Best Time | TEG Size |
|---|---|---|---|
| **London** | 272 | 18h05m | 160K nodes, 1.26M edges |
| **NYC Subway** | 475 | 21h35m | Larger network, more branches |
| **Berlin U-Bahn** | 170 | 7h53m | Smaller, more connected |

The same algorithm framework works across cities — just drop in a GTFS feed and a YAML config.

### Comparison to World Record
- **Guinness World Record:** 17h46m (Robin Otter & Thomas Sheat, August 2024)
- **Solver best (272/272):** 18h05m
- **Gap:** ~19 minutes

The gap is likely due to:
1. Greedy heuristic, not globally optimal ordering
2. Human competitors use running transfers between stations (not yet in solver)
3. Expert knowledge of line frequencies and real-time adaptability
4. Timetable data may differ from actual day of attempt

---

## What Didn't Work

This is as important as what did work:

- **k=3 lookahead:** Consistently misses 5 branch-end stations. The multi-step penalty causes the solver to defer branches it should visit early.
- **Line-aware scoring:** Biasing toward same-line stations (30% bonus for staying on the same route) never achieves 272/272 — the same-line bonus causes different misses.
- **TEG local search (2-opt/or-opt):** Random moves on a 272-station route find no improvement. The time-dependent landscape is too rugged — swapping two stations changes arrival times at every subsequent station, which changes which trains are available, which changes everything downstream.
- **Branch-aware solver:** Junction-triggered branch completion is too aggressive (114-269/272).
- **Forced visit windows:** Broad time windows break other coverage; narrow windows don't trigger when needed.
- **Evening K.O. injection:** The route arrives at Earl's Court at 20:41. K.O.'s last shuttle departs at 20:40. One minute too late.
- **500-trial randomized search:** Random perturbations from top starting stations couldn't find any 272/272 solutions with K.O. forced visit.

**The fundamental lesson:** local search doesn't work well on time-expanded graphs. Small reorderings cascade through the entire route because arrival times shift, different trains become available, and the whole downstream path changes. The landscape is rugged and local optima are deep.

---

## Running Transfer Filtering: $375 to $7

Beyond riding trains, Tube Challenge competitors can **run between stations**. When you're at the end of a branch, it's sometimes faster to run to a nearby station on a different line than to backtrack through the network.

Computing accurate running times requires the Google Routes API. With 272 stations, all-pairs queries would cost ~$375 (37,128 directed pairs). We need to figure out which pairs are actually worth computing.

### Filter 1: Distance Cutoff (37,128 -> 21,774 pairs)
Only consider pairs within 20km Haversine distance. Beyond that, running would take 2+ hours at race pace and will almost never beat the train.

### Filter 2: Branch Terminus Stations (21,774 -> 10,921 pairs)
Running between stations is only useful **at the ends of lines**. If you're mid-line, just take the train. We identify terminus candidates by reconstructing trip sequences from GTFS data: the last 3 stops of each branch end. Keep pairs where at least one station is a terminus and they're on different lines.

### Filter 3: Train Dominance Prune (10,921 -> 723 pairs)
The aggressive filter. For each remaining pair (A, B):

> Even if you could run in a perfectly straight line at maximum sprint speed — ignoring streets, crossings, and navigation — would running still be slower than the train's raw travel time (ignoring headway waits)?

```
crow_flies_time = haversine(A, B) / sprint_speed
train_time = shortest_path_weight(train_graph, A, B)  # no waits, just travel

if crow_flies_time >= train_time:
    eliminate  # running can never win
```

Both sides are **optimistic lower bounds**: crow-flies is shorter than any real run, and raw travel time ignores headway waits. If the train's lower bound beats running's lower bound, the dominance holds for all real values. The prune is provably correct — it cannot eliminate a useful running shortcut.

### Result

| Stage | Pairs | Est. API Cost |
|---|---|---|
| All pairs | 37,128 | ~$375 |
| After distance filter | 21,774 | ~$218 |
| After terminus filter | 10,921 | ~$109 |
| After dominance prune | **723** | **~$7** |

**98% cost reduction** while keeping every pair that could conceivably improve the route.

---

## Technical Details

### TEG Statistics (London, weekday)
- **Nodes:** 160,069
- **Edges:** 1,262,131
- **Running mode:** 18.0 km/h base speed, max 20km transfers
- **Date tested:** 2026-03-24 (Tuesday)

### Solver Parameters (best variant)
1. Prefix: visit K.O. first (morning shuttle via Earl's Court)
2. k=5 nearest-neighbor with urgency scoring (urgency_weight=0.5)
3. Station-triggered injections: T4 via Hatton Cross (max 600s = 10 min), K.O. backup window
4. Early-termination Dijkstra (~0.02s per step on TEG)

### Code Stats
- ~5,400 lines of Python
- 4 city configs (London, NYC, Berlin, toy network)
- CLI interface: `python cli.py solve --config configs/london.yaml --date 2024-03-15`

---

## What's Next

### LP Relaxation (Phase 3)
The current LP bound (~7h43m) is computed on the static graph and is quite loose. Computing it on the full TEG would give a much tighter bound, but the instance size (160K nodes) makes this challenging. The goal: prove the solution is within X% of optimal, where X is ideally < 10%.

### Running Transfers
The 723 filtered station pairs haven't been integrated into the solver yet. Adding running edges to the TEG could shave significant time off branch-end detours — this is likely where most of the 19-minute gap to the world record lives.

### Better Search
Currently using greedy nearest-neighbor with manually designed prefix/injection strategies. Simulated annealing over solver parameters (start station, urgency weight, injection thresholds) could find better configurations automatically.

### More Cities
The framework is city-agnostic. Tokyo, Chicago, Singapore, and more are planned. Each city has different topology characteristics that stress different parts of the algorithm.

---

## Why This Project

This started as a personal challenge — I wanted to visit every Tube station as fast as possible, and I did it in 17h45m. But the planning process was entirely manual: spreadsheets, intuition, local knowledge of which connections work.

Formalizing that into an algorithm forced me to think rigorously about what makes the problem hard. Time-dependency. Sparse service windows. The cascading effect of small ordering changes. The gap between "I found a good solution" and "I can prove it's good."

The Covering TSP on a time-expanded graph is a clean, well-defined problem with real-world data, multiple cities to generalize across, and a human world record to benchmark against. It sits at the intersection of combinatorial optimization, transit data engineering, and systems thinking — which is exactly where I like to work.

---

## Links

- **Live demo:** [covtsp website](https://cmbenello.github.io/covtsp/) — interactive route visualization for London, NYC, and Berlin
- **Source code:** [github.com/cmbenello/covtsp](https://github.com/cmbenello/covtsp)
- **Guinness World Record:** 17h46m by Robin Otter & Thomas Sheat (August 2024)
- **My record:** 17h45m
