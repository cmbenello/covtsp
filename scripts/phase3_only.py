"""Phase 3 only: Fine sweep ±20 min around Upminster with heavy randomized search.

This is where the 16h53m result lives. Skips the slow Chesham seeds.
Saves every improvement incrementally.

Usage: python -u scripts/phase3_only.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import time as time_mod
from datetime import date, datetime

from src.config import load_config
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser
from src.solver.greedy import GreedySolver
from src.solver.hard_stations import (
    HardStationDetector, VisitWindowOptimizer, build_pairings
)

RESULTS_DIR = Path("results/search_log")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = RESULTS_DIR / "search_log.md"
BEST_ROUTE_PATH = Path("results/focused_sweep_best.json")
WORLD_RECORD_S = 63900  # 17h45m

session_start = datetime.now()
log_entries = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    log_entries.append(line)

def save_log():
    header = f"\n## Session {session_start.strftime('%Y-%m-%d %H:%M')} — Phase 3 fine sweep\n\n"
    with open(LOG_PATH, "a") as f:
        f.write(header)
        for entry in log_entries:
            f.write(entry + "\n")
        f.write("\n")

def save_improvement(route, route_time_s, station_id, station_name, start_time,
                     solver_params, improvement_num):
    t_h = route_time_s // 3600
    t_m = (route_time_s % 3600) // 60
    filename = f"improvement_{improvement_num:03d}_{t_h}h{t_m:02d}m.json"
    data = {
        "improvement_number": improvement_num,
        "timestamp": datetime.now().isoformat(),
        "total_time_seconds": route_time_s,
        "total_time_formatted": f"{t_h}h{t_m:02d}m",
        "stations_visited": route.stations_visited,
        "start_station_id": station_id,
        "start_station_name": station_name,
        "start_time_seconds": start_time,
        "start_time_formatted": f"{start_time//3600:02d}:{(start_time%3600)//60:02d}",
        "gap_to_record_seconds": route_time_s - WORLD_RECORD_S,
        "gap_to_record_formatted": f"{(route_time_s - WORLD_RECORD_S) // 60} min",
        "solver_params": solver_params,
        "route": route.visits,
    }
    (RESULTS_DIR / filename).write_text(json.dumps(data, indent=2))
    BEST_ROUTE_PATH.write_text(json.dumps(data, indent=2))
    return filename

# ── Setup ───────────────────────────────────────────────────
log("=" * 60)
log("PHASE 3: Fine sweep around Upminster ±20 min")
log("=" * 60)

log("Parsing GTFS...")
cfg = load_config("configs/london.yaml")
parsed = GTFSParser(cfg).parse(date(2026, 3, 24))
n_req = len(parsed.required_station_ids)
log(f"  {len(parsed.stations)} stations, {len(parsed.segments)} segments")

log("Building TEG...")
teg = TimeExpandedGraph.from_gtfs(parsed)
log(f"  {teg.node_count} nodes, {teg.edge_count} edges")
solver = GreedySolver(teg, lookahead=1)

log("Building pairings...")
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
log(f"  {len(pairings)} pairings ready")

# ── Search state ────────────────────────────────────────────
best_route = None
best_time_s = float("inf")
best_info = ""
# Continue numbering from previous session
improvement_count = len(list(RESULTS_DIR.glob("improvement_*.json")))
log(f"  {improvement_count} previous improvements found, continuing from #{improvement_count + 1}")

def check_improvement(route, station_id, start_time, params_str):
    global best_route, best_time_s, best_info, improvement_count
    if route.stations_visited != n_req:
        return
    if route.total_time_seconds >= best_time_s:
        return
    best_route = route
    best_time_s = route.total_time_seconds
    best_info = params_str
    improvement_count += 1
    t_h = best_time_s // 3600
    t_m = (best_time_s % 3600) // 60
    gap = best_time_s - WORLD_RECORD_S
    station_name = parsed.stations[station_id].name if station_id in parsed.stations else station_id
    st_h, st_m = start_time // 3600, (start_time % 3600) // 60
    log(f"  *** IMPROVEMENT #{improvement_count}: {t_h}h{t_m:02d}m ({best_time_s}s) | "
        f"{station_name} @ {st_h:02d}:{st_m:02d} | "
        f"gap to record: {gap//60} min | {params_str}")
    fname = save_improvement(route, best_time_s, station_id, station_name,
                             start_time, params_str, improvement_count)
    log(f"      Saved: {fname}")

# ════════════════════════════════════════════════════════════
# PHASE 3A: Upminster ±20 min, 1-min steps, deterministic first
# ════════════════════════════════════════════════════════════
station = "940GZZLUUPM"
base_time = 6 * 3600 + 15 * 60  # 06:15

log("")
log("Phase 3A: Deterministic sweep, Upminster 05:55–06:35 (1-min steps)")
log("-" * 40)
t0 = time_mod.time()

for t_offset in range(-20, 21):  # ±20 min
    t = base_time + t_offset * 60
    if t < 5 * 3600:
        continue
    start_nodes = teg.get_start_nodes(station, t)
    if not start_nodes:
        continue
    try:
        route = solver.solve_with_pairings(
            station, parsed.required_station_ids, t,
            pairings=pairings, urgency_weight=0.5
        )
        check_improvement(route, station, t, "deterministic pairings uw=0.5")
    except Exception:
        pass

elapsed = time_mod.time() - t0
log(f"Phase 3A done in {elapsed:.0f}s | best: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m")

# ════════════════════════════════════════════════════════════
# PHASE 3B: Heavy randomized from every 1-min start
# ════════════════════════════════════════════════════════════
log("")
log("Phase 3B: Randomized sweep, Upminster 05:55–06:35 (1-min steps)")
log("  7000 trials × 4 epsilons per start time")
log("-" * 40)

epsilons = [0.05, 0.1, 0.15, 0.2]
trials_per = 7000
t0 = time_mod.time()
total_trials = 0

for t_offset in range(-20, 21):
    t = base_time + t_offset * 60
    if t < 5 * 3600:
        continue
    start_nodes = teg.get_start_nodes(station, t)
    if not start_nodes:
        continue

    th, tm = t // 3600, (t % 3600) // 60

    for eps in epsilons:
        for trial in range(trials_per):
            seed = hash((station, t, eps, 0.3, trial)) & 0x7FFFFFFF
            try:
                route = solver.solve_randomized(
                    station, parsed.required_station_ids, t,
                    epsilon=eps, seed=seed, pairings=pairings
                )
            except Exception:
                total_trials += 1
                continue
            total_trials += 1
            check_improvement(route, station, t,
                              f"randomized eps={eps} trial={trial} seed={seed}")

    elapsed = time_mod.time() - t0
    rate = total_trials / elapsed if elapsed > 0 else 0
    log(f"  {th:02d}:{tm:02d} done | {total_trials} trials | "
        f"{rate:.0f}/s | best: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m")

p3b_elapsed = time_mod.time() - t0
log(f"\nPhase 3B done: {total_trials} trials in {p3b_elapsed:.0f}s")

# ════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════
log("")
log("=" * 60)
log("FINAL RESULTS")
log("=" * 60)
log(f"Total improvements this session: {improvement_count}")
log(f"Best time: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m ({best_time_s}s)")
log(f"Gap to world record (17h45m): {(best_time_s - WORLD_RECORD_S) // 60} min")
log(f"Found by: {best_info}")
log(f"All routes saved to: {RESULTS_DIR}/")
log(f"Best route: {BEST_ROUTE_PATH}")

save_log()
log(f"Log appended to: {LOG_PATH}")
