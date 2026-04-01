"""Hunt for the 16h37m result seen in the killed focused_sweep run.

The killed run showed:
  Trial 3462: 16h37m (eps=0.05, uw=0.3, start=Upminster @ 06:15)

This script reproduces the exact seed computation from that run and
sweeps more broadly to find it or anything close.

Run log is written to results/find_1637_log.txt
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, time as time_mod
from datetime import date
from src.config import load_config
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser
from src.solver.greedy import GreedySolver
from src.solver.hard_stations import HardStationDetector, VisitWindowOptimizer, build_pairings

log_lines = []
def log(msg):
    print(msg)
    log_lines.append(msg)

cfg = load_config('configs/london.yaml')
parsed = GTFSParser(cfg).parse(date(2026, 3, 24))
n_req = len(parsed.required_station_ids)
log(f"Parsed: {len(parsed.stations)} stations, {len(parsed.segments)} segments, {n_req} required")

teg = TimeExpandedGraph.from_gtfs(parsed)
log(f"TEG: {teg.node_count} nodes, {teg.edge_count} edges")
solver = GreedySolver(teg, lookahead=1)

# Build pairings
force_hard = set()
approach = {}
for ov in cfg.hard_stations.overrides:
    if ov.force_hard: force_hard.add(ov.station_id)
    if ov.approach_via: approach[ov.station_id] = ov.approach_via
detector = HardStationDetector(teg)
profiles = detector.detect(parsed.required_station_ids, force_hard=force_hard, exclude=set())
for hp in profiles:
    if hp.station_id in approach: hp.junction_id = approach[hp.station_id]
wopt = VisitWindowOptimizer(teg)
for hp in profiles: hp.optimal_windows = wopt.compute_optimal_windows(hp)
pairings = build_pairings(teg, profiles)
log(f"{len(pairings)} pairings")

# Current best to beat
current_best_s = 61980  # 17h13m

station = '940GZZLUUPM'  # Upminster
start_time = 6 * 3600 + 15 * 60  # 06:15 = 22500s

best = None
best_trial_info = None
all_improvements = []

# ============================================================
# Run 1: Exact seed reproduction from focused_sweep.py
# The original used: hash((s_station, s_time, eps, uw, trial))
# with eps in [0.05, 0.1, 0.15, 0.2, 0.3, 0.4]
# and uw in [0.3, 0.5, 0.7]
# Trial 3462 was at eps=0.05, uw=0.3
# ============================================================
log("\n=== Run 1: Exact seed reproduction (eps=0.05, uw=0.3, trials 0-5000) ===")
t0 = time_mod.time()
for trial in range(5000):
    seed = hash((station, start_time, 0.05, 0.3, trial)) & 0x7FFFFFFF
    r = solver.solve_randomized(station, parsed.required_station_ids, start_time,
                                epsilon=0.05, seed=seed, pairings=pairings)
    if r.stations_visited == n_req:
        if best is None or r.total_time_seconds < best.total_time_seconds:
            best = r
            best_trial_info = f"Run1 eps=0.05 uw=0.3 trial={trial}"
            ts = best.total_time_seconds
            all_improvements.append((ts, best_trial_info))
            log(f"  Trial {trial}: {ts//3600}h{(ts%3600)//60:02d}m ({ts}s) seed={seed}")

elapsed = time_mod.time() - t0
log(f"  Run 1 done in {elapsed:.0f}s")

# ============================================================
# Run 2: All epsilon/urgency combos from original (trials 0-5000 each)
# ============================================================
log("\n=== Run 2: All eps × uw combos (5000 trials each) ===")
t0 = time_mod.time()
for eps in [0.05, 0.1, 0.15, 0.2, 0.3, 0.4]:
    for uw in [0.3, 0.5, 0.7]:
        if eps == 0.05 and uw == 0.3:
            continue  # already did this in Run 1
        for trial in range(5000):
            seed = hash((station, start_time, eps, uw, trial)) & 0x7FFFFFFF
            r = solver.solve_randomized(station, parsed.required_station_ids, start_time,
                                        epsilon=eps, seed=seed, pairings=pairings)
            if r.stations_visited == n_req:
                if best is None or r.total_time_seconds < best.total_time_seconds:
                    best = r
                    best_trial_info = f"Run2 eps={eps} uw={uw} trial={trial}"
                    ts = best.total_time_seconds
                    all_improvements.append((ts, best_trial_info))
                    log(f"  eps={eps} uw={uw} trial={trial}: {ts//3600}h{(ts%3600)//60:02d}m ({ts}s)")

elapsed = time_mod.time() - t0
log(f"  Run 2 done in {elapsed:.0f}s")

# ============================================================
# Run 3: Try nearby start times (06:10, 06:20, 06:05, 06:25)
# ============================================================
log("\n=== Run 3: Nearby start times × randomized ===")
t0 = time_mod.time()
for t_offset in [-10, -5, 5, 10, -15, 15]:
    t = start_time + t_offset * 60
    t_h, t_m = t // 3600, (t % 3600) // 60
    for eps in [0.05, 0.1, 0.2]:
        for trial in range(3000):
            seed = hash((station, t, eps, 0.3, trial)) & 0x7FFFFFFF
            r = solver.solve_randomized(station, parsed.required_station_ids, t,
                                        epsilon=eps, seed=seed, pairings=pairings)
            if r.stations_visited == n_req:
                if best is None or r.total_time_seconds < best.total_time_seconds:
                    best = r
                    best_trial_info = f"Run3 t={t_h:02d}:{t_m:02d} eps={eps} trial={trial}"
                    ts = best.total_time_seconds
                    all_improvements.append((ts, best_trial_info))
                    log(f"  {t_h:02d}:{t_m:02d} eps={eps} trial={trial}: {ts//3600}h{(ts%3600)//60:02d}m ({ts}s)")

elapsed = time_mod.time() - t0
log(f"  Run 3 done in {elapsed:.0f}s")

# ============================================================
# Run 4: Other top stations from the original sweep
# ============================================================
log("\n=== Run 4: Other top stations ===")
other_stations = [
    ('940GZZLUHGR', 'Hanger Lane', 7*3600),       # original best from main backtest
    ('940GZZLUWFN', 'Watford', 6*3600),
    ('940GZZLUEBY', 'Bromley-by-Bow', 6*3600+30*60),
    ('940GZZLUEPG', 'Epping', 6*3600),
]
t0 = time_mod.time()
for sid, name, base_t in other_stations:
    for t_offset in range(-30, 31, 5):  # ±30 min in 5-min steps
        t = base_t + t_offset * 60
        if t < 5 * 3600:
            continue
        # Deterministic first
        r = solver.solve_with_pairings(sid, parsed.required_station_ids, t,
                                        pairings=pairings, urgency_weight=0.5)
        if r.stations_visited == n_req and (best is None or r.total_time_seconds < best.total_time_seconds):
            best = r
            best_trial_info = f"Run4 {name} t={t//3600:02d}:{(t%3600)//60:02d} deterministic"
            ts = best.total_time_seconds
            all_improvements.append((ts, best_trial_info))
            log(f"  {name} @ {t//3600:02d}:{(t%3600)//60:02d} determ: {ts//3600}h{(ts%3600)//60:02d}m ({ts}s)")
        # Randomized
        for trial in range(2000):
            seed = hash((sid, t, 0.05, 0.3, trial)) & 0x7FFFFFFF
            r = solver.solve_randomized(sid, parsed.required_station_ids, t,
                                        epsilon=0.05, seed=seed, pairings=pairings)
            if r.stations_visited == n_req and (best is None or r.total_time_seconds < best.total_time_seconds):
                best = r
                best_trial_info = f"Run4 {name} t={t//3600:02d}:{(t%3600)//60:02d} eps=0.05 trial={trial}"
                ts = best.total_time_seconds
                all_improvements.append((ts, best_trial_info))
                log(f"  {name} @ {t//3600:02d}:{(t%3600)//60:02d} trial={trial}: {ts//3600}h{(ts%3600)//60:02d}m ({ts}s)")

elapsed = time_mod.time() - t0
log(f"  Run 4 done in {elapsed:.0f}s")

# ============================================================
# Summary
# ============================================================
ts = best.total_time_seconds
log(f"\n{'='*60}")
log(f"FINAL BEST: {best.stations_visited}/{n_req} in {ts//3600}h{(ts%3600)//60:02d}m ({ts}s)")
log(f"  Found by: {best_trial_info}")
log(f"  Gap to record (17h45m): {ts - 63900}s ({(ts - 63900) // 60} min)")
log(f"\nAll improvements found:")
for imp_ts, imp_info in sorted(all_improvements):
    log(f"  {imp_ts//3600}h{(imp_ts%3600)//60:02d}m ({imp_ts}s) — {imp_info}")

# Save if better than current best
if ts < current_best_s:
    data = {
        'total_time_seconds': ts,
        'total_time_formatted': f"{ts//3600}h{(ts%3600)//60:02d}m",
        'stations_visited': best.stations_visited,
        'stations_required': n_req,
        'start_station': station if 'Run4' not in best_trial_info else best_trial_info.split()[1],
        'found_by': best_trial_info,
        'gap_to_record_seconds': ts - 63900,
        'route': best.visits,
    }
    Path('results/focused_sweep_best.json').write_text(json.dumps(data, indent=2))
    log(f"\nSaved to results/focused_sweep_best.json (beats previous {current_best_s//3600}h{(current_best_s%3600)//60:02d}m)")
else:
    log(f"\nNo improvement over current best ({current_best_s//3600}h{(current_best_s%3600)//60:02d}m)")

# Save log
Path('results/find_1637_log.txt').write_text('\n'.join(log_lines))
log("Log saved to results/find_1637_log.txt")
