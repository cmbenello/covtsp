# Transit Optimizer — Results (2026-03-27)

## Station Count

**272 required stations** on the London Underground network (date: 2026-03-24).

After merging 2 duplicate pairs:
- Paddington: `940GZZLUPAH` (H&C) merged into `940GZZLUPAC` (main)
- Shepherd's Bush Central: `9400ZZLUSBC2` merged into `940GZZLUSBC`

Edgware Road (2 stations) and Hammersmith (2 stations) remain separate — they are genuinely distinct stations on different lines.

## Best Results

| Solver | Coverage | Time | Start Station | Start Time | Notes |
|--------|----------|------|--------------|------------|-------|
| **Prefix K.O. + urgency NN** | **272/272** | **18h05m** | Finchley Road | 05:41 | Best 272/272 — visit K.O. first |
| Prefix K.O. + urgency NN | 272/272 | 18h06m | Euston Square | 05:44 | Second-best 272/272 |
| Prefix K.O. + NN (urg=0) | 271/272 | 17h45m | Euston Square | 05:44 | Misses Mill Hill East |
| k=1 NN (old baseline) | 272/272 | 18h45m | Ealing Broadway | 06:33 | Previous best; late K.O. detour |
| Urgency NN + T4 inject | 271/272 | 17h47m | Ealing Broadway | 06:33 | Misses K.O. (1 shuttle/day) |
| Urgency NN (urg=0.6) | 271/272 | 17h27m | Ealing Broadway | 06:33 | Misses T4 (Heathrow loop) |

**Improvement: 18h45m → 18h05m (−40 minutes)** via early-morning K.O. prefix strategy.

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
- Charles's actual record: **13h40m** (set in real life)
- Solver best (272/272): **18h05m**
- Gap: ~4h25m — likely due to:
  1. Solver uses greedy heuristic, not globally optimal ordering
  2. Real challenge used expert knowledge of line frequencies and connections
  3. Timetable data may differ from actual day
  4. Running speed model may underestimate actual speeds on familiar routes

## Next Steps
- [ ] LP relaxation lower bound (Phase 3) — needed to compute optimality gap
- [ ] Try different dates — K.O. shuttle frequency may vary
- [ ] More sophisticated search (simulated annealing on prefix + urgency + injection params)
- [ ] Multi-city generalization (NYC, Tokyo, Berlin)
