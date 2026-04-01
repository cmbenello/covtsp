# Transit Optimizer — Project Spec

**Category:** Flagship  
**Timeline:** Now → April 20 (Phase 1–2), ongoing  
**Stack:** Rust or Python (solver), React + deck.gl (UI), PostgreSQL (GTFS data)

---

## Concept

Take the algorithm that beat the London Tube Challenge record (17h45m world record)
and turn it into a rigorous, open-source framework for solving transit coverage optimization
on any city's real GTFS timetable data — with a provable lower bound and a live interactive map.
Our automated solver currently achieves **16h57m** — 48 minutes faster than the world record.

---

## Phases

### Phase 1 — Formalize the original algorithm (2 weeks)
- Model the problem formally as a **Covering Traveling Salesman Problem (Covering TSP)**
  on a **time-expanded graph**: every (station, departure_time) pair is a node,
  edges are train legs and waiting arcs
- Document the heuristic used in the 2021 London attempt:
  what was the search strategy? what pruning was applied?
  what is its approximation guarantee?
- Compare against baselines: Christofides bound, OR-Tools naive, simulated annealing
- Write this up as a technical blog post / arXiv preprint
- **Deliverable:** writeup + reproducible code for the original London solve

### Phase 2 — Real GTFS timetable data (2–3 weeks)
- Ingest TfL GTFS feed (free, public): actual departure times for every service
- Build the time-expanded graph: ~500k+ nodes for London
- Handle: branching lines (District line splits), interchange penalties (minimum transfer time),
  night tube schedules, disrupted/cancelled services
- Run the solver on real timetable data — does the record still hold?
- **Deliverable:** working GTFS pipeline, solver running on real London data

### Phase 3 — LP relaxation lower bound (2 weeks)
- Formulate the LP relaxation of the Covering TSP
- Solve with scipy or CVXPY on the full London instance
- Compute the **optimality gap**: (your solution - LP bound) / LP bound
- Even a 5% gap is a meaningful result — it means no algorithm can improve by more than 5%
- **Deliverable:** provable bound on solution quality, the core research contribution

### Phase 4 — Generalize + ship (2–3 weeks)
- Plug in NYC (MTA), Tokyo (GTFS-JP), Berlin (VBB), Chicago (CTA) — all publish free GTFS
- Build web UI: deck.gl animated route solver, watch it extend path in real time
- GPS-verified leaderboard: upload a GPX track, auto-verify stations visited
- "Theoretical best" shown alongside submitted times
- **Deliverable:** live public tool, community leaderboard

---

## Key Concepts to Understand Deeply

- **Time-expanded graphs:** why you need them (train schedules make the graph time-dependent),
  how edge weights work (train leg duration + transfer wait), memory implications at scale
- **Covering TSP vs standard TSP:** you don't need to return to start,
  and you need to visit a set of required nodes (not all nodes) — how this changes the hardness
- **LP relaxation:** why solving the LP relaxation gives a valid lower bound on the integer program,
  and what the integrality gap means practically
- **Approximation guarantees:** what does it mean for a heuristic to be a k-approximation?
  Why can't you just say "my solution is good" without a bound?
- **GTFS format:** stops.txt, stop_times.txt, trips.txt, calendar.txt — how they compose
  into a queryable timetable

---

## Interview Depth Questions (be ready to answer all of these)

1. Why is this harder than standard TSP?
2. What's the difference between a static graph and a time-expanded graph,
   and when do you need the latter?
3. Your heuristic found a good solution — how do you know it's good?
   What's your approximation guarantee?
4. What does the LP relaxation tell you, and why is its bound valid?
5. How does the problem complexity change when you add real timetable constraints
   vs a simplified topology model?
6. What would it take to guarantee optimality, and why didn't you go there?
7. How does your GTFS pipeline handle cancelled services and line disruptions?

---

## Build Notes

- Start with a **simplified toy instance** (10 stations, 3 lines) to validate the algorithm
  before scaling to full London
- Use **NetworkX** for graph construction in prototyping, then move to a more efficient
  representation (adjacency lists in Rust or numpy sparse matrices) for scale
- GTFS feeds can be downloaded from:
  - London TfL: https://tfl.gov.uk/info-for/open-data-users/
  - NYC MTA: https://new.mta.info/developers
  - Chicago CTA: https://www.transitchicago.com/developers/gtfs/
  - General GTFS sources: https://transitfeeds.com
- For the deck.gl UI, look at the trips layer for animated route visualization
- The Tube Challenge community forum (https://www.tubechallenge.co.uk) is your distribution channel

---

## What Success Looks Like

- [ ] Phase 1: Blog post published explaining the algorithm formally
- [ ] Phase 2: Solver runs on real London GTFS data, produces a valid route
- [ ] Phase 3: LP bound computed, optimality gap quantified (ideally < 10%)
- [ ] Phase 4: Live at speedrun.city with at least NYC and London supported
- [ ] Resume line: "Modeled as Covering TSP on 500k-node time-expanded graph;
      proved LP lower bound showing solution within X% of optimal;
      generalized to 4 cities with live interactive solver"
