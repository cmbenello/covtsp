"""Focused sweep: fine-grained times + heavy randomized search from top starts."""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import time as time_mod
from datetime import date

from rich.console import Console

from src.config import load_config
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser
from src.solver.greedy import GreedySolver
from src.solver.hard_stations import HardStationDetector, VisitWindowOptimizer, SkeletonScheduler, build_pairings

console = Console()

cfg = load_config("configs/london.yaml")
target_date = date(2026, 3, 24)

console.print("[bold]Focused sweep — fine times + heavy randomization[/bold]")
console.print("[dim]Parsing GTFS...[/dim]")
parser = GTFSParser(cfg)
parsed = parser.parse(target_date)
n_required = len(parsed.required_station_ids)
console.print(f"  {len(parsed.stations)} stations, {len(parsed.segments)} segments, {n_required} required")

console.print("[dim]Building TEG...[/dim]")
teg = TimeExpandedGraph.from_gtfs(parsed)
console.print(f"  {teg.node_count} nodes, {teg.edge_count} edges")

solver = GreedySolver(teg, lookahead=1)

# Detect hard stations + build pairings
force_hard_ids = set()
exclude_ids = set()
approach_overrides = {}
if hasattr(cfg, "hard_stations"):
    for ov in cfg.hard_stations.overrides:
        if ov.force_hard:
            force_hard_ids.add(ov.station_id)
        if ov.approach_via:
            approach_overrides[ov.station_id] = ov.approach_via

detector = HardStationDetector(teg)
hard_profiles = detector.detect(
    parsed.required_station_ids,
    hardness_threshold=getattr(cfg.hard_stations, "hardness_threshold", None) if hasattr(cfg, "hard_stations") else None,
    force_hard=force_hard_ids,
    exclude=exclude_ids,
)
for hp in hard_profiles:
    if hp.station_id in approach_overrides:
        hp.junction_id = approach_overrides[hp.station_id]

window_opt = VisitWindowOptimizer(teg)
for hp in hard_profiles:
    hp.optimal_windows = window_opt.compute_optimal_windows(hp)

hard_pairings = build_pairings(teg, hard_profiles)
console.print(f"  {len(hard_pairings)} hard station pairings")

skel_scheduler = SkeletonScheduler(teg)

# ============================================================
# Part 1: Fine-grained time sweep (5-min steps) from top starts
# ============================================================
# Previous best came from Hanger Lane @ 07:00 and skeleton sweep.
# Try the top ~20 stations from initial sweep + fine times.

console.print("\n[bold]Part 1: Fine-grained pairings + skeleton sweep[/bold]")

best_route = None
best_info = None

def _route_better(new, old):
    if old is None:
        return True
    if new.stations_visited > old.stations_visited:
        return True
    return new.stations_visited == old.stations_visited and new.total_time_seconds < old.total_time_seconds

def _fmt(r):
    return f"{r.total_time_seconds // 3600}h{(r.total_time_seconds % 3600) // 60:02d}m"

# First: quick k=1 sweep to find top-20 stations
console.print("[dim]Quick k=1 sweep to find top stations...[/dim]")
all_stations = sorted(parsed.required_station_ids)
coarse_times = list(range(5 * 3600, 9 * 3600 + 1, 30 * 60))
station_best: dict[str, int] = {}

t0 = time_mod.time()
for gs in all_stations:
    for t in coarse_times:
        start_nodes = teg.get_start_nodes(gs, t)
        if not start_nodes:
            continue
        try:
            route = solver.solve_with_pairings(gs, parsed.required_station_ids, t, pairings=hard_pairings, urgency_weight=0.5)
        except Exception:
            continue
        if route.stations_visited == n_required:
            if gs not in station_best or route.total_time_seconds < station_best[gs]:
                station_best[gs] = route.total_time_seconds
            if _route_better(route, best_route):
                best_route = route
                best_info = (gs, t)

elapsed = time_mod.time() - t0
console.print(f"  Coarse sweep done in {elapsed:.0f}s — best: {_fmt(best_route)} from {best_info}")
console.print(f"  {len(station_best)} stations achieve 272/272")

# Top 20 stations by best time
top_stations = sorted(station_best.items(), key=lambda x: x[1])[:20]
console.print(f"\n[dim]Top 20 starts:[/dim]")
for gs, t in top_stations:
    name = parsed.stations[gs].name if gs in parsed.stations else gs
    console.print(f"  {name}: {t//3600}h{(t%3600)//60:02d}m")

# Fine-grained sweep: 5-min steps from 05:00 to 09:00 for top 20
console.print(f"\n[dim]Fine sweep (5-min steps) for top 20 stations...[/dim]")
fine_times = list(range(5 * 3600, 9 * 3600 + 1, 5 * 60))  # 5-min steps

t0 = time_mod.time()
fine_count = 0
for gs, _ in top_stations:
    name = parsed.stations[gs].name if gs in parsed.stations else gs
    for t in fine_times:
        start_nodes = teg.get_start_nodes(gs, t)
        if not start_nodes:
            continue
        fine_count += 1
        # Try pairings solver
        try:
            route = solver.solve_with_pairings(gs, parsed.required_station_ids, t, pairings=hard_pairings, urgency_weight=0.5)
        except Exception:
            continue
        if _route_better(route, best_route):
            best_route = route
            best_info = (gs, t)
            console.print(
                f"  [green]Pairings: {route.stations_visited}/{n_required} {_fmt(route)} "
                f"from {name} @ {t//3600:02d}:{(t%3600)//60:02d}[/green]"
            )
        # Try skeleton solver
        try:
            skeleton = skel_scheduler.build_skeleton(hard_profiles, start_time=t)
            route2 = solver.solve_skeleton(gs, parsed.required_station_ids, t, skeleton=skeleton, urgency_weight=0.3)
        except Exception:
            continue
        if _route_better(route2, best_route):
            best_route = route2
            best_info = (gs, t)
            console.print(
                f"  [green]Skeleton: {route2.stations_visited}/{n_required} {_fmt(route2)} "
                f"from {name} @ {t//3600:02d}:{(t%3600)//60:02d}[/green]"
            )

fine_elapsed = time_mod.time() - t0
console.print(f"  Fine sweep done: {fine_count} runs in {fine_elapsed:.0f}s")
gs_best, t_best = best_info
name_best = parsed.stations[gs_best].name if gs_best in parsed.stations else gs_best
console.print(
    f"  [bold green]Best after fine sweep: {_fmt(best_route)} "
    f"from {name_best} @ {t_best//3600:02d}:{(t_best%3600)//60:02d}[/bold green]"
)

# ============================================================
# Part 2: Heavy randomized search from top 10 starts (with pairings)
# ============================================================
console.print(f"\n[bold]Part 2: Heavy randomized search (pairings-aware)[/bold]")

# Collect top-10 unique (station, time) seeds with 272/272 coverage
top_seeds: list[tuple[int, str, int]] = []
seen = set()
for gs, _ in top_stations:
    for t in fine_times:
        start_nodes = teg.get_start_nodes(gs, t)
        if not start_nodes:
            continue
        try:
            route = solver.solve_with_pairings(gs, parsed.required_station_ids, t, pairings=hard_pairings, urgency_weight=0.5)
        except Exception:
            continue
        if route.stations_visited == n_required:
            key = (gs, t)
            if key not in seen:
                seen.add(key)
                top_seeds.append((route.total_time_seconds, gs, t))

top_seeds.sort()
top_seeds = top_seeds[:10]

console.print(f"  Top 10 seeds:")
for rank, (ts, gs, t) in enumerate(top_seeds):
    name = parsed.stations[gs].name if gs in parsed.stations else gs
    console.print(f"    {rank+1}. {name} @ {t//3600:02d}:{(t%3600)//60:02d} — {ts//3600}h{(ts%3600)//60:02d}m")

# Randomized search: 5000 trials × 6 epsilons × top 10
n_trials = 5000
epsilons = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4]
# Also vary urgency weights
urgency_weights = [0.3, 0.5, 0.7]

t0 = time_mod.time()
trial_count = 0
improvements = 0

for _, s_station, s_time in top_seeds:
    for eps in epsilons:
        for uw in urgency_weights:
            for trial in range(n_trials):
                seed = hash((s_station, s_time, eps, uw, trial)) & 0x7FFFFFFF
                try:
                    route = solver.solve_randomized(
                        s_station, parsed.required_station_ids, s_time,
                        epsilon=eps, seed=seed,
                        pairings=hard_pairings,
                    )
                except Exception:
                    trial_count += 1
                    continue
                trial_count += 1

                if route.stations_visited == n_required and _route_better(route, best_route):
                    best_route = route
                    best_info = (s_station, s_time)
                    improvements += 1
                    name = parsed.stations[s_station].name if s_station in parsed.stations else s_station
                    console.print(
                        f"  [green]Trial {trial_count}: {_fmt(route)} "
                        f"(eps={eps}, uw={uw}, start={name} @ "
                        f"{s_time//3600:02d}:{(s_time%3600)//60:02d})[/green]"
                    )

            if trial_count % 50000 == 0:
                elapsed = time_mod.time() - t0
                console.print(
                    f"  [{trial_count} trials, {elapsed:.0f}s, "
                    f"{improvements} improvements, best: {_fmt(best_route)}]"
                )

total_elapsed = time_mod.time() - t0
console.print(
    f"\n[bold]Randomized done: {trial_count} trials in {total_elapsed:.0f}s, "
    f"{improvements} improvements[/bold]"
)

gs_best, t_best = best_info
name_best = parsed.stations[gs_best].name if gs_best in parsed.stations else gs_best
console.print(
    f"\n[bold green]FINAL BEST: {best_route.stations_visited}/{n_required} in {_fmt(best_route)} "
    f"from {name_best} @ {t_best//3600:02d}:{(t_best%3600)//60:02d}[/bold green]"
)
console.print(f"  Gap to record (17h45m): {best_route.total_time_seconds - 63900}s "
              f"({(best_route.total_time_seconds - 63900) // 60} min)")

# Save best route
output_path = Path("results/focused_sweep_best.json")
route_data = {
    "total_time_seconds": best_route.total_time_seconds,
    "total_time_formatted": _fmt(best_route),
    "stations_visited": best_route.stations_visited,
    "start_station": gs_best,
    "start_station_name": name_best,
    "start_time": t_best,
    "route": [v for v in best_route.visits],
}
output_path.write_text(json.dumps(route_data, indent=2))
console.print(f"  Saved to {output_path}")
