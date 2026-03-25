"""One-time script to compute all-pairs walking distances using Google Routes API.

Computes walking time and distance between pairs of stations and stores
results in the walking cache for future solver runs.

Usage:
    # All pairs within 2km
    python scripts/compute_all_walking.py --config configs/london.yaml --max-distance 2000

    # Only branch terminus stations (last N stops of each line), different lines, within 10km
    python scripts/compute_all_walking.py --config configs/london.yaml \\
        --max-distance 10000 --branch-terminus 3 --prune-vs-train

Filtering strategy:
  --branch-terminus N   Only compute pairs where BOTH stations are within the first/last N
                        stops of some line, AND they are on different lines. This targets
                        the only cases where running between branches is useful.

  --prune-vs-train      Eliminate any pair where the Haversine crow-flies running time is
                        >= the shortest train path time (ignoring headway waits).
                        If running can't beat the train even at best-case (straight line,
                        sprint pace, no wait on train), it's never worth computing.

272 stations = 36,856 unordered pairs = 73,712 directed pairs.
Routes API allows up to 625 elements per request (25 origins × 25 destinations).
"""

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import click
import networkx as nx
import numpy as np
import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.gtfs.parser import GTFSParser, Station, TripSegment, _haversine_meters
from src.gtfs.walking import load_walking_cache, save_walking_cache

console = Console()

ROUTES_API_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
MAX_ORIGINS = 25
MAX_DESTINATIONS = 25
# Max elements per request is 625 (25 * 25)


def _make_waypoint(lat: float, lon: float) -> dict:
    return {
        "waypoint": {
            "location": {
                "latLng": {"latitude": lat, "longitude": lon}
            }
        }
    }


def _batch_routes_api(
    origins: list[tuple[str, float, float]],
    destinations: list[tuple[str, float, float]],
    api_key: str,
) -> dict[str, dict]:
    """Call Routes API computeRouteMatrix for a batch of origins × destinations.

    Returns dict of "from_id|to_id" -> {"walk_time_seconds": int, "distance_meters": int}
    """
    body = {
        "origins": [_make_waypoint(lat, lon) for _, lat, lon in origins],
        "destinations": [_make_waypoint(lat, lon) for _, lat, lon in destinations],
        "travelMode": "WALK",
    }

    resp = requests.post(
        ROUTES_API_URL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,duration,distanceMeters,status,condition",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = {}
    for elem in data:
        if elem.get("condition") != "ROUTE_EXISTS":
            continue
        oi = elem["originIndex"]
        di = elem["destinationIndex"]
        from_id = origins[oi][0]
        to_id = destinations[di][0]

        # Skip self-pairs
        if from_id == to_id:
            continue

        duration_str = elem.get("duration", "0s")
        seconds = int(duration_str.rstrip("s"))
        distance = elem.get("distanceMeters", 0)

        results[f"{from_id}|{to_id}"] = {
            "walk_time_seconds": seconds,
            "distance_meters": distance,
        }

    return results


def _chunk_list(lst, n):
    """Split list into chunks of size n."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def find_terminus_stations(
    segments: list[TripSegment], n: int
) -> tuple[set[str], dict[str, set[str]]]:
    """Find stations appearing in the first/last N stops of any line's trips.

    Returns:
        terminus_stations: set of station_ids that are near a line terminus
        station_to_routes: dict mapping station_id -> set of route_ids it's terminus for
    """
    # Group segments by trip_id, sort by departure time to reconstruct order
    trip_segs: dict[str, list[TripSegment]] = defaultdict(list)
    for seg in segments:
        trip_segs[seg.trip_id].append(seg)

    terminus_stations: set[str] = set()
    station_to_routes: dict[str, set[str]] = defaultdict(set)

    # Deduplicate: only process unique (route_id, first_station, last_station) sequences
    seen_sequences: set[tuple[str, str, str]] = set()

    for trip_id, segs in trip_segs.items():
        segs_sorted = sorted(segs, key=lambda s: s.departure_time)

        # Reconstruct ordered station list: [from_0, to_0, to_1, ..., to_last]
        ordered: list[str] = [segs_sorted[0].from_station_id]
        for s in segs_sorted:
            if not ordered or ordered[-1] != s.from_station_id:
                ordered.append(s.from_station_id)
            ordered.append(s.to_station_id)

        # Deduplicate consecutive repeats (station loops)
        deduped: list[str] = []
        for sid in ordered:
            if not deduped or deduped[-1] != sid:
                deduped.append(sid)

        route_id = segs_sorted[0].route_id
        seq_key = (route_id, deduped[0], deduped[-1])
        if seq_key in seen_sequences:
            continue
        seen_sequences.add(seq_key)

        # Mark first N and last N stations as terminus candidates
        for sid in deduped[:n]:
            terminus_stations.add(sid)
            station_to_routes[sid].add(route_id)
        for sid in deduped[-n:]:
            terminus_stations.add(sid)
            station_to_routes[sid].add(route_id)

    return terminus_stations, dict(station_to_routes)


def build_train_graph(segments: list[TripSegment]) -> nx.DiGraph:
    """Build a directed graph with minimum train travel times (no wait times).

    Edge weights are the minimum observed travel time in seconds.
    This is an optimistic lower bound on actual travel time.
    """
    G: nx.DiGraph = nx.DiGraph()
    for seg in segments:
        u, v = seg.from_station_id, seg.to_station_id
        t = seg.arrival_time - seg.departure_time
        if t <= 0:
            continue
        if not G.has_edge(u, v) or G[u][v]["weight"] > t:
            G.add_edge(u, v, weight=t)
    return G


@click.command()
@click.option("--config", "-c", required=True, help="Path to city config YAML")
@click.option("--dry-run", is_flag=True, help="Show what would be computed without calling API")
@click.option(
    "--max-distance", default=None, type=float,
    help="Only compute pairs within this Haversine distance (meters). Default: all pairs."
)
@click.option(
    "--branch-terminus", default=None, type=int,
    help=(
        "Only compute pairs where BOTH stations are within the first/last N stops of "
        "a line, AND they are on different lines. E.g. --branch-terminus 3"
    )
)
@click.option(
    "--prune-vs-train", is_flag=True,
    help=(
        "Eliminate pairs where Haversine crow-flies running time >= shortest train path "
        "time (ignoring headway waits). Running can never beat the train for these pairs."
    )
)
def main(config, dry_run, max_distance, branch_terminus, prune_vs_train):
    """Compute all-pairs walking distances via Google Routes API."""
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key and not dry_run:
        console.print("[red]GOOGLE_MAPS_API_KEY not set[/red]")
        return

    cfg = load_config(config)
    cache_path = cfg.data_dir / "walking_cache.json"

    # Parse stations + segments (disable google walking to avoid recursion)
    console.print(f"[bold]Loading GTFS data for {cfg.city_name}...[/bold]")
    orig_google = cfg.use_google_walking
    cfg.use_google_walking = False
    parser = GTFSParser(cfg)
    parsed = parser.parse()
    cfg.use_google_walking = orig_google
    stations = list(parsed.stations.values())
    segments = parsed.segments
    console.print(f"  Found {len(stations)} stations, {len(segments)} trip segments")

    # Load existing cache
    cache = load_walking_cache(cache_path)
    console.print(f"  Existing cache: {len(cache)} entries")

    # ── Step 1: Distance filter ──────────────────────────────────────────────
    if max_distance:
        from scipy.spatial import KDTree
        coords = np.array([[s.lat, s.lon] for s in stations])
        tree = KDTree(coords)
        max_deg = max_distance / 111_000
        pairs_idx = tree.query_pairs(max_deg)

        station_pairs: list[tuple[Station, Station]] = []
        for i, j in pairs_idx:
            s1, s2 = stations[i], stations[j]
            dist = _haversine_meters(s1.lat, s1.lon, s2.lat, s2.lon)
            if dist <= max_distance:
                station_pairs.append((s1, s2))
    else:
        station_pairs = [
            (stations[i], stations[j])
            for i in range(len(stations))
            for j in range(i + 1, len(stations))
        ]

    console.print(f"\n[bold]Filtering candidate pairs...[/bold]")
    console.print(f"  After distance filter ({max_distance or 'all'}m): {len(station_pairs)} pairs")

    # ── Step 2: Branch terminus filter ──────────────────────────────────────
    if branch_terminus:
        terminus_stations, station_to_routes = find_terminus_stations(segments, branch_terminus)
        console.print(
            f"  Branch terminus stations (last/first {branch_terminus} stops): "
            f"{len(terminus_stations)}"
        )

        filtered: list[tuple[Station, Station]] = []
        for s1, s2 in station_pairs:
            # At least ONE must be a terminus candidate — you run FROM a branch end
            # to anywhere on a different line, not necessarily another terminus
            s1_is_terminus = s1.station_id in terminus_stations
            s2_is_terminus = s2.station_id in terminus_stations
            if not s1_is_terminus and not s2_is_terminus:
                continue
            # Must be on different lines (no point running within the same branch)
            routes_1 = station_to_routes.get(s1.station_id, set())
            routes_2 = station_to_routes.get(s2.station_id, set())
            if routes_1.isdisjoint(routes_2):
                filtered.append((s1, s2))

        console.print(f"  After branch terminus filter: {len(filtered)} pairs")
        station_pairs = filtered

    # ── Step 3: Train-vs-crow-flies prune ───────────────────────────────────
    if prune_vs_train:
        console.print(f"  Building train graph for shortest-path pruning...")
        train_graph = build_train_graph(segments)

        # Sprint speed = best-case running (1.2x base, no routing overhead)
        sprint_speed_ms = cfg.running_speed_kmh * 1.2 * 1000 / 3600

        pruned: list[tuple[Station, Station]] = []
        eliminated = 0
        no_train_path = 0

        for s1, s2 in station_pairs:
            dist = _haversine_meters(s1.lat, s1.lon, s2.lat, s2.lon)
            crow_flies_run_time = dist / sprint_speed_ms  # seconds, best-case

            # Shortest train path (no headway waits = optimistic lower bound)
            try:
                train_time = nx.shortest_path_length(
                    train_graph, s1.station_id, s2.station_id, weight="weight"
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                # No train path → running might be the only option; keep it
                no_train_path += 1
                pruned.append((s1, s2))
                continue

            if crow_flies_run_time >= train_time:
                # Even crow-flies running at sprint pace is slower than train travel time
                # (ignoring waits). Running can't possibly win.
                eliminated += 1
            else:
                pruned.append((s1, s2))

        console.print(
            f"  After train-vs-crow-flies prune: {len(pruned)} pairs "
            f"({eliminated} eliminated, {no_train_path} kept due to no train path)"
        )
        station_pairs = pruned

    # ── Step 4: Remove already-cached pairs ─────────────────────────────────
    uncached_pairs: list[tuple[Station, Station]] = []
    for s1, s2 in station_pairs:
        fwd = f"{s1.station_id}|{s2.station_id}"
        rev = f"{s2.station_id}|{s1.station_id}"
        if fwd not in cache or rev not in cache:
            uncached_pairs.append((s1, s2))

    total_pairs = len(station_pairs)
    total_uncached = len(uncached_pairs)

    console.print(f"\n  Total candidate pairs: {total_pairs}")
    console.print(f"  Already cached: {total_pairs - total_uncached}")
    console.print(f"  Need to compute: {total_uncached}")

    if total_uncached == 0:
        console.print("[green]All pairs already cached![/green]")
        return

    # ── Step 5: Build API batches ────────────────────────────────────────────
    by_origin: dict[str, list[tuple[str, float, float]]] = {}
    station_lookup = {s.station_id: s for s in stations}
    for s1, s2 in uncached_pairs:
        by_origin.setdefault(s1.station_id, []).append((s2.station_id, s2.lat, s2.lon))
        by_origin.setdefault(s2.station_id, []).append((s1.station_id, s1.lat, s1.lon))

    batches = []
    for origin_id, dests in by_origin.items():
        seen_dests: set[str] = set()
        unique_dests: list[tuple[str, float, float]] = []
        for d in dests:
            if d[0] not in seen_dests:
                seen_dests.add(d[0])
                unique_dests.append(d)

        s = station_lookup[origin_id]
        origin_tuple = (origin_id, s.lat, s.lon)
        for chunk in _chunk_list(unique_dests, MAX_DESTINATIONS):
            batches.append(([origin_tuple], chunk))

    total_requests = len(batches)
    total_elements = sum(len(origins) * len(dests) for origins, dests in batches)

    console.print(f"\n  API requests needed: {total_requests}")
    console.print(f"  Total elements: {total_elements}")
    console.print(f"  Estimated cost: ${total_elements * 0.005:.2f}")

    if dry_run:
        console.print("\n[yellow]Dry run — not calling API[/yellow]")
        return

    # ── Step 6: Execute API calls ────────────────────────────────────────────
    new_entries = 0
    errors = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Computing walking distances...", total=total_requests)

        for origins, dests in batches:
            try:
                results = _batch_routes_api(origins, dests, api_key)
                cache.update(results)
                new_entries += len(results)
            except requests.HTTPError as e:
                console.print(f"  [red]API error: {e}[/red]")
                errors += 1
                if errors > 5:
                    console.print("[red]Too many errors, stopping. Saving progress...[/red]")
                    save_walking_cache(cache_path, cache)
                    return
            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")
                errors += 1

            progress.advance(task)
            time.sleep(0.1)

            # Save progress every 50 requests
            if new_entries > 0 and new_entries % 500 < len(results if results else []):
                save_walking_cache(cache_path, cache)

    # Save final cache
    save_walking_cache(cache_path, cache)
    console.print(
        f"\n[green]Done! Added {new_entries} new entries. Total cache: {len(cache)} entries.[/green]"
    )
    console.print(f"[green]Saved to {cache_path}[/green]")
    if errors:
        console.print(f"[yellow]{errors} API errors encountered[/yellow]")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
