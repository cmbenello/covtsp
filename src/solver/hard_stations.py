"""Auto-detect and optimally schedule hard-to-visit stations.

Hard stations are those with limited service (few TEG nodes), stub topology
(dead-end branches requiring backtracking), or narrow service windows.
Examples: Kensington Olympia (9 trains/day), Mill Hill East (early deadline),
Heathrow Terminal 4 (separate loop off main line).

This module:
1. Detects hard stations from TEG properties (no manual configuration needed)
2. Computes optimal visit windows (cheapest time to detour)
3. Builds a skeleton schedule of hard station visits at optimal times
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx

from src.graph.time_expanded import TENode, TimeExpandedGraph


@dataclass
class HardStationProfile:
    """Profile of a hard-to-visit station."""

    station_id: str
    station_name: str

    # Detection metrics
    teg_node_count: int  # Number of (station, time) nodes in TEG
    service_windows: list[tuple[int, int]] = field(default_factory=list)  # clustered service bands
    total_service_span_s: int = 0  # last departure - first departure
    is_stub: bool = False  # degree-1 in collapsed transit graph
    branch_depth: int = 0  # hops from nearest junction (0 = not stub)
    junction_id: str | None = None  # nearest junction station ID

    # Scoring
    hardness_score: float = 0.0

    # Scheduling (populated by VisitWindowOptimizer)
    optimal_windows: list[tuple[int, int, float]] = field(default_factory=list)


@dataclass
class SkeletonWaypoint:
    """A scheduled visit to a hard station."""

    station_id: str
    station_name: str
    target_time: int  # ideal arrival (seconds since midnight)
    window_start: int  # earliest acceptable arrival
    window_end: int  # latest acceptable arrival
    priority: float  # higher = more important to hit this window
    approach_station: str | None = None  # junction to approach from


class HardStationDetector:
    """Detect hard stations from TEG properties."""

    def __init__(self, teg: TimeExpandedGraph):
        self.teg = teg

    def detect(
        self,
        required_stations: set[str],
        hardness_threshold: float | None = None,
        force_hard: set[str] | None = None,
        exclude: set[str] | None = None,
    ) -> list[HardStationProfile]:
        """Auto-detect hard stations from TEG properties.

        Args:
            required_stations: Set of station IDs to consider.
            hardness_threshold: Score threshold (None = auto 90th percentile).
            force_hard: Station IDs to always include as hard.
            exclude: Station IDs to never include.

        Returns:
            List of HardStationProfile sorted by hardness_score (hardest first).
        """
        force_hard = force_hard or set()
        exclude = exclude or set()

        # Build collapsed transit-only graph for topology analysis
        stub_info = self._compute_stub_info(required_stations)

        profiles: list[HardStationProfile] = []
        for sid in required_stations:
            if sid in exclude:
                continue

            nodes = self.teg._station_nodes.get(sid, [])
            node_count = len(nodes)
            if node_count == 0:
                continue

            name = self.teg._station_names.get(sid, sid)
            is_stub, branch_depth, junction_id = stub_info.get(sid, (False, 0, None))

            # Service window clustering
            windows = self._find_service_windows(sid)
            first_t = nodes[0][1]
            last_t = nodes[-1][1]
            span = last_t - first_t

            # Hardness scoring — designed to be SELECTIVE.
            # Only stations that are genuinely problematic should score high.
            # A station with 400+ nodes running all day is NOT hard, even if
            # it's a stub terminus.

            # Factor 1: TEG sparsity (DOMINANT factor — fewer nodes = MUCH harder)
            # Stations with <50 nodes are genuinely hard; >200 are fine
            if node_count < 20:
                sparsity = 3.0
            elif node_count < 50:
                sparsity = 2.0
            elif node_count < 100:
                sparsity = 1.0
            elif node_count < 200:
                sparsity = 0.3
            else:
                sparsity = 0.1  # well-served stations

            # Factor 2: Branch depth (minor — only matters when combined with sparse service)
            depth_penalty = 1.0 + 0.1 * branch_depth

            # Factor 3: Narrow service window (matters for stations with restricted hours)
            day_fraction = span / 68400.0 if span > 0 else 0.01  # 19h operating day
            if day_fraction < 0.3:
                window_penalty = 2.0  # very narrow (< ~6h service)
            elif day_fraction < 0.6:
                window_penalty = 1.3
            else:
                window_penalty = 1.0  # full-day service

            # Factor 4: Disconnected service (e.g., morning + evening only)
            if len(windows) <= 2 and node_count < 50:
                window_penalty *= 1.5

            score = sparsity * depth_penalty * window_penalty

            profile = HardStationProfile(
                station_id=sid,
                station_name=name,
                teg_node_count=node_count,
                service_windows=windows,
                total_service_span_s=span,
                is_stub=is_stub,
                branch_depth=branch_depth,
                junction_id=junction_id,
                hardness_score=score,
            )
            profiles.append(profile)

        # Determine threshold
        # Default: score >= 0.5 (catches stations with genuinely limited service).
        # Stations with 400+ nodes and all-day service score ~0.1-0.2 and are excluded.
        if hardness_threshold is None:
            hardness_threshold = 0.5

        # Filter to hard stations
        result = [
            p for p in profiles
            if p.hardness_score >= hardness_threshold or p.station_id in force_hard
        ]
        result.sort(key=lambda p: -p.hardness_score)
        return result

    def _find_service_windows(
        self, station_id: str, gap_threshold: int = 3600
    ) -> list[tuple[int, int]]:
        """Cluster TEG node times into service windows.

        A gap > gap_threshold seconds between consecutive nodes starts a new window.
        Returns list of (window_start, window_end) tuples.
        """
        nodes = self.teg._station_nodes.get(station_id, [])
        if not nodes:
            return []

        times = [n[1] for n in nodes]
        windows: list[tuple[int, int]] = []
        w_start = times[0]
        w_end = times[0]

        for t in times[1:]:
            if t - w_end > gap_threshold:
                windows.append((w_start, w_end))
                w_start = t
            w_end = t

        windows.append((w_start, w_end))
        return windows

    def _compute_stub_info(
        self, required_stations: set[str]
    ) -> dict[str, tuple[bool, int, str | None]]:
        """Compute stub topology info for all stations.

        Returns:
            {station_id: (is_stub, branch_depth, junction_id)}
            where junction_id is the nearest station with degree >= 3.
        """
        # Build collapsed transit-only graph (ignore walks and waits)
        transit_graph = nx.Graph()
        for u, v, data in self.teg.graph.edges(data=True):
            if data.get("edge_type") == "transit":
                su, sv = u[0], v[0]
                if su != sv and su in required_stations and sv in required_stations:
                    transit_graph.add_edge(su, sv)

        result: dict[str, tuple[bool, int, str | None]] = {}

        for sid in required_stations:
            if sid not in transit_graph:
                result[sid] = (True, 1, None)
                continue

            degree = transit_graph.degree(sid)
            if degree > 1:
                result[sid] = (False, 0, None)
                continue

            # degree == 1: stub station. Walk up the branch to find junction.
            is_stub = True
            depth = 0
            current = sid
            visited = {sid}

            while True:
                depth += 1
                neighbors = [n for n in transit_graph.neighbors(current) if n not in visited]
                if not neighbors:
                    # Dead end — no junction found
                    result[sid] = (is_stub, depth, current)
                    break
                next_node = neighbors[0]
                visited.add(next_node)
                if transit_graph.degree(next_node) >= 3:
                    # Found junction
                    result[sid] = (is_stub, depth, next_node)
                    break
                current = next_node

        return result


@dataclass
class HardStationPairing:
    """A hard station paired with its nearest junction.

    When the solver arrives at the junction, it should grab the hard station
    as a side-trip. The round_trip_cost is the expected detour penalty.
    """

    hard_station_id: str
    hard_station_name: str
    junction_id: str  # paired "grab from" station
    junction_name: str
    round_trip_cost_s: float  # static round-trip seconds
    teg_node_count: int
    is_prefix: bool = False  # must visit first (ultra-sparse, e.g. K.O.)


def build_pairings(
    teg: TimeExpandedGraph,
    hard_profiles: list[HardStationProfile],
    prefix_node_threshold: int = 20,
) -> list[HardStationPairing]:
    """Build simple hard station → junction pairings.

    For each hard station, pairs it with its nearest junction and computes
    the static round-trip cost. Stations with very few TEG nodes (< threshold)
    are marked as prefix (must visit first thing).

    Returns sorted by round_trip_cost (cheapest first).
    """
    pairings: list[HardStationPairing] = []

    for profile in hard_profiles:
        junction = profile.junction_id
        if not junction:
            continue

        # Compute round-trip static cost
        to_cost = teg.static_dist(junction, profile.station_id)
        from_cost = teg.static_dist(profile.station_id, junction)
        if to_cost == float("inf") or from_cost == float("inf"):
            rt_cost = 1800.0  # 30 min fallback
        else:
            rt_cost = to_cost + from_cost

        pairings.append(HardStationPairing(
            hard_station_id=profile.station_id,
            hard_station_name=profile.station_name,
            junction_id=junction,
            junction_name=teg._station_names.get(junction, junction),
            round_trip_cost_s=rt_cost,
            teg_node_count=profile.teg_node_count,
            is_prefix=profile.teg_node_count < prefix_node_threshold,
        ))

    pairings.sort(key=lambda p: p.round_trip_cost_s)
    return pairings


class VisitWindowOptimizer:
    """Compute optimal visit windows for hard stations.

    For each hard station, determines the cheapest time to visit by
    computing detour costs from the nearest junction at different times.
    """

    def __init__(self, teg: TimeExpandedGraph):
        self.teg = teg

    def compute_optimal_windows(
        self,
        profile: HardStationProfile,
        sample_interval_s: int = 1800,
    ) -> list[tuple[int, int, float]]:
        """Compute cheapest-detour visit windows for a hard station.

        For each service window, samples detour costs (round-trip from junction)
        and returns windows ranked by cost (cheapest first).

        Returns:
            [(window_start, window_end, avg_round_trip_seconds), ...]
        """
        if not profile.service_windows:
            return []

        junction = profile.junction_id
        results: list[tuple[int, int, float]] = []

        for w_start, w_end in profile.service_windows:
            costs: list[float] = []

            # Sample times within this service window
            t = w_start
            while t <= w_end:
                cost = self._compute_round_trip_cost(
                    profile.station_id, junction, t
                )
                if cost < float("inf"):
                    costs.append(cost)
                t += sample_interval_s

            if costs:
                avg_cost = sum(costs) / len(costs)
                results.append((w_start, w_end, avg_cost))

        results.sort(key=lambda x: x[2])
        return results

    def _compute_round_trip_cost(
        self, station_id: str, junction_id: str | None, target_time: int
    ) -> float:
        """Compute round-trip cost: get to station near target_time and back.

        Uses static distance as a fast approximation rather than full Dijkstra.
        """
        if junction_id is None:
            # No junction — use static distance from any neighbor
            return self.teg.static_dist(station_id, station_id) or 600.0

        # Round trip: junction -> station + station -> junction
        to_cost = self.teg.static_dist(junction_id, station_id)
        from_cost = self.teg.static_dist(station_id, junction_id)

        if to_cost == float("inf") or from_cost == float("inf"):
            return float("inf")

        return to_cost + from_cost


class SkeletonScheduler:
    """Build a time-ordered skeleton of hard station visits."""

    def __init__(self, teg: TimeExpandedGraph):
        self.teg = teg

    def build_skeleton(
        self,
        hard_profiles: list[HardStationProfile],
        start_time: int,
    ) -> list[SkeletonWaypoint]:
        """Build a time-ordered skeleton schedule for hard station visits.

        Algorithm:
        1. For each hard station, pick the cheapest visit window
        2. Sort by window midpoint
        3. Check for time conflicts (overlapping required visit times)
        4. Resolve conflicts by trying next-cheapest window
        5. Return sorted waypoints

        Args:
            hard_profiles: Detected hard stations with optimal_windows populated.
            start_time: Solver start time (seconds since midnight).
        """
        if not hard_profiles:
            return []

        # Assign each hard station to its best window.
        # Skip stations with long service windows — let NN + urgency handle those.
        # Skeleton waypoints ONLY for truly time-constrained stations where
        # missing the window means a huge penalty (e.g., K.O.'s 1.5h morning shuttle).
        NARROW_WINDOW_THRESHOLD = 4 * 3600  # 4 hours — only genuinely narrow windows

        assignments: list[tuple[HardStationProfile, int, int, float]] = []
        for profile in hard_profiles:
            # Check if any individual service window is narrow enough to warrant scheduling
            has_narrow_window = any(
                (w_end - w_start) < NARROW_WINDOW_THRESHOLD
                for w_start, w_end in profile.service_windows
            )
            if not has_narrow_window and profile.total_service_span_s > NARROW_WINDOW_THRESHOLD:
                # All-day service — NN + urgency will find it naturally
                continue

            if not profile.optimal_windows:
                if profile.service_windows:
                    w_start, w_end = profile.service_windows[0]
                    assignments.append((profile, w_start, w_end, 600.0))
                continue

            # Pick cheapest narrow window that's reachable from start_time
            for w_start, w_end, cost in profile.optimal_windows:
                if w_end >= start_time and (w_end - w_start) < NARROW_WINDOW_THRESHOLD:
                    assignments.append((profile, w_start, w_end, cost))
                    break
            else:
                # No narrow window — try first reachable window
                for w_start, w_end, cost in profile.optimal_windows:
                    if w_end >= start_time:
                        assignments.append((profile, w_start, w_end, cost))
                        break

        # Sort by window start time
        assignments.sort(key=lambda x: x[1])

        # Build waypoints
        waypoints: list[SkeletonWaypoint] = []
        for profile, w_start, w_end, cost in assignments:
            # Target: midpoint of window (gives maximum flexibility)
            target = (w_start + w_end) // 2
            # Priority: harder stations + more expensive detours get higher priority
            priority = profile.hardness_score * (1.0 + cost / 3600.0)

            waypoints.append(SkeletonWaypoint(
                station_id=profile.station_id,
                station_name=profile.station_name,
                target_time=target,
                window_start=w_start,
                window_end=w_end,
                priority=priority,
                approach_station=profile.junction_id,
            ))

        # Sort by target time
        waypoints.sort(key=lambda w: w.target_time)

        return waypoints
