"""Greedy nearest-unvisited solver with lookahead beam search."""

import itertools
import random
from dataclasses import dataclass

from src.graph.time_expanded import Route, TENode, TimeExpandedGraph, _format_time


class GreedySolver:
    """Greedy solver for the Covering TSP on a time-expanded graph.

    At each step, evaluates the best sequence of the next `lookahead` stations
    to visit via beam search, choosing the move that minimizes cumulative time.

    With line_aware=True, applies a bonus to continuing along the same line,
    which prevents premature line-switching that causes backtracking.
    """

    def __init__(self, graph: TimeExpandedGraph, lookahead: int = 3, line_aware: bool = False):
        self.graph = graph
        self.lookahead = lookahead
        self.line_aware = line_aware
        self._current_line: str | None = None
        self._deadlines: dict[str, int] = {}  # station_id -> latest TEG time

    def set_deadlines(self):
        """Compute service deadlines from TEG node data."""
        for sid, nodes in self.graph._station_nodes.items():
            if nodes:
                self._deadlines[sid] = nodes[-1][1]

    def solve(self, start_station: str, required_stations: set[str], start_time: int = 0) -> Route:
        """Find a route visiting all required stations.

        If deadlines are set, stations with early deadlines (limited service)
        are visited first in a forced phase before the normal greedy phase.

        Args:
            start_station: Station ID to start from.
            required_stations: Set of station IDs that must be visited.
            start_time: Earliest departure time (seconds since midnight).

        Returns:
            Route object with visit details and total time.
        """
        # Find the earliest start node
        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            raise ValueError(f"No departures from {start_station} after {_format_time(start_time)}")

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)

        full_path: list[TENode] = [current_node]
        visited_order: list[str] = [start_station]

        self._current_line = None

        # Main greedy phase
        while unvisited:
            next_station, next_node, path_segment = self._best_next_move(
                current_node, unvisited
            )

            if next_node is None:
                break

            if self.line_aware and len(path_segment) >= 2:
                edge_data = self.graph.graph.get_edge_data(
                    path_segment[-2], path_segment[-1]
                )
                if edge_data:
                    self._current_line = edge_data.get("route_name")

            full_path.extend(path_segment[1:])
            current_node = next_node
            unvisited.discard(next_station)
            visited_order.append(next_station)

            for node in path_segment[1:]:
                sid = node[0]
                if sid in unvisited:
                    unvisited.discard(sid)
                    visited_order.append(sid)

        return self.graph.reconstruct_route(full_path, None)

    def _best_next_move(
        self, current: TENode, unvisited: set[str]
    ) -> tuple[str | None, TENode | None, list[TENode]]:
        """Evaluate the best next station to visit using lookahead.

        With lookahead=1, this is pure nearest-neighbor.
        With lookahead=k, evaluates permutations of the nearest k candidates
        using static-graph distances for the inner hops (fast approximation).

        Returns:
            (next_station_id, arrival_node, path_to_next) or (None, None, []) if stuck.
        """
        # Single Dijkstra gives us travel times AND paths to all unvisited stations
        arrivals = self.graph.earliest_arrivals_from(current, unvisited)

        if not arrivals:
            return None, None, []

        # Score function: lower is better. Considers travel time and line bonus.
        def score(sid: str) -> float:
            arr_node, travel_time, path = arrivals[sid]
            t = travel_time
            if self.line_aware and self._current_line and path and len(path) >= 2:
                edge_data = self.graph.graph.get_edge_data(path[-2], path[-1])
                if edge_data and edge_data.get("route_name") == self._current_line:
                    t *= 0.7  # 30% bonus for staying on the same line
            return t

        # Pure nearest neighbor — path already in hand, no second Dijkstra
        best_sid = min(arrivals, key=score)

        if self.lookahead <= 1 or len(arrivals) <= 1:
            arr_node, _, path = arrivals[best_sid]
            return best_sid, arr_node, path

        # Beam search: rank candidate sequences using static distances for inner hops.
        candidates = sorted(arrivals.keys(), key=score)
        candidates = candidates[: self.lookahead * 2]

        k = min(self.lookahead, len(candidates))
        best_first = None
        best_total_time = float("inf")

        for perm in itertools.permutations(candidates, k):
            # First hop: exact time from TEG Dijkstra
            first_arr_node, first_time, _ = arrivals[perm[0]]
            total_time = first_arr_node[1] - current[1]

            # Apply line bonus for first hop
            if self.line_aware and self._current_line:
                first_path = arrivals[perm[0]][2]
                if first_path and len(first_path) >= 2:
                    edge_data = self.graph.graph.get_edge_data(
                        first_path[-2], first_path[-1]
                    )
                    if edge_data and edge_data.get("route_name") == self._current_line:
                        total_time *= 0.7

            # Subsequent hops: approximate via static graph
            sim_station = perm[0]
            valid = True
            for sid in perm[1:]:
                static_dists = self.graph.static_distances_from(sim_station)
                if sid not in static_dists:
                    valid = False
                    break
                total_time += static_dists[sid]
                sim_station = sid

            if valid and total_time < best_total_time:
                best_total_time = total_time
                best_first = perm[0]

        if best_first is None:
            best_first = best_sid

        arr_node, _, path = arrivals[best_first]
        return best_first, arr_node, path

    def solve_randomized(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0, epsilon: float = 0.3,
        forced_visits: list[tuple[str, int, int]] | None = None,
        seed: int | None = None,
    ) -> Route:
        """Randomized nearest-neighbor: with probability epsilon, pick from
        top-3 nearest instead of always the nearest. Run many times to find
        a better solution than deterministic NN.

        Also supports forced visits for limited-service stations.
        """
        rng = random.Random(seed)

        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)

        full_path: list[TENode] = [current_node]
        forced = list(forced_visits or [])

        while unvisited:
            current_time = current_node[1]

            # Check forced visits — only trigger when within 10 min of window
            force_target = None
            for f_sid, f_earliest, f_latest in forced:
                if f_sid not in unvisited:
                    continue
                if f_earliest - 600 <= current_time <= f_latest:
                    force_target = f_sid
                    break

            if force_target:
                arr_node, travel_time, path = self.graph.earliest_arrival(
                    current_node, force_target
                )
                if arr_node is not None:
                    full_path.extend(path[1:])
                    current_node = arr_node
                    unvisited.discard(force_target)
                    for p_node in path[1:]:
                        if p_node[0] in unvisited:
                            unvisited.discard(p_node[0])
                    continue

            # Get k nearest, then pick with randomization
            k = 3
            candidates = self.graph.earliest_arrival_k_nearest(
                current_node, unvisited, k=k
            )
            if not candidates:
                break

            if len(candidates) == 1 or rng.random() > epsilon:
                # Pick the nearest (greedy)
                sid, node, travel_time, path = candidates[0]
            else:
                # Pick randomly from top candidates (exploration)
                sid, node, travel_time, path = rng.choice(candidates[1:] if len(candidates) > 1 else candidates)

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)
            for p_node in path[1:]:
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        return self.graph.reconstruct_route(full_path, None)

    def solve_fast_with_forced(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0, k_nearest: int = 1,
        forced_visits: list[tuple[str, int, int]] | None = None,
    ) -> Route:
        """Fast greedy with forced detours for limited-service stations.

        forced_visits: list of (station_id, earliest_time, latest_time).
        When current_time enters a forced station's window and it's still
        unvisited, the solver detours there immediately.
        """
        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)

        full_path: list[TENode] = [current_node]
        forced = list(forced_visits or [])

        while unvisited:
            current_time = current_node[1]

            # Check if any forced station's window is now or approaching
            force_target = None
            for f_sid, f_earliest, f_latest in forced:
                if f_sid not in unvisited:
                    continue
                # If we're within 45 min before the window or inside it, detour now
                if f_earliest - 600 <= current_time <= f_latest:
                    force_target = f_sid
                    break

            if force_target:
                arr_node, travel_time, path = self.graph.earliest_arrival(
                    current_node, force_target
                )
                if arr_node is not None:
                    full_path.extend(path[1:])
                    current_node = arr_node
                    unvisited.discard(force_target)
                    for p_node in path[1:]:
                        p_sid = p_node[0]
                        if p_sid in unvisited:
                            unvisited.discard(p_sid)
                    continue

            # Normal NN step
            sid, node, travel_time, path = self.graph.earliest_arrival_nearest(
                current_node, unvisited
            )
            if sid is None:
                break

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)
            for p_node in path[1:]:
                p_sid = p_node[0]
                if p_sid in unvisited:
                    unvisited.discard(p_sid)

        return self.graph.reconstruct_route(full_path, None)

    def solve_fast(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0, k_nearest: int = 1
    ) -> Route:
        """Fast greedy using early-termination Dijkstra.

        With k_nearest=1: pure nearest-neighbor (~100x faster than solve()).
        With k_nearest>1: finds k nearest unvisited stations, then picks the
        one that minimizes (travel_time + static_distance_to_next_nearest).
        This is a cheap 2-step lookahead without full beam search.

        Designed for mass multi-start sweeps where speed matters.
        """
        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)

        full_path: list[TENode] = [current_node]

        while unvisited:
            if k_nearest <= 1:
                sid, node, travel_time, path = self.graph.earliest_arrival_nearest(
                    current_node, unvisited
                )
                if sid is None:
                    break
            else:
                candidates = self.graph.earliest_arrival_k_nearest(
                    current_node, unvisited, k=k_nearest
                )
                if not candidates:
                    break

                if len(candidates) == 1:
                    sid, node, travel_time, path = candidates[0]
                else:
                    # 2-step lookahead: pick candidate that minimizes
                    # arrival_time + estimated_time_to_next_nearest
                    best_score = float("inf")
                    sid, node, travel_time, path = candidates[0]

                    for c_sid, c_node, c_time, c_path in candidates:
                        # Estimate: after visiting c_sid, how far to nearest remaining?
                        remaining = unvisited - {c_sid}
                        # Discount pass-through stations
                        for p in c_path[1:]:
                            remaining.discard(p[0])

                        if not remaining:
                            # This candidate finishes the tour!
                            score = c_node[1]  # absolute arrival time
                        else:
                            # Use static distances as estimate
                            min_next = min(
                                (self.graph.static_dist(c_sid, r) for r in remaining),
                                default=0
                            )
                            score = c_node[1] + min_next

                        if score < best_score:
                            best_score = score
                            sid, node, travel_time, path = c_sid, c_node, c_time, c_path

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)

            # Also mark any pass-through stations as visited
            for p_node in path[1:]:
                p_sid = p_node[0]
                if p_sid in unvisited:
                    unvisited.discard(p_sid)

        return self.graph.reconstruct_route(full_path, None)

    def solve_fixed_order(self, station_order: list[str], start_time: int = 0) -> Route:
        """Simulate traversal of a fixed station order through the TEG.

        Unlike solve(), this doesn't choose which station to visit next —
        it follows the given order exactly, using Dijkstra for each hop.

        Args:
            station_order: Ordered list of station IDs to visit.
            start_time: Earliest departure time (seconds since midnight).

        Returns:
            Route with real TEG-based timing, or empty Route if unreachable.
        """
        if not station_order:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        start_nodes = self.graph.get_start_nodes(station_order[0], start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        full_path: list[TENode] = [current_node]

        for i in range(len(station_order) - 1):
            target_sid = station_order[i + 1]
            arr_node, travel_time, path = self.graph.earliest_arrival(
                current_node, target_sid
            )
            if arr_node is None:
                # Can't reach next station — skip it and try the next one
                continue
            full_path.extend(path[1:])
            current_node = arr_node

        return self.graph.reconstruct_route(full_path, None)
