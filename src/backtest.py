"""Date-aware backtesting engine for transit optimization."""

import json
from datetime import date
from pathlib import Path

from rich.console import Console

from src.config import CityConfig, load_config
from src.graph.network import TransitNetwork
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser
from src.solver.greedy import GreedySolver
from src.solver.local_search import LocalSearchOptimizer, random_order_baseline
from src.solver.lp_bound import compute_lp_bound, compute_lp_bound_time_expanded, compute_optimality_gap
from src.solver.segment_solver import SegmentSolver
from src.solver.static_optimizer import StaticOptimizer

console = Console()


def backtest(
    config: CityConfig,
    target_date: date,
    output_path: str | Path | None = None,
    lookahead: int = 3,
    local_search_iterations: int = 500,
    compute_teg_lp: bool = False,
    solver_type: str = "greedy",
) -> dict:
    """Run a full optimization backtest for a given date.

    Pipeline:
    1. Parse GTFS data filtered to the target date
    2. Build time-expanded graph
    3. Run greedy solver with lookahead
    4. Improve with local search
    5. Compute LP lower bound (static + optionally time-expanded)
    6. Output results as JSON

    Args:
        config: City configuration.
        target_date: Date to backtest.
        output_path: Path to write JSON results (optional).
        lookahead: Greedy solver lookahead depth.
        local_search_iterations: Max local search iterations.
        compute_teg_lp: Whether to attempt the time-expanded LP bound.

    Returns:
        Results dict.
    """
    console.print(f"\n[bold]Backtesting {config.city_name} for {target_date}[/bold]")
    if config.movement_mode == "run":
        base = config.running_speed_kmh
        console.print(
            f"  [cyan]Running mode: {base} km/h base "
            f"({base * 1.15:.1f}/{base}/{base * 0.9:.1f} km/h by distance) | "
            f"Max transfer: {config.max_walk_distance_m}m[/cyan]"
        )

    # Step 1: Parse GTFS
    console.print("[dim]Parsing GTFS data...[/dim]")
    parser = GTFSParser(config)
    parsed = parser.parse(target_date)

    console.print(
        f"  Stations: {len(parsed.stations)} | "
        f"Segments: {len(parsed.segments)} | "
        f"Walking transfers: {len(parsed.walking_transfers)} | "
        f"Required: {len(parsed.required_station_ids)}"
    )

    if not parsed.segments:
        return {"error": f"No services found for {target_date}"}

    # Step 2: Build graphs
    console.print("[dim]Building time-expanded graph...[/dim]")
    teg = TimeExpandedGraph.from_gtfs(parsed)
    console.print(f"  Nodes: {teg.node_count} | Edges: {teg.edge_count}")

    console.print("[dim]Building static graph...[/dim]")
    static = TransitNetwork.from_gtfs(parsed)
    console.print(f"  Stations: {static.station_count} | Edges: {static.edge_count}")

    start_time = config.time_window.start_seconds

    if solver_type == "static":
        # ---------------------------------------------------------------
        # Static-plan + multi-start-station + multi-start-time TEG backtest
        # ---------------------------------------------------------------
        # Phase 0: Select candidate start stations.
        # Phase 1: For each, optimize station ordering on static graph (SA).
        # Phase 2: Backtest each ordering through real GTFS timetable at
        #   multiple candidate start times. Pick the best overall.
        # ---------------------------------------------------------------

        # Phase 0: Candidate start stations
        if config.start_station:
            candidate_stations = [config.start_station]
        else:
            candidate_stations = []
            req = parsed.required_station_ids

            # (a) Highest-degree hub
            hub = max(req, key=lambda s: static.graph.degree(s) if s in static.graph else 0)
            candidate_stations.append(hub)

            # (b) Most central station (min avg static distance to all others)
            # Build static cache if needed
            _ = teg.static_dist(hub, hub)
            apsp = teg._static_apsp
            best_centrality = float("inf")
            central = hub
            for s in req:
                if s in apsp:
                    avg_dist = sum(apsp[s].get(t, 1e9) for t in req) / len(req)
                    if avg_dist < best_centrality:
                        best_centrality = avg_dist
                        central = s
            if central != hub:
                candidate_stations.append(central)

            # (c) Geographic extremes (N, S, E, W)
            for key_fn in [
                lambda s: parsed.stations[s].lat,   # northernmost
                lambda s: -parsed.stations[s].lat,   # southernmost
                lambda s: parsed.stations[s].lon,    # easternmost
                lambda s: -parsed.stations[s].lon,   # westernmost
            ]:
                extreme = max((s for s in req if s in parsed.stations), key=key_fn)
                if extreme not in candidate_stations:
                    candidate_stations.append(extreme)

        console.print(f"  Candidate start stations: {len(candidate_stations)}")
        for cs in candidate_stations:
            name = parsed.stations[cs].name if cs in parsed.stations else cs
            console.print(f"    {cs} ({name})")

        # Phase 1 + 2: For each candidate, run SA + TEG backtest
        overall_best_route = None
        overall_best_start_time = start_time
        overall_best_station = candidate_stations[0]
        overall_best_order = None  # track winning ordering for repair

        candidate_time_starts = list(range(5 * 3600, 9 * 3600 + 1, 30 * 60))
        solver = GreedySolver(teg, lookahead=lookahead)

        for cs_idx, start_station in enumerate(candidate_stations):
            start_name = parsed.stations[start_station].name if start_station in parsed.stations else start_station
            console.print(
                f"\n[dim]--- Start station {cs_idx + 1}/{len(candidate_stations)}: "
                f"{start_name} ---[/dim]"
            )

            # Phase 1: Static SA optimization
            console.print("[dim]Phase 1: Static SA optimization...[/dim]")
            opt = StaticOptimizer(teg)
            static_order, static_cost = opt.optimize(
                required_stations=parsed.required_station_ids,
                start_station=start_station,
                max_iterations=50_000,
                n_restarts=5,
            )
            console.print(
                f"  [green]Static best: {static_cost:.0f}s "
                f"({int(static_cost) // 3600}h{(int(static_cost) % 3600) // 60}m) "
                f"[dim](lower bound — ignores timetable waits)[/dim][/green]"
            )

            # Phase 2: Backtest through TEG at multiple start times
            console.print("[dim]Phase 2: TEG backtest across start times (05:00 – 09:00)...[/dim]")

            for t in candidate_time_starts:
                h, m = t // 3600, (t % 3600) // 60
                route = solver.solve_fixed_order(static_order, start_time=t)
                if route.total_time_seconds > 0:
                    if overall_best_route is None or (
                        route.stations_visited > overall_best_route.stations_visited
                        or (route.stations_visited == overall_best_route.stations_visited
                            and route.total_time_seconds < overall_best_route.total_time_seconds)
                    ):
                        overall_best_route = route
                        overall_best_start_time = t
                        overall_best_station = start_station
                        overall_best_order = list(static_order)
                    console.print(
                        f"  Start {h:02d}:{m:02d} → "
                        f"{route.total_time_seconds // 3600}h{(route.total_time_seconds % 3600) // 60}m "
                        f"| {route.stations_visited} stations"
                    )
                else:
                    console.print(f"  Start {h:02d}:{m:02d} → [dim]no valid route[/dim]")

        if overall_best_route is None:
            return {"error": "Static optimizer produced no valid TEG route at any start time/station"}

        best_route = overall_best_route
        start_station = overall_best_station
        best_time = best_route.total_time_seconds
        start_time = overall_best_start_time

        best_h, best_m = start_time // 3600, (start_time % 3600) // 60
        start_name = parsed.stations[start_station].name if start_station in parsed.stations else start_station
        console.print(
            f"\n  [bold green]Best overall: {start_name} @ {best_h:02d}:{best_m:02d} → "
            f"{best_time // 3600}h{(best_time % 3600) // 60}m "
            f"| {best_route.stations_visited} stations[/bold green]"
        )

        # Phase 3: If stations are missed, try additional SA runs hoping for
        # an ordering that covers more. The SA is stochastic — different runs
        # produce different orderings, some of which hit all stations.
        visited_ids = {v["station_id"] for v in best_route.visits}
        missed_ids = parsed.required_station_ids - visited_ids
        n_required = len(parsed.required_station_ids)

        if missed_ids:
            n_missed = len(missed_ids)
            console.print(
                f"\n[dim]Phase 3: {n_missed} stations missed. "
                f"Running additional SA attempts...[/dim]"
            )

            for sid in sorted(missed_ids):
                nodes = teg._station_nodes.get(sid, [])
                name = parsed.stations[sid].name if sid in parsed.stations else sid
                if nodes:
                    last_time = nodes[-1][1]
                    console.print(
                        f"  {name}: last service at "
                        f"{(last_time // 3600) % 24:02d}:{(last_time % 3600) // 60:02d}"
                    )

            for extra in range(3):
                opt = StaticOptimizer(teg)
                trial_order, trial_cost = opt.optimize(
                    required_stations=parsed.required_station_ids,
                    start_station=start_station,
                    max_iterations=50_000,
                    n_restarts=3,
                )
                trial_route = solver.solve_fixed_order(trial_order, start_time=start_time)
                if trial_route.total_time_seconds > 0:
                    trial_visited = {v["station_id"] for v in trial_route.visits}
                    trial_missed = parsed.required_station_ids - trial_visited
                    console.print(
                        f"  Extra SA {extra + 1}: "
                        f"{trial_route.stations_visited}/{n_required} stations, "
                        f"{trial_route.total_time_seconds // 3600}h"
                        f"{(trial_route.total_time_seconds % 3600) // 60}m"
                    )
                    if (trial_route.stations_visited > best_route.stations_visited
                        or (trial_route.stations_visited == best_route.stations_visited
                            and trial_route.total_time_seconds < best_route.total_time_seconds)):
                        best_route = trial_route
                        best_time = best_route.total_time_seconds
                        overall_best_order = trial_order
                        missed_ids = trial_missed
                        if not missed_ids:
                            console.print("  [bold green]All stations covered![/bold green]")
                            break

            if missed_ids:
                console.print(f"  [yellow]Still missing {len(missed_ids)} stations after extra attempts[/yellow]")

    elif solver_type == "sweep":
        # ---------------------------------------------------------------
        # Sweep: 3-phase fast nearest-neighbor from every station
        # Phase 1: NN sweep (no forcing) — find best starts
        # Phase 2: Identify hard stations, forced-NN sweep for 274/274
        # Phase 3: Randomized search from best starts to improve time
        # ---------------------------------------------------------------
        import time as time_mod

        n_required = len(parsed.required_station_ids)
        sweep_start_times = list(range(5 * 3600, 8 * 3600 + 1, 30 * 60))
        all_stations = sorted(parsed.required_station_ids)
        solver = GreedySolver(teg, lookahead=1)

        # === Phase 1: Fast NN sweep ===
        console.print(
            f"\n[dim]Phase 1: NN sweep — {len(all_stations)} stations × "
            f"{len(sweep_start_times)} times[/dim]"
        )
        sweep_t0 = time_mod.time()
        best_route = None
        best_start_station = None
        best_start_time_val = start_time
        miss_count: dict[str, int] = {}
        completed = 0

        for gs_idx, gs in enumerate(all_stations):
            gs_name = parsed.stations[gs].name if gs in parsed.stations else gs
            station_best = None

            for t in sweep_start_times:
                start_nodes = teg.get_start_nodes(gs, t)
                if not start_nodes:
                    completed += 1
                    continue
                try:
                    route = solver.solve_fast(gs, parsed.required_station_ids, t)
                except Exception:
                    completed += 1
                    continue
                completed += 1

                if route.total_time_seconds <= 0:
                    continue

                # Track misses for hard-station detection
                visited = {v["station_id"] for v in route.visits}
                for m_sid in parsed.required_station_ids - visited:
                    miss_count[m_sid] = miss_count.get(m_sid, 0) + 1

                if station_best is None or (
                    route.stations_visited > station_best.stations_visited
                    or (route.stations_visited == station_best.stations_visited
                        and route.total_time_seconds < station_best.total_time_seconds)
                ):
                    station_best = route

                if best_route is None or (
                    route.stations_visited > best_route.stations_visited
                    or (route.stations_visited == best_route.stations_visited
                        and route.total_time_seconds < best_route.total_time_seconds)
                ):
                    best_route = route
                    best_start_station = gs
                    best_start_time_val = t

            if station_best and (gs_idx + 1) % 50 == 0:
                elapsed = time_mod.time() - sweep_t0
                rate = completed / elapsed if elapsed > 0 else 0
                console.print(
                    f"  [{gs_idx+1:3d}/{len(all_stations)}] "
                    f"best so far: {best_route.stations_visited}/{n_required}  "
                    f"[dim]({rate:.0f} runs/s)[/dim]"
                )

        p1_elapsed = time_mod.time() - sweep_t0
        console.print(
            f"  Phase 1 done in {p1_elapsed:.0f}s — "
            f"best: {best_route.stations_visited}/{n_required} "
            f"{best_route.total_time_seconds // 3600}h"
            f"{(best_route.total_time_seconds % 3600) // 60:02d}m"
        )

        # === Phase 2: Identify hard stations & forced sweep ===
        if best_route.stations_visited < n_required:
            # Find stations missed by >80% of runs (truly hard to visit)
            n_runs = completed
            hard_stations = [
                (sid, miss_count[sid])
                for sid in miss_count
                if miss_count[sid] > n_runs * 0.8
            ]
            hard_stations.sort(key=lambda x: -x[1])

            if hard_stations:
                console.print(f"\n[dim]Phase 2: Forcing {len(hard_stations)} hard stations[/dim]")
                for sid, cnt in hard_stations:
                    name = parsed.stations[sid].name if sid in parsed.stations else sid
                    nodes = teg._station_nodes.get(sid, [])
                    last_t = nodes[-1][1] if nodes else 0
                    console.print(
                        f"  {name}: missed {cnt}/{len(all_stations)} starts, "
                        f"last service {last_t // 3600:02d}:{(last_t % 3600) // 60:02d}"
                    )

                # Build forced-visit windows: each hard station gets
                # a window from its last service time - 1h to last service time
                forced_visits = []
                for sid, _ in hard_stations:
                    nodes = teg._station_nodes.get(sid, [])
                    if nodes:
                        last_t = nodes[-1][1]
                        # Window: [last - 1h, last]
                        forced_visits.append((sid, max(0, last_t - 3600), last_t))

                # Sweep with forced visits
                for gs in all_stations:
                    for t in sweep_start_times:
                        start_nodes = teg.get_start_nodes(gs, t)
                        if not start_nodes:
                            continue
                        try:
                            route = solver.solve_fast_with_forced(
                                gs, parsed.required_station_ids, t,
                                forced_visits=forced_visits,
                            )
                        except Exception:
                            continue

                        if route.total_time_seconds <= 0:
                            continue

                        if best_route is None or (
                            route.stations_visited > best_route.stations_visited
                            or (route.stations_visited == best_route.stations_visited
                                and route.total_time_seconds < best_route.total_time_seconds)
                        ):
                            best_route = route
                            best_start_station = gs
                            best_start_time_val = t

                console.print(
                    f"  Phase 2 best: {best_route.stations_visited}/{n_required} "
                    f"{best_route.total_time_seconds // 3600}h"
                    f"{(best_route.total_time_seconds % 3600) // 60:02d}m"
                )

        # === Phase 3: Randomized search from best starts ===
        if best_route.stations_visited == n_required:
            console.print(f"\n[dim]Phase 3: Randomized search (500 trials) to improve time[/dim]")
            # Collect forced visits from phase 2 (or empty)
            forced_visits = []
            visited_ids = {v["station_id"] for v in best_route.visits}
            for sid, cnt in sorted(miss_count.items(), key=lambda x: -x[1]):
                if cnt > len(all_stations) * 0.5:
                    nodes = teg._station_nodes.get(sid, [])
                    if nodes:
                        last_t = nodes[-1][1]
                        forced_visits.append((sid, max(0, last_t - 3600), last_t))

            for trial in range(500):
                route = solver.solve_randomized(
                    best_start_station, parsed.required_station_ids,
                    best_start_time_val, epsilon=0.2,
                    forced_visits=forced_visits if forced_visits else None,
                    seed=trial,
                )
                if (route.stations_visited == n_required
                    and route.total_time_seconds < best_route.total_time_seconds):
                    best_route = route
                    console.print(
                        f"  Trial {trial}: improved to "
                        f"{route.total_time_seconds // 3600}h"
                        f"{(route.total_time_seconds % 3600) // 60:02d}m"
                    )

        if best_route is None:
            return {"error": "Sweep found no valid routes"}

        start_station = best_start_station
        start_time = best_start_time_val
        best_time = best_route.total_time_seconds

        elapsed_total = time_mod.time() - sweep_t0
        start_name = parsed.stations[start_station].name if start_station in parsed.stations else start_station
        console.print(
            f"\n[bold green]Best: {start_name} @ "
            f"{start_time // 3600:02d}:{(start_time % 3600) // 60:02d} → "
            f"{best_time // 3600}h{(best_time % 3600) // 60:02d}m | "
            f"{best_route.stations_visited}/{n_required} stations "
            f"[dim]({elapsed_total / 60:.1f} min total)[/dim][/bold green]"
        )

    elif solver_type == "segment":
        # Step 3 (segment): Branch decomposition + simulated annealing
        console.print("[dim]Running segment solver (branch decomposition + SA)...[/dim]")
        seg_solver = SegmentSolver(teg, parsed)
        best_route = seg_solver.solve(
            parsed.required_station_ids,
            start_time=start_time,
            sa_iterations=3000,
        )
        console.print(
            f"  [green]Segment solver: {best_route.total_time_seconds}s "
            f"({best_route.total_time_seconds // 3600}h{(best_route.total_time_seconds % 3600) // 60}m) | "
            f"{best_route.stations_visited} stations[/green]"
        )
        best_time = best_route.total_time_seconds
        # Use auto-detected start station for reporting
        start_station = best_route.visits[0]["station_id"] if best_route.visits else "unknown"
    else:
        # Step 3 (greedy): Multi-start greedy solver
        # Try multiple start stations and start times to find the best
        # greedy solution. The greedy solver works in real TEG space, so
        # it makes timetable-aware decisions at every step.

        # Build candidate start stations
        if config.start_station:
            greedy_start_stations = [config.start_station]
        else:
            greedy_start_stations = []
            req = parsed.required_station_ids

            # Highest-degree hub
            hub = max(req, key=lambda s: static.graph.degree(s) if s in static.graph else 0)
            greedy_start_stations.append(hub)

            # Most central station
            _ = teg.static_dist(hub, hub)  # build cache
            apsp = teg._static_apsp
            best_centrality = float("inf")
            central = hub
            for s in req:
                if s in apsp:
                    avg_dist = sum(apsp[s].get(t, 1e9) for t in req) / len(req)
                    if avg_dist < best_centrality:
                        best_centrality = avg_dist
                        central = s
            if central != hub:
                greedy_start_stations.append(central)

            # Geographic extremes
            for key_fn in [
                lambda s: parsed.stations[s].lat,
                lambda s: -parsed.stations[s].lat,
                lambda s: parsed.stations[s].lon,
                lambda s: -parsed.stations[s].lon,
            ]:
                extreme = max((s for s in req if s in parsed.stations), key=key_fn)
                if extreme not in greedy_start_stations:
                    greedy_start_stations.append(extreme)

            # Limited-service stations as start candidates — starting here
            # guarantees they're visited, at no extra cost
            solver_tmp = GreedySolver(teg, lookahead=1)
            solver_tmp.set_deadlines()
            early_deadline = 21 * 3600
            for sid in req:
                if (solver_tmp._deadlines.get(sid, float("inf")) < early_deadline
                    and sid not in greedy_start_stations):
                    greedy_start_stations.append(sid)

        greedy_start_times = list(range(5 * 3600, 7 * 3600 + 1, 30 * 60))  # 05:00-07:00
        solver = GreedySolver(teg, lookahead=lookahead)
        solver.set_deadlines()

        console.print(
            f"[dim]Running greedy solver (lookahead={lookahead}) "
            f"from {len(greedy_start_stations)} stations × "
            f"{len(greedy_start_times)} start times...[/dim]"
        )

        best_route = None
        for gs in greedy_start_stations:
            gs_name = parsed.stations[gs].name if gs in parsed.stations else gs
            for t in greedy_start_times:
                h, m = t // 3600, (t % 3600) // 60
                route = solver.solve(gs, parsed.required_station_ids, t)
                if route.total_time_seconds > 0:
                    if best_route is None or (
                        route.stations_visited > best_route.stations_visited
                        or (route.stations_visited == best_route.stations_visited
                            and route.total_time_seconds < best_route.total_time_seconds)
                    ):
                        best_route = route
                        start_station = gs
                        start_time = t
                    console.print(
                        f"  {gs_name} @ {h:02d}:{m:02d} → "
                        f"{route.total_time_seconds // 3600}h"
                        f"{(route.total_time_seconds % 3600) // 60}m | "
                        f"{route.stations_visited} stations"
                    )

        if best_route is None:
            return {"error": "Greedy solver found no valid route"}

        start_name = parsed.stations[start_station].name if start_station in parsed.stations else start_station
        console.print(
            f"\n  [bold green]Best: {start_name} @ "
            f"{start_time // 3600:02d}:{(start_time % 3600) // 60:02d} → "
            f"{best_route.total_time_seconds // 3600}h"
            f"{(best_route.total_time_seconds % 3600) // 60}m | "
            f"{best_route.stations_visited} stations[/bold green]"
        )
        best_time = best_route.total_time_seconds

    # Diagnose missed stations
    visited_ids = {v["station_id"] for v in best_route.visits}
    missed_ids = parsed.required_station_ids - visited_ids
    if missed_ids:
        console.print(f"\n[bold yellow]Missed {len(missed_ids)} stations:[/bold yellow]")
        diagnoses = []
        for sid in sorted(missed_ids):
            name = parsed.stations[sid].name if sid in parsed.stations else sid
            if sid not in teg._station_nodes or not teg._station_nodes[sid]:
                reason = "no GTFS service on date"
            elif not any(
                teg.graph.has_edge(pred, node)
                for node in teg._station_nodes[sid]
                for pred in teg.graph.predecessors(node)
                if pred[0] != sid
            ):
                reason = "TEG-isolated (no incoming transit/walk edges)"
            else:
                # Station has TEG nodes and is reachable — solver didn't get there
                last_visit_time = best_route.visits[-1]["departure"] if best_route.visits else "?"
                reason = f"solver didn't reach (last visit at {last_visit_time})"
            diagnoses.append((sid, name, reason))
            console.print(f"  {name:<35} {reason}")

        # Summary
        reasons = {}
        for _, _, r in diagnoses:
            bucket = r.split(" (")[0] if "(" in r else r.split(" ")[0]
            reasons[bucket] = reasons.get(bucket, 0) + 1
        console.print(f"  [dim]Summary: {dict(reasons)}[/dim]")

    # Step 5: LP lower bound
    console.print("[dim]Computing LP relaxation lower bound...[/dim]")
    lp_result = compute_lp_bound(static, parsed.required_station_ids, start_station)
    console.print(f"  Static LP bound: {lp_result['lp_bound_seconds']}s ({lp_result['status']})")

    teg_lp_result = None
    if compute_teg_lp:
        console.print("[dim]Computing time-expanded LP bound (may take a while)...[/dim]")
        teg_lp_result = compute_lp_bound_time_expanded(
            teg, parsed.required_station_ids, start_station, start_time
        )
        console.print(f"  TEG LP bound: {teg_lp_result['lp_bound_seconds']}s ({teg_lp_result['status']})")

    # Compute optimality gap
    lp_bound = lp_result["lp_bound_seconds"]
    if teg_lp_result and teg_lp_result["lp_bound_seconds"] is not None:
        lp_bound = max(lp_bound or 0, teg_lp_result["lp_bound_seconds"])

    gap = compute_optimality_gap(best_time, lp_bound) if lp_bound and lp_bound > 0 else None

    if gap is not None:
        console.print(f"\n[bold]Optimality gap: {gap:.1f}%[/bold]")
    console.print(
        f"[bold]Best time: {best_time // 3600}h{(best_time % 3600) // 60}m "
        f"({best_time}s)[/bold]"
    )

    # Build results
    results = {
        "city": config.city_name,
        "date": str(target_date),
        "total_time_seconds": best_time,
        "total_time_formatted": f"{best_time // 3600}h{(best_time % 3600) // 60}m",
        "stations_visited": best_route.stations_visited,
        "stations_required": len(parsed.required_station_ids),
        "optimality_gap_pct": round(gap, 2) if gap is not None else None,
        "lp_lower_bound_seconds": lp_bound,
        "lp_static": lp_result,
        "lp_time_expanded": teg_lp_result,
        "stations": {
            sid: {"name": s.name, "lat": s.lat, "lon": s.lon}
            for sid, s in parsed.stations.items()
        },
        "route": best_route.visits,
        "walk_segments": best_route.walk_segments,
        "graph_stats": {
            "teg_nodes": teg.node_count,
            "teg_edges": teg.edge_count,
            "static_stations": static.station_count,
            "static_edges": static.edge_count,
        },
        "solver_params": {
            "start_station": start_station,
            "start_time": start_time,
            "lookahead": lookahead,
            "local_search_iterations": local_search_iterations,
            "movement_mode": config.movement_mode,
            "running_speed_kmh": config.running_speed_kmh if config.movement_mode == "run" else None,
            "max_walk_distance_m": config.max_walk_distance_m,
        },
    }

    # Write output
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        console.print(f"\n[green]Results written to {output_path}[/green]")

    return results
