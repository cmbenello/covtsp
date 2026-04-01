# Transit Optimizer — Results (2026-03-31)

## Station Count

**272 required stations** on the London Underground network (date: 2026-03-24).

After merging 2 duplicate pairs:
- Paddington: `940GZZLUPAH` (H&C) merged into `940GZZLUPAC` (main)
- Shepherd's Bush Central: `9400ZZLUSBC2` merged into `940GZZLUSBC`

Edgware Road (2 stations) and Hammersmith (2 stations) remain separate — they are genuinely distinct stations on different lines.

## Current Best

**272/272 stations in 16h57m (61,020s) — 48 minutes faster than the 17h45m world record.**

Start: Upminster Underground Station @ 06:02 (2026-03-24, Tuesday)

## Full Improvement History

| # | Time | Start Station | Start Time | Method | vs Record |
|---|---|---|---|---|---|
| 1 | 19h42m | Battersea Power Station | 05:00 | Deterministic pairings | +117 min |
| 2 | 19h12m | St John's Wood | 05:50 | Deterministic pairings | +87 min |
| 3 | 18h43m | Arsenal | 05:35 | Deterministic pairings | +58 min |
| 4 | 18h28m | Bromley-by-Bow | 05:00 | Deterministic pairings | +43 min |
| 5 | 18h16m | Bromley-by-Bow | 05:30 | Deterministic pairings | +31 min |
| 6 | 18h03m | Chesham | 05:00 | Deterministic pairings | +18 min |
| 7 | 17h30m | Upminster | 06:14 | Deterministic pairings | **-15 min** |
| 8 | 17h12m | Upminster | 06:15 | Randomized (eps=0.05) | **-33 min** |
| 14 | 17h22m | Upminster | 05:55 | Randomized (eps=0.05) | -23 min |
| 15 | 17h01m | Upminster | 05:57 | Randomized (eps=0.05) | -44 min |
| 16 | 16h58m | Upminster | 05:59 | Randomized (eps=0.05) | -47 min |
| **17** | **16h57m** | **Upminster** | **06:02** | **Randomized (eps=0.05)** | **-48 min** |

**Improvement: 19h42m → 16h57m (−2h45m total)** across 17 progressive improvements.

## Previous Best Results (historical)

| Solver | Coverage | Time | Start Station | Start Time | Notes |
|--------|----------|------|--------------|------------|-------|
| Prefix K.O. + urgency NN | 272/272 | 18h05m | Finchley Road | 05:41 | Old best before hard station pairings |
| k=1 NN (old baseline) | 272/272 | 18h45m | Ealing Broadway | 06:33 | Original baseline |

## Key Findings

### 1. The K.O. Prefix Breakthrough
The biggest improvement came from visiting K.O. **first thing in the morning** instead of catching it as a late-night detour:
- Old approach: k=1 NN from Ealing Broadway visits EC at 20:41 → catches K.O.'s last shuttle (20:40) with a 60+ min detour → 18h45m
- New approach: **prefix K.O.** — force the solver to visit K.O. first (morning shuttles 05:49-07:20), then continue with urgency NN → 18h05m
- Net savings: ~40 minutes by eliminating the late-night K.O. detour

K.O. has only **9 TEG nodes** (trains per day): 5 morning shuttles (05:49-07:20) and 4 evening shuttles (20:01-20:40).

### 2. Start Station Matters
With the K.O. prefix strategy, the best starting stations shifted:
- **Finchley Road @ 05:41** → 18h05m (best)
- **Euston Square @ 05:44** → 18h06m
- **Ealing Broadway @ 05:51** → 18h12m
- Starting times must be early enough (05:40-05:50) to catch K.O.'s morning shuttles

Previously, Ealing Broadway @ 06:33 was the only viable start — the prefix strategy unlocks many more 272/272-capable starts.

### 3. Coverage vs Speed Tradeoff
There is a sharp phase transition between full coverage and fast routing:
- **272/272 at 18h05m** — K.O. prefix + urgency NN from Finchley Road
- **271/272 at 17h45m** — same solver without urgency (misses Mill Hill East)
- **271/272 at 17h47m** — no K.O. prefix (misses K.O. entirely)
- **270/272 at 17h23m** — skipping K.O. + Mill Hill East

### 4. Heathrow Terminal 4 Loop
T4 is served by a separate Piccadilly line loop from T2/3. The **Hatton Cross injection** catches T4 at just 3-5 min extra cost — the best-performing injection in our framework.

### 5. Urgency Scoring
The urgency weight has a phase transition:
- From Euston Square with K.O. prefix: `urg < 0.16` → 271/272 (misses Mill Hill East), `urg >= 0.16` → 272/272 (but +21 min)
- From Finchley Road with K.O. prefix: urgency weight doesn't matter (0.0-0.5 all give 18h05m)
- The urgency's main effect is pulling the solver toward stations with early service deadlines (Mill Hill East, K.O.)

### 6. Approaches That Didn't Work
- **k=3 lookahead**: Consistently misses 5 branch-end stations due to 2-step penalty
- **Line-aware scoring**: Never achieves 272/272 — same-line bonus causes different misses
- **Branch-aware solver**: Junction-triggered branch completion too aggressive (114-269/272)
- **Hybrid k=3→k=1 switch**: k=3 defers branch ends, k=1 can't recover them
- **solve_fixed_order with station insertion**: Too fragile — timetable connections break
- **TEG local search (2-opt/or-opt)**: Random moves on 272 stations find no improvement
- **Forced visit windows**: Broad windows break other coverage; narrow windows don't trigger
- **Evening K.O. injection**: Route arrives at EC at 20:41 → K.O.'s last shuttle at 20:40 = 1 min too late
- **Randomized search**: 500 trials from top starts couldn't find 272/272 with K.O. forced visit

## Solver Architecture

### solve_with_injections + prefix (best variant)
```
1. Prefix: visit K.O. first (morning shuttle via Earl's Court)
2. k=5 NN with urgency scoring (urgency_weight=0.5)
3. Station-triggered injections:
   - T4 via Hatton Cross (max 600s = 10 min)
   - K.O. time-windowed injection (backup, if prefix not used)
4. Result: 272/272 at 18h05m from Finchley Road @ 05:41
```

### solve_fast (old baseline)
```
1. k=1 pure nearest-neighbor
2. Early-termination Dijkstra (~0.02s per step)
3. Result: 272/272 at 18h45m from Ealing Broadway @ 06:33
```

## TEG Statistics
- Nodes: 160,069
- Edges: 1,262,131
- Date: 2026-03-24 (Tuesday)
- Running mode: 18.0 km/h base speed, max 20km transfers

## Comparison to Record
- Guinness World Record: **17h46m** (Robin Otter & Thomas Sheat, August 2024)
- Solver best (272/272): **18h05m**
- Gap: ~19 minutes — likely due to:
  1. Solver uses greedy heuristic, not globally optimal ordering
  2. Human competitors use running transfers between stations (not yet in solver)
  3. Expert knowledge of line frequencies and real-time connections
  4. Timetable data may differ from actual day

## Next Steps
- [ ] LP relaxation lower bound (Phase 3) — needed to compute optimality gap
- [ ] Try different dates — K.O. shuttle frequency may vary
- [ ] More sophisticated search (simulated annealing on prefix + urgency + injection params)
- [ ] Multi-city generalization (NYC, Tokyo, Berlin)
