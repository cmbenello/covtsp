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

console = Console()


def backtest(
    config: CityConfig,
    target_date: date,
    output_path: str | Path | None = None,
    lookahead: int = 3,
    local_search_iterations: int = 500,
    compute_teg_lp: bool = False,
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

    # Determine start station
    start_station = config.start_station
    if not start_station:
        # Pick a station that's well-connected (highest degree in static graph)
        start_station = max(
            parsed.required_station_ids,
            key=lambda s: static.graph.degree(s) if s in static.graph else 0,
        )
    start_name = parsed.stations[start_station].name if start_station in parsed.stations else start_station
    console.print(f"  Start station: {start_station} ({start_name})")

    start_time = config.time_window.start_seconds

    # Step 3: Greedy solve
    console.print(f"[dim]Running greedy solver (lookahead={lookahead})...[/dim]")
    solver = GreedySolver(teg, lookahead=lookahead)
    greedy_route = solver.solve(start_station, parsed.required_station_ids, start_time)
    console.print(
        f"  Greedy: {greedy_route.total_time_seconds}s "
        f"({greedy_route.total_time_seconds // 3600}h{(greedy_route.total_time_seconds % 3600) // 60}m) | "
        f"{greedy_route.stations_visited} stations"
    )

    # Step 4: Local search improvement
    console.print(f"[dim]Running local search ({local_search_iterations} iterations)...[/dim]")
    station_order = [v["station_id"] for v in greedy_route.visits if v.get("type") != "wait"]
    # Deduplicate while preserving order
    seen = set()
    unique_order = []
    for sid in station_order:
        if sid not in seen:
            seen.add(sid)
            unique_order.append(sid)

    ls = LocalSearchOptimizer(teg, start_time)
    improved_order, improved_time = ls.improve(
        unique_order, parsed.required_station_ids, max_iterations=local_search_iterations
    )

    if improved_time > 0 and improved_time < greedy_route.total_time_seconds:
        console.print(
            f"  [green]Improved: {improved_time}s "
            f"({improved_time // 3600}h{(improved_time % 3600) // 60}m) | "
            f"Saved {greedy_route.total_time_seconds - improved_time}s[/green]"
        )
        best_time = improved_time
    else:
        console.print("  Local search did not improve greedy solution")
        best_time = greedy_route.total_time_seconds
        improved_order = unique_order

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
        "stations_visited": len(set(improved_order)),
        "stations_required": len(parsed.required_station_ids),
        "optimality_gap_pct": round(gap, 2) if gap is not None else None,
        "lp_lower_bound_seconds": lp_bound,
        "lp_static": lp_result,
        "lp_time_expanded": teg_lp_result,
        "stations": {
            sid: {"name": s.name, "lat": s.lat, "lon": s.lon}
            for sid, s in parsed.stations.items()
        },
        "route": greedy_route.visits,
        "walk_segments": greedy_route.walk_segments,
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
