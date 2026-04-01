"""Systematic search for best London Tube Challenge route.

Saves every improvement as a separate route JSON + maintains a running log.
All results go to results/search_log/ for blog documentation.

Usage: python -u scripts/search_best.py
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

# ── Config ──────────────────────────────────────────────────
RESULTS_DIR = Path("results/search_log")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = RESULTS_DIR / "search_log.md"
BEST_ROUTE_PATH = Path("results/focused_sweep_best.json")
WORLD_RECORD_S = 63900  # 17h45m

# ── Logging ─────────────────────────────────────────────────
session_start = datetime.now()
log_entries = []

def log(msg, also_log_file=True):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if also_log_file:
        log_entries.append(line)

def save_log():
    """Append this session's log to the running log file."""
    header = f"\n## Session {session_start.strftime('%Y-%m-%d %H:%M')}\n\n"
    with open(LOG_PATH, "a") as f:
        f.write(header)
        for entry in log_entries:
            f.write(entry + "\n")
        f.write("\n")

def save_improvement(route, route_time_s, station_id, station_name, start_time,
                     solver_params, improvement_num):
    """Save a route improvement as JSON."""
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

    # Also overwrite the main best file
    BEST_ROUTE_PATH.write_text(json.dumps(data, indent=2))

    return filename


# ── Setup ───────────────────────────────────────────────────
log("=" * 60)
log("LONDON TUBE CHALLENGE — SYSTEMATIC ROUTE SEARCH")
log("=" * 60)
log(f"World record: 17h45m ({WORLD_RECORD_S}s)")
log("")

log("Parsing GTFS for 2026-03-24 (Tuesday)...")
t0 = time_mod.time()
cfg = load_config("configs/london.yaml")
parsed = GTFSParser(cfg).parse(date(2026, 3, 24))
n_req = len(parsed.required_station_ids)
log(f"  {len(parsed.stations)} stations, {len(parsed.segments)} segments, {n_req} required")
log(f"  Movement mode: {cfg.movement_mode} ({cfg.running_speed_kmh} km/h base)")

log("Building time-expanded graph...")
teg = TimeExpandedGraph.from_gtfs(parsed)
log(f"  {teg.node_count} nodes, {teg.edge_count} edges")

solver = GreedySolver(teg, lookahead=1)

log("Detecting hard stations...")
force_hard = set()
approach = {}
for ov in cfg.hard_stations.overrides:
    if ov.force_hard:
        force_hard.add(ov.station_id)
    if ov.approach_via:
        approach[ov.station_id] = ov.approach_via
detector = HardStationDetector(teg)
profiles = detector.detect(parsed.required_station_ids,
                           force_hard=force_hard, exclude=set())
for hp in profiles:
    if hp.station_id in approach:
        hp.junction_id = approach[hp.station_id]
wopt = VisitWindowOptimizer(teg)
for hp in profiles:
    hp.optimal_windows = wopt.compute_optimal_windows(hp)
pairings = build_pairings(teg, profiles)

log(f"  {len(pairings)} hard station pairings:")
for p in pairings:
    tag = " [PREFIX]" if p.is_prefix else ""
    log(f"    {p.hard_station_name} → {p.junction_name} ({p.round_trip_cost_s:.0f}s RT){tag}")

setup_time = time_mod.time() - t0
log(f"\nSetup complete in {setup_time:.0f}s")
log("")

# ── Search state ────────────────────────────────────────────
best_route = None
best_time_s = float("inf")
best_info = ""
improvement_count = 0

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
    st_h = start_time // 3600
    st_m = (start_time % 3600) // 60

    log(f"  *** IMPROVEMENT #{improvement_count}: {t_h}h{t_m:02d}m ({best_time_s}s) | "
        f"{station_name} @ {st_h:02d}:{st_m:02d} | "
        f"gap to record: {gap//60} min | {params_str}")

    fname = save_improvement(route, best_time_s, station_id, station_name,
                             start_time, params_str, improvement_count)
    log(f"      Saved: {fname}")


# ════════════════════════════════════════════════════════════
# PHASE 1: Deterministic pairings sweep — all stations × fine times
# ════════════════════════════════════════════════════════════
log("=" * 60)
log("PHASE 1: Deterministic pairings sweep")
log("  All 272 stations × 5-min steps (05:00–09:00)")
log("=" * 60)

all_stations = sorted(parsed.required_station_ids)
fine_times = list(range(5 * 3600, 9 * 3600 + 1, 5 * 60))

t0 = time_mod.time()
runs = 0
full_coverage_starts = []

for i, gs in enumerate(all_stations):
    for t in fine_times:
        start_nodes = teg.get_start_nodes(gs, t)
        if not start_nodes:
            continue
        runs += 1
        try:
            route = solver.solve_with_pairings(
                gs, parsed.required_station_ids, t,
                pairings=pairings, urgency_weight=0.5
            )
        except Exception:
            continue

        if route.stations_visited == n_req:
            full_coverage_starts.append((route.total_time_seconds, gs, t))
        check_improvement(route, gs, t, "deterministic pairings uw=0.5")

    if (i + 1) % 50 == 0:
        elapsed = time_mod.time() - t0
        log(f"  [{i+1}/{len(all_stations)}] {runs} runs in {elapsed:.0f}s | "
            f"best: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m")

p1_elapsed = time_mod.time() - t0
log(f"\nPhase 1 done: {runs} runs in {p1_elapsed:.0f}s")
log(f"  {len(full_coverage_starts)} starts achieve 272/272")
log(f"  Best: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m ({best_time_s}s)")
log("")


# ════════════════════════════════════════════════════════════
# PHASE 2: Randomized search from top 15 deterministic starts
# ════════════════════════════════════════════════════════════
full_coverage_starts.sort()
top_seeds = full_coverage_starts[:15]

log("=" * 60)
log("PHASE 2: Randomized search from top 15 starts")
log("  5000 trials × 6 epsilons per start")
log("=" * 60)

for rank, (ts, gs, t) in enumerate(top_seeds):
    name = parsed.stations[gs].name if gs in parsed.stations else gs
    log(f"  Seed {rank+1}: {name} @ {t//3600:02d}:{(t%3600)//60:02d} — "
        f"{ts//3600}h{(ts%3600)//60:02d}m")

epsilons = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4]
trials_per = 5000

t0 = time_mod.time()
total_trials = 0

for rank, (_, gs, t) in enumerate(top_seeds):
    name = parsed.stations[gs].name if gs in parsed.stations else gs
    log(f"\n  --- Seed {rank+1}/{len(top_seeds)}: {name} @ {t//3600:02d}:{(t%3600)//60:02d} ---")

    for eps in epsilons:
        for trial in range(trials_per):
            seed = hash((gs, t, eps, 0.3, trial)) & 0x7FFFFFFF
            try:
                route = solver.solve_randomized(
                    gs, parsed.required_station_ids, t,
                    epsilon=eps, seed=seed, pairings=pairings
                )
            except Exception:
                total_trials += 1
                continue
            total_trials += 1
            check_improvement(route, gs, t, f"randomized eps={eps} trial={trial} seed={seed}")

    elapsed = time_mod.time() - t0
    rate = total_trials / elapsed if elapsed > 0 else 0
    log(f"  Seed {rank+1} done | {total_trials} total trials | "
        f"{rate:.0f} trials/s | best: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m")

p2_elapsed = time_mod.time() - t0
log(f"\nPhase 2 done: {total_trials} trials in {p2_elapsed:.0f}s")
log(f"  Best: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m ({best_time_s}s)")
log("")


# ════════════════════════════════════════════════════════════
# PHASE 3: Fine time sweep around best start ±15 min
# ════════════════════════════════════════════════════════════
if best_route:
    # Find best start info
    best_start_station = top_seeds[0][1]
    best_start_time = top_seeds[0][2]
    for ts, gs, t in full_coverage_starts:
        if ts == best_time_s:
            best_start_station = gs
            best_start_time = t
            break

    log("=" * 60)
    log("PHASE 3: Fine sweep ±15 min around best start + randomized")
    log("=" * 60)

    t0 = time_mod.time()
    total_trials = 0

    for t_offset in range(-15, 16):  # ±15 min, 1-min steps
        t = best_start_time + t_offset * 60
        if t < 5 * 3600:
            continue
        start_nodes = teg.get_start_nodes(best_start_station, t)
        if not start_nodes:
            continue

        # Deterministic
        try:
            route = solver.solve_with_pairings(
                best_start_station, parsed.required_station_ids, t,
                pairings=pairings, urgency_weight=0.5
            )
            check_improvement(route, best_start_station, t, "deterministic pairings (fine)")
        except Exception:
            pass

        # Randomized
        for eps in [0.05, 0.1, 0.15, 0.2]:
            for trial in range(5000):
                seed = hash((best_start_station, t, eps, 0.3, trial)) & 0x7FFFFFFF
                try:
                    route = solver.solve_randomized(
                        best_start_station, parsed.required_station_ids, t,
                        epsilon=eps, seed=seed, pairings=pairings
                    )
                except Exception:
                    total_trials += 1
                    continue
                total_trials += 1
                check_improvement(route, best_start_station, t,
                                  f"randomized (fine) eps={eps} trial={trial} seed={seed}")

    p3_elapsed = time_mod.time() - t0
    log(f"\nPhase 3 done: {total_trials} trials in {p3_elapsed:.0f}s")
    log(f"  Best: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m ({best_time_s}s)")


# ════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════
total_elapsed = time_mod.time() - (session_start.timestamp())
log("")
log("=" * 60)
log("FINAL RESULTS")
log("=" * 60)
log(f"Total improvements found: {improvement_count}")
log(f"Best time: {best_time_s//3600}h{(best_time_s%3600)//60:02d}m ({best_time_s}s)")
log(f"Gap to world record (17h45m): {(best_time_s - WORLD_RECORD_S) // 60} min "
    f"({best_time_s - WORLD_RECORD_S}s)")
log(f"Found by: {best_info}")
log(f"Total runtime: {total_elapsed / 60:.1f} min")
log("")
log(f"All improvement routes saved to: {RESULTS_DIR}/")
log(f"Best route saved to: {BEST_ROUTE_PATH}")

# Save the full log
save_log()
log(f"Search log saved to: {LOG_PATH}")
