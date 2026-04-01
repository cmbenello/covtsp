# Beating the London Tube Challenge World Record by 48 Minutes — With Code

The London Tube Challenge is simple to explain: visit all 272 stations on the Underground as fast as possible. The Guinness World Record stands at 17 hours 45 minutes. I built an automated solver that found a route completing it in **16 hours 57 minutes** — 48 minutes faster than any human has achieved.

## The Problem

This isn't a standard Traveling Salesman Problem. It's a **Covering TSP on a time-expanded graph**:

- You don't need to visit every node — just one node per required station
- The graph is time-dependent: trains only depart at scheduled times
- Missing a train means waiting for the next one (sometimes 30+ minutes on branch lines)
- Running between nearby stations can be faster than waiting for a connection
- Some stations have as few as 9 trains per day (Kensington Olympia)

The search space is vast: 160,553 time-expanded nodes, 1,262,857 edges, 272 stations to cover, any of which could be the starting point, at any time between 05:00 and 09:00.

## The Solver

### Phase 1: Hard Station Detection

Not all stations are created equal. The solver auto-detects the hardest stations from the timetable data:

| Station | Problem | Strategy |
|---|---|---|
| Kensington Olympia | 9 trains/day | Visit first (prefix) |
| Mill Hill East | Stub branch, early last service | Grab when passing junction |
| Chesham | 17-min round trip from mainline | Grab when passing junction |
| Heathrow Terminal 4 | Separate loop | Grab when passing Hatton Cross |

Each hard station is paired with its nearest junction. When the solver passes a junction, it grabs the paired stub station as a side trip.

### Phase 2: Deterministic Sweep

The solver runs a greedy k=5 nearest-neighbor with urgency scoring from every station at every 5-minute start time (05:00–09:00). Stations approaching their last service get priority. This produces a landscape of ~13,000 candidate routes.

### Phase 3: Randomized Search

The best deterministic starts seed a randomized search: epsilon-greedy (with probability 0.05, pick a random top-3 station instead of the nearest). Thousands of trials per seed. The randomization discovers route orderings that the deterministic solver can't find — like the difference between 17h30m and 16h57m from the same start station.

## Results: The Improvement Curve

Starting from a naive greedy solver and progressively adding algorithmic improvements:

| # | Time | Start | Method | vs Record |
|---|---|---|---|---|
| 1 | 19h42m | Battersea @ 05:00 | Greedy pairings | +117 min |
| 3 | 18h43m | Arsenal @ 05:35 | Greedy pairings | +58 min |
| 6 | 18h03m | Chesham @ 05:00 | Greedy pairings | +18 min |
| 7 | **17h30m** | Upminster @ 06:14 | Greedy pairings | **-15 min** |
| 8 | 17h12m | Upminster @ 06:15 | Randomized | -33 min |
| 15 | 17h01m | Upminster @ 05:57 | Randomized | -44 min |
| 16 | 16h58m | Upminster @ 05:59 | Randomized | -47 min |
| **17** | **16h57m** | **Upminster @ 06:02** | **Randomized** | **-48 min** |

The first time we beat the record was improvement #7 — just the deterministic solver finding the right start station (Upminster instead of Chesham). The randomized search then shaved off another 33 minutes.

## What Made the Difference

**1. Start station discovery.** The previous best (18h03m) started from Chesham in the far northwest. Upminster, on the eastern end of the District line, turned out to be over an hour better. You can't know this without trying all 272 stations.

**2. Start time granularity.** Switching from 30-minute to 5-minute to 1-minute start time resolution found that 06:02 beats 06:15 by over 30 minutes. Train connections cascade — one missed connection can cost 20 minutes downstream.

**3. Hard station scheduling.** Kensington Olympia has 9 trains per day. Without explicit prefix-visit logic, the solver either misses it entirely or wastes time backtracking for it late in the day.

**4. Pairings in the randomized search.** The initial randomized solver didn't use hard station logic — so most of its 80,000 trials handled K.O., Terminal 4, and Mill Hill East suboptimally. Adding pairings to the randomized solver was the single largest code change.

## Caveats

This is a computational result, not a human attempt:
- Uses running transfers at 18 km/h between stations (up to 20km)
- Assumes perfect timetable adherence (no delays)
- Assumes no crowds or platform congestion
- The route visits all 272 stations but the visit order may not be physically achievable at full running speed for 17 hours

The solver proves that a sub-17-hour route *exists* in the timetable. Whether a human can execute it is a different question.

## Technical Stack

- **Python** with NetworkX for graph operations
- **Time-expanded graph**: 160K nodes from real TfL GTFS data
- **Google Maps Walking API** for accurate inter-station distances (cached)
- **KD-tree** for efficient nearby-station detection
- **Randomized search**: ~1M trials across start times and epsilon values

All code is open source. The full route (every station, arrival time, and line) is saved as JSON.
