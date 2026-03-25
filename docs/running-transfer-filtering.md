# Running Transfer Filtering — Design Notes

**Context:** London Tube Challenge solver. The challenge is to visit all Underground stations
as fast as possible. Beyond riding trains, competitors can run between stations — particularly
between the far ends of different lines — to avoid long backtracking journeys.

This document explains how we decide *which* station pairs are worth computing accurate
walking/running times for (via Google Routes API), and how we prune the rest cheaply.

---

## The Problem

The London Underground has 272+ stations. All-pairs running times would require
~37,000 directed queries to Google Routes API — roughly $375 at current pricing, and
most of those pairs would never realistically be run during a challenge.

We need a principled way to get down to only the pairs that could actually improve a route.

---

## Filter 1 — Distance Cutoff

The first filter is simple: only consider pairs within a Haversine radius.

We use **20km** as the upper bound. This is generous — previous world record attempts
have involved running legs up to ~10km, but 20km gives headroom for future city configs
and unusual topologies. Beyond 20km, a run would take ~2+ hours at race pace and will
almost never beat the train.

```
21,774 pairs remain after distance filter (from 37,128 total)
```

This alone isn't enough — we've just gone from "everything" to "everything that's vaguely
nearby", and the cost is still ~$218.

---

## Filter 2 — Branch Terminus Stations

The key insight: running between stations is only useful at **the ends of lines**.

If you're at a mid-line station and want to reach another station, you can just take the
train. Running only becomes competitive when you're at a **terminus** — the end of a branch —
and the nearest unvisited station is reachable faster on foot than by backtracking through
the network.

We identify terminus stations by reconstructing trip sequences from the GTFS data:

1. Group segments by `trip_id`, sort by departure time
2. For each unique trip sequence, take the first N and last N stations
3. A station is a "terminus candidate" if it appears at the beginning or end of any line

We use **N = 3** (last 3 stops of each branch end). This captures:
- The actual terminus (stop 1)
- The penultimate station (stop 2) — sometimes you exit one stop early and run to save time
- The ante-penultimate (stop 3) — rare but can matter on very long branches (e.g. Chesham/Amersham on Met)

We then keep only pairs where **at least one** station is a terminus candidate, and they are
**on different lines**. The idea: you run *from* a branch end to wherever on another line is
fastest to reach — that destination could be mid-line, not necessarily another terminus.
Requiring both to be termini would miss the case where a mid-line station on line Y sits
geographically close to the end of line X and is faster to run to than to backtrack through
the network.

```
224 terminus candidate stations identified
10,921 pairs remain after terminus filter
```

This cuts the problem roughly in half, but we can do much better.

---

## Filter 3 — Train-vs-Crow-Flies Dominance Prune

This is the aggressive prune. For each remaining candidate pair (A, B), we ask:

> Even if you could run in a perfectly straight line at maximum sprint speed —
> ignoring street layouts, crossings, and navigation overhead —
> would running still be slower than the train's raw travel time (ignoring headway waits)?

If yes, running can never win. We eliminate the pair.

Formally:

```
crow_flies_run_time = haversine_distance(A, B) / sprint_speed

train_time = shortest_path_weight(train_graph, A, B)  # min travel time, no waits

if crow_flies_run_time >= train_time:
    eliminate  # running can't possibly be faster
```

Both sides are **optimistic lower bounds**:
- `crow_flies_run_time` is strictly less than any real running time (straight line, no routing overhead)
- `train_time` is strictly less than any real train time (no headway wait, just segment travel time)

So if the train's lower bound ≤ running's lower bound, the dominance holds for all real values:
actual running time will always be ≥ crow-flies time, and actual train time will always be ≥ pure
travel time. The inequality is preserved.

**Sprint speed**: we use 1.2× the configured base running speed (12 km/h for a 10 km/h base),
representing a short sprint. This makes the prune conservative — we only eliminate pairs where
running is clearly hopeless, not marginal cases.

```
723 pairs remain after prune (10,198 eliminated)
```

The prune eliminates ~93% of remaining pairs. Most terminus-to-terminus pairs are still faster
by train even ignoring all the overhead — the network is well-connected enough that long running
legs almost never make sense except for very specific geographic shortcuts.

---

## Result

| Stage | Pairs | Est. API cost |
|---|---|---|
| All pairs | 37,128 | ~$375 |
| After distance filter (≤20km) | 21,774 | ~$218 |
| After branch terminus filter | 10,921 | ~$109 |
| After train-vs-crow-flies prune | **723** | **~$7** |

We go from $375 → **$7** while keeping every pair that could conceivably benefit the solver.

---

## Why the Prune Is Correct

The prune can only eliminate pairs where running is genuinely dominated. It cannot miss a
useful running shortcut.

Proof sketch: Suppose pair (A, B) is eliminated. Then:

```
haversine(A, B) / sprint_speed >= shortest_train_travel_time(A, B)
```

For running to be useful in practice, the actual running time must beat the actual train time:

```
actual_run_time > haversine(A, B) / sprint_speed   [routing overhead + sustained pace]
actual_train_time > shortest_train_travel_time(A, B)  [headway wait + travel time]
```

So `actual_run_time > actual_train_time` — the pair is indeed useless. QED.

The only edge case is pairs with **no train path** (disconnected components). For those,
we conservatively keep the pair regardless of distance — running may be the only option.
(In practice this doesn't occur on the London Underground, which is fully connected.)

---

## Usage

```bash
# Dry run to see what would be computed
python scripts/compute_all_walking.py \
    --config configs/london.yaml \
    --max-distance 20000 \
    --branch-terminus 3 \
    --prune-vs-train \
    --dry-run

# Actually compute and cache
python scripts/compute_all_walking.py \
    --config configs/london.yaml \
    --max-distance 20000 \
    --branch-terminus 3 \
    --prune-vs-train
```

Then in `configs/london.yaml`, set `use_google_walking: true` and re-run the solver.

---

## Potential Extensions

- **Buses**: the same filtering logic applies. Add `route_type_filter: [1, 3]` and the
  terminus detection will pick up bus route ends too. Key question is whether bus schedules
  are worth including in the graph at all (they substantially increase complexity).

- **News/disruption integration**: on disrupted days, some train paths are severed or
  heavily delayed. The train-vs-crow-flies prune becomes more aggressive (more pairs
  eliminated) on normal days, and less aggressive on disrupted days — the solver would
  automatically consider more running options when the network is degraded.

- **Tuning N**: N=3 was chosen based on the London topology. For cities with shorter branches
  (e.g. Singapore MRT) you might want N=2; for cities with very long rural branches
  (e.g. Tokyo), N=5 might be warranted.
