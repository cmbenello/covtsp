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
        start_time: int = 0, k_nearest: int = 1,
        urgency_weight: float = 0.0,
        forced_visits: list[tuple[str, int, int]] | None = None,
        lookahead_weight: float = 1.0,
    ) -> Route:
        """Fast greedy using early-termination Dijkstra.

        With k_nearest=1: pure nearest-neighbor (~100x faster than solve()).
        With k_nearest>1: finds k nearest, picks best by 2-step lookahead.
        With lookahead_weight<1: reduces the 2-step lookahead penalty,
        making it less aggressive about avoiding branch-end stations.
        With urgency_weight>0: among k nearest, prefer stations whose
        last service time is approaching (prevents missing branch-end stations).
        With forced_visits: detour to specified stations when their time window
        arrives, regardless of k_nearest scoring.

        Designed for mass multi-start sweeps where speed matters.
        """
        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)

        full_path: list[TENode] = [current_node]
        forced = list(forced_visits or [])

        # Pre-compute service deadlines for urgency scoring
        deadlines: dict[str, int] = {}
        if urgency_weight > 0:
            for sid in required_stations:
                nodes = self.graph._station_nodes.get(sid, [])
                if nodes:
                    deadlines[sid] = nodes[-1][1]

        while unvisited:
            current_time = current_node[1]

            # Check forced visits first — detour when within window
            force_target = None
            if forced:
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

            if k_nearest <= 1 and urgency_weight <= 0:
                sid, node, travel_time, path = self.graph.earliest_arrival_nearest(
                    current_node, unvisited
                )
                if sid is None:
                    break
            else:
                k = max(k_nearest, 5 if urgency_weight > 0 else 1)
                candidates = self.graph.earliest_arrival_k_nearest(
                    current_node, unvisited, k=k
                )
                if not candidates:
                    break

                if len(candidates) == 1:
                    sid, node, travel_time, path = candidates[0]
                else:
                    best_score = float("inf")
                    sid, node, travel_time, path = candidates[0]

                    for c_sid, c_node, c_time, c_path in candidates:
                        # Base score: arrival time
                        score = float(c_node[1])

                        # Urgency: bonus for visiting stations close to deadline
                        if urgency_weight > 0 and c_sid in deadlines:
                            time_left = deadlines[c_sid] - current_time
                            if time_left < 7200:  # < 2h left
                                # Strong pull toward stations about to lose service
                                score -= urgency_weight * (7200 - time_left)

                        # 2-step lookahead if k_nearest > 1
                        if k_nearest > 1 and lookahead_weight > 0:
                            remaining = unvisited - {c_sid}
                            for p in c_path[1:]:
                                remaining.discard(p[0])
                            if remaining:
                                min_next = min(
                                    (self.graph.static_dist(c_sid, r) for r in remaining),
                                    default=0
                                )
                                score += lookahead_weight * min_next

                        if score < best_score:
                            best_score = score
                            sid, node, travel_time, path = c_sid, c_node, c_time, c_path

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)

            for p_node in path[1:]:
                p_sid = p_node[0]
                if p_sid in unvisited:
                    unvisited.discard(p_sid)

        return self.graph.reconstruct_route(full_path, None)

    def solve_with_injections(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0, urgency_weight: float = 0.5,
        injections: list[tuple] | None = None,
        prefix_stations: list[str] | None = None,
    ) -> Route:
        """NN solver with urgency scoring and station-triggered side-trip injections.

        At each step, picks from k=5 nearest with urgency scoring (prefers
        stations approaching their service deadline). When the solver arrives
        at a trigger station, it takes a side-trip to the injection target
        if reachable within the time limit.

        This is the best-performing solver variant: urgency scoring improves
        routing efficiency (~17h47m vs 18h45m baseline), and injections catch
        stations with poor connectivity (e.g., T4 via Hatton Cross loop).

        Args:
            start_station: Station ID to start from.
            required_stations: Set of station IDs to visit.
            start_time: Earliest departure time.
            urgency_weight: Weight for deadline urgency (0.5 is optimal for
                London — higher causes T4/K.O. misses, lower gives no benefit).
            injections: List of injection specs. Each is either:
                (target, [triggers], max_time) — fire anytime, or
                (target, [triggers], max_time, earliest, latest) — fire only
                when current_time is in [earliest, latest] window.
            prefix_stations: List of station IDs to visit first (in order)
                before starting the urgency NN. Used to force early visits to
                hard-to-reach stations (e.g., visit K.O. first thing).
        """
        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)
        full_path: list[TENode] = [current_node]

        # Visit prefix stations first (forced order)
        if prefix_stations:
            for prefix_sid in prefix_stations:
                if prefix_sid not in unvisited:
                    continue
                arr_node, travel_time, path = self.graph.earliest_arrival(
                    current_node, prefix_sid
                )
                if arr_node is None:
                    continue
                full_path.extend(path[1:])
                current_node = arr_node
                unvisited.discard(prefix_sid)
                for p_node in path[1:]:
                    if p_node[0] in unvisited:
                        unvisited.discard(p_node[0])

        # Pre-compute deadlines
        deadlines: dict[str, int] = {}
        if urgency_weight > 0:
            for sid in required_stations:
                nodes = self.graph._station_nodes.get(sid, [])
                if nodes:
                    deadlines[sid] = nodes[-1][1]

        # Build injection lookup: trigger_station -> [(target, max_time, earliest, latest)]
        inject_map: dict[str, list[tuple[str, int, int, int]]] = {}
        injected: set[str] = set()
        if injections:
            for inj in injections:
                if len(inj) == 3:
                    target, triggers, max_time = inj
                    earliest, latest = 0, 999999
                else:
                    target, triggers, max_time, earliest, latest = inj
                for trig in triggers:
                    if trig not in inject_map:
                        inject_map[trig] = []
                    inject_map[trig].append((target, max_time, earliest, latest))

        while unvisited:
            current_sid = current_node[0]
            current_time = current_node[1]

            # Check injections
            if current_sid in inject_map:
                for target, max_time, earliest, latest in inject_map[current_sid]:
                    if target in unvisited and target not in injected:
                        if not (earliest <= current_time <= latest):
                            continue
                        arr_node, travel_time, path = self.graph.earliest_arrival(
                            current_node, target
                        )
                        if arr_node is not None and travel_time <= max_time:
                            full_path.extend(path[1:])
                            current_node = arr_node
                            unvisited.discard(target)
                            injected.add(target)
                            for p_node in path[1:]:
                                if p_node[0] in unvisited:
                                    unvisited.discard(p_node[0])
                            break
                else:
                    # No injection fired, fall through to NN
                    pass

                if current_node[0] != current_sid:
                    continue  # injection happened, restart loop

            # k=5 NN with urgency scoring
            candidates = self.graph.earliest_arrival_k_nearest(
                current_node, unvisited, k=5
            )
            if not candidates:
                break

            best_score = float("inf")
            sid, node, travel_time, path = candidates[0]
            for c_sid, c_node, c_time, c_path in candidates:
                score = float(c_node[1])
                if urgency_weight > 0 and c_sid in deadlines:
                    time_left = deadlines[c_sid] - current_time
                    if time_left < 7200:
                        score -= urgency_weight * (7200 - time_left)
                if score < best_score:
                    best_score = score
                    sid, node, travel_time, path = c_sid, c_node, c_time, c_path

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)
            for p_node in path[1:]:
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        return self.graph.reconstruct_route(full_path, None)

    def solve_skeleton(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0,
        skeleton: list | None = None,
        urgency_weight: float = 0.5,
        hard_station_ids: set[str] | None = None,
    ) -> Route:
        """Hard-station-aware solver combining prefix, injections, and boosted urgency.

        Uses auto-detected hard station data to:
        1. Prefix: visit ultra-sparse stations first (< 20 TEG nodes, e.g. K.O.)
        2. Inject: detour to junction-reachable stations when passing nearby
        3. Urgency boost: hard stations get 2x urgency weight

        The skeleton provides the schedule. Each waypoint becomes either a
        prefix (if window_start is near start_time) or an injection (if it
        has an approach_station). Everything else gets urgency-boosted.

        Args:
            start_station: Station ID to start from.
            required_stations: Set of station IDs to visit.
            start_time: Earliest departure time.
            skeleton: List of SkeletonWaypoint (from SkeletonScheduler).
            urgency_weight: Base weight for deadline urgency scoring.
            hard_station_ids: All detected hard station IDs (get boosted urgency).
        """
        from src.solver.hard_stations import SkeletonWaypoint

        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)
        full_path: list[TENode] = [current_node]

        hard_ids = hard_station_ids or set()

        # Classify skeleton waypoints into prefix vs injection
        prefix_stations: list[str] = []
        # injection: (target, [approach], max_time, earliest, latest)
        inject_map: dict[str, list[tuple[str, int, int, int]]] = {}

        if skeleton:
            for wp in sorted(skeleton, key=lambda w: w.target_time):
                window_duration = wp.window_end - wp.window_start
                # Ultra-narrow windows (< 4h) near start → prefix
                if window_duration < 4 * 3600 and wp.window_start < start_time + 3600:
                    prefix_stations.append(wp.station_id)
                elif wp.approach_station:
                    # Has a junction approach → injection.
                    # Use full service window (0, 999999) — fire anytime the
                    # solver passes the approach station, not just during the
                    # skeleton's scheduled window. The skeleton window is for
                    # scheduling; injections should be opportunistic.
                    if wp.approach_station not in inject_map:
                        inject_map[wp.approach_station] = []
                    inject_map[wp.approach_station].append(
                        (wp.station_id, 900, 0, 999999)
                    )

        # Visit prefix stations first (forced order)
        for prefix_sid in prefix_stations:
            if prefix_sid not in unvisited:
                continue
            arr_node, travel_time, path = self.graph.earliest_arrival(
                current_node, prefix_sid
            )
            if arr_node is None:
                continue
            full_path.extend(path[1:])
            current_node = arr_node
            unvisited.discard(prefix_sid)
            for p_node in path[1:]:
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        # Pre-compute deadlines for urgency scoring
        deadlines: dict[str, int] = {}
        for sid in required_stations:
            nodes = self.graph._station_nodes.get(sid, [])
            if nodes:
                deadlines[sid] = nodes[-1][1]

        injected: set[str] = set()

        while unvisited:
            current_sid = current_node[0]
            current_time = current_node[1]

            # Check injections: when at approach station, detour to target
            if current_sid in inject_map:
                for target, max_time, earliest, latest in inject_map[current_sid]:
                    if target in unvisited and target not in injected:
                        if not (earliest <= current_time <= latest):
                            continue
                        arr_node, travel_time, path = self.graph.earliest_arrival(
                            current_node, target
                        )
                        if arr_node is not None and travel_time <= max_time:
                            full_path.extend(path[1:])
                            current_node = arr_node
                            unvisited.discard(target)
                            injected.add(target)
                            for p_node in path[1:]:
                                if p_node[0] in unvisited:
                                    unvisited.discard(p_node[0])
                            break
                else:
                    pass

                if current_node[0] != current_sid:
                    continue  # injection happened, restart loop

            # k=5 NN with urgency scoring (boosted for hard stations)
            candidates = self.graph.earliest_arrival_k_nearest(
                current_node, unvisited, k=5
            )
            if not candidates:
                break

            best_score = float("inf")
            sid, node, travel_time, path = candidates[0]
            for c_sid, c_node, c_time, c_path in candidates:
                score = float(c_node[1])
                if urgency_weight > 0 and c_sid in deadlines:
                    time_left = deadlines[c_sid] - current_time
                    if time_left < 7200:  # < 2h left
                        score -= urgency_weight * (7200 - time_left)

                if score < best_score:
                    best_score = score
                    sid, node, travel_time, path = c_sid, c_node, c_time, c_path

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)
            for p_node in path[1:]:
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        return self.graph.reconstruct_route(full_path, None)

    def solve_with_pairings(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0,
        pairings: list | None = None,
        urgency_weight: float = 0.5,
    ) -> Route:
        """Simple hard-station solver: pair each hard station with its junction.

        When the solver arrives at a junction, it grabs the paired hard station
        as a side-trip. Ultra-sparse stations (is_prefix=True) are visited first.
        Everything else is k=5 NN with urgency.

        This is the simplest effective hard-station strategy:
        - K.O. → prefix (visit first, it only has 9 trains/day)
        - T4 → paired with Hatton Cross (grab when passing)
        - Mill Hill East → paired with Finchley Central (grab when passing)

        Args:
            start_station: Station ID to start from.
            required_stations: Set of station IDs to visit.
            start_time: Earliest departure time.
            pairings: List of HardStationPairing from build_pairings().
            urgency_weight: Weight for deadline urgency scoring.
        """
        from src.solver.hard_stations import HardStationPairing

        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)
        full_path: list[TENode] = [current_node]

        # Classify pairings: prefix vs junction grab
        prefix_stations: list[str] = []
        # junction_id → [(hard_station_id, max_grab_time)]
        grab_map: dict[str, list[tuple[str, int]]] = {}

        if pairings:
            for p in pairings:
                if p.is_prefix:
                    prefix_stations.append(p.hard_station_id)
                else:
                    if p.junction_id not in grab_map:
                        grab_map[p.junction_id] = []
                    # Allow grab if round-trip < 15 min (900s), else use bigger budget
                    max_time = max(900, int(p.round_trip_cost_s * 1.5))
                    grab_map[p.junction_id].append((p.hard_station_id, max_time))

        # Phase 1: Visit prefix stations first (e.g., K.O. morning shuttle)
        for prefix_sid in prefix_stations:
            if prefix_sid not in unvisited:
                continue
            arr_node, travel_time, path = self.graph.earliest_arrival(
                current_node, prefix_sid
            )
            if arr_node is None:
                continue
            full_path.extend(path[1:])
            current_node = arr_node
            unvisited.discard(prefix_sid)
            for p_node in path[1:]:
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        # Pre-compute deadlines for urgency scoring
        deadlines: dict[str, int] = {}
        for sid in required_stations:
            nodes = self.graph._station_nodes.get(sid, [])
            if nodes:
                deadlines[sid] = nodes[-1][1]

        grabbed: set[str] = set()

        # Phase 2: k=5 NN with junction grabs
        while unvisited:
            current_sid = current_node[0]
            current_time = current_node[1]

            # At a junction? Grab its paired hard stations
            if current_sid in grab_map:
                for target_sid, max_time in grab_map[current_sid]:
                    if target_sid not in unvisited or target_sid in grabbed:
                        continue
                    arr_node, travel_time, path = self.graph.earliest_arrival(
                        current_node, target_sid
                    )
                    if arr_node is not None and travel_time <= max_time:
                        full_path.extend(path[1:])
                        current_node = arr_node
                        unvisited.discard(target_sid)
                        grabbed.add(target_sid)
                        for p_node in path[1:]:
                            if p_node[0] in unvisited:
                                unvisited.discard(p_node[0])
                        break
                else:
                    pass

                if current_node[0] != current_sid:
                    continue  # grab happened, restart loop

            # k=5 NN with urgency
            candidates = self.graph.earliest_arrival_k_nearest(
                current_node, unvisited, k=5
            )
            if not candidates:
                break

            best_score = float("inf")
            sid, node, travel_time, path = candidates[0]
            for c_sid, c_node, c_time, c_path in candidates:
                score = float(c_node[1])
                if urgency_weight > 0 and c_sid in deadlines:
                    time_left = deadlines[c_sid] - current_time
                    if time_left < 7200:
                        score -= urgency_weight * (7200 - time_left)

                if score < best_score:
                    best_score = score
                    sid, node, travel_time, path = c_sid, c_node, c_time, c_path

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)
            for p_node in path[1:]:
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        return self.graph.reconstruct_route(full_path, None)

    def solve_hybrid(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0, k_phase1: int = 3,
        switch_threshold: int = 10,
    ) -> Route:
        """Hybrid solver: k=3 lookahead for bulk, then k=1 NN for stragglers.

        Phase 1 uses k-nearest with 2-step lookahead (efficient routing).
        When fewer than switch_threshold stations remain, switches to k=1 NN
        which is more reliable at reaching branch-end stations.

        Args:
            start_station: Station ID to start from.
            required_stations: Set of station IDs to visit.
            start_time: Earliest departure time.
            k_phase1: k for the lookahead phase (default 3).
            switch_threshold: Switch to k=1 when this many stations remain.
        """
        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)
        full_path: list[TENode] = [current_node]

        while unvisited:
            # Phase selection: use lookahead when many remain, NN when few left
            use_lookahead = len(unvisited) > switch_threshold

            if use_lookahead:
                candidates = self.graph.earliest_arrival_k_nearest(
                    current_node, unvisited, k=k_phase1
                )
                if not candidates:
                    break

                if len(candidates) == 1:
                    sid, node, travel_time, path = candidates[0]
                else:
                    best_score = float("inf")
                    sid, node, travel_time, path = candidates[0]

                    for c_sid, c_node, c_time, c_path in candidates:
                        score = float(c_node[1])
                        # 2-step lookahead
                        remaining = unvisited - {c_sid}
                        for p in c_path[1:]:
                            remaining.discard(p[0])
                        if remaining:
                            min_next = min(
                                (self.graph.static_dist(c_sid, r) for r in remaining),
                                default=0
                            )
                            score += min_next

                        if score < best_score:
                            best_score = score
                            sid, node, travel_time, path = c_sid, c_node, c_time, c_path
            else:
                # k=1 NN for remaining stations
                sid, node, travel_time, path = self.graph.earliest_arrival_nearest(
                    current_node, unvisited
                )
                if sid is None:
                    break

            full_path.extend(path[1:])
            current_node = node
            unvisited.discard(sid)
            for p_node in path[1:]:
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        return self.graph.reconstruct_route(full_path, None)

    def solve_fixed_order(self, station_order: list[str], start_time: int = 0) -> Route:
        """Simulate traversal of a fixed station order through the TEG.

        Unlike solve(), this doesn't choose which station to visit next —
        it follows the given order exactly, using Dijkstra for each hop.
        Unreachable stations are skipped but retried at the end via NN.

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
        visited: set[str] = {station_order[0]}
        skipped: list[str] = []

        for i in range(len(station_order) - 1):
            target_sid = station_order[i + 1]
            if target_sid in visited:
                continue
            arr_node, travel_time, path = self.graph.earliest_arrival(
                current_node, target_sid
            )
            if arr_node is None:
                skipped.append(target_sid)
                continue
            full_path.extend(path[1:])
            current_node = arr_node
            visited.add(target_sid)
            # Mark pass-through stations
            for p in path[1:]:
                visited.add(p[0])

        # Retry skipped stations via nearest-neighbor
        if skipped:
            unvisited = set(s for s in skipped if s not in visited)
            while unvisited:
                sid, node, _, path = self.graph.earliest_arrival_nearest(
                    current_node, unvisited
                )
                if sid is None:
                    break
                full_path.extend(path[1:])
                current_node = node
                unvisited.discard(sid)
                visited.add(sid)
                for p in path[1:]:
                    if p[0] in unvisited:
                        unvisited.discard(p[0])
                        visited.add(p[0])

        return self.graph.reconstruct_route(full_path, None)

    def solve_branch_aware(
        self, start_station: str, required_stations: set[str],
        start_time: int = 0, branch_map: dict | None = None,
    ) -> Route:
        """NN solver that completes branches when passing through junctions.

        Like solve_fast(k=1), but when the solver visits a station that is a
        junction leading to unvisited branch termini, it rides to each terminus
        before continuing with NN. This prevents zigzagging across branches.

        Args:
            start_station: Station ID to start from.
            required_stations: Set of station IDs that must be visited.
            start_time: Earliest departure time.
            branch_map: Dict of junction_id -> list of (terminus_id, branch_path).
                        Built by build_branch_map() if not provided.
        """
        start_nodes = self.graph.get_start_nodes(start_station, start_time)
        if not start_nodes:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        current_node = start_nodes[0]
        unvisited = set(required_stations)
        unvisited.discard(start_station)
        full_path: list[TENode] = [current_node]

        if branch_map is None:
            branch_map = {}

        # Map: junction station -> list of short-branch termini
        # Only trigger for branches ≤ max_branch_len stops from junction
        max_branch_len = 6
        junction_to_termini: dict[str, list[str]] = {}
        for junction_id, branches in branch_map.items():
            for terminus_id, branch_path in branches:
                if len(branch_path) - 1 <= max_branch_len:  # path includes junction
                    if junction_id not in junction_to_termini:
                        junction_to_termini[junction_id] = []
                    junction_to_termini[junction_id].append(terminus_id)

        while unvisited:
            current_sid = current_node[0]

            # Check if current station is a junction with unvisited branch termini
            branch_target = None
            if current_sid in junction_to_termini:
                for terminus_id in junction_to_termini[current_sid]:
                    if terminus_id in unvisited:
                        branch_target = terminus_id
                        break

            if branch_target:
                # Only ride to terminus if reachable within 20 min
                arr_node, travel_time, path = self.graph.earliest_arrival(
                    current_node, branch_target
                )
                if arr_node is not None and travel_time <= 1200:
                    full_path.extend(path[1:])
                    current_node = arr_node
                    unvisited.discard(branch_target)
                    for p_node in path[1:]:
                        if p_node[0] in unvisited:
                            unvisited.discard(p_node[0])
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
                if p_node[0] in unvisited:
                    unvisited.discard(p_node[0])

        return self.graph.reconstruct_route(full_path, None)

    @staticmethod
    def build_branch_map(parsed_gtfs, required_stations: set[str]) -> dict:
        """Build a map of junction stations to their branch termini.

        Uses transit-only edges (no walking) to find true branch structure.
        Returns: {junction_id: [(terminus_id, [branch_path_ids]), ...]}
        """
        import networkx as nx

        # Build transit-only graph
        G = nx.Graph()
        for seg in parsed_gtfs.segments:
            if seg.from_station_id in required_stations and seg.to_station_id in required_stations:
                G.add_edge(seg.from_station_id, seg.to_station_id)

        branch_map: dict[str, list] = {}

        for sid in G.nodes():
            if G.degree(sid) != 1:
                continue  # not a terminus

            # Walk inward to find junction
            curr = sid
            path = [curr]
            while G.degree(curr) <= 2:
                neighbors = [n for n in G.neighbors(curr) if n not in path]
                if not neighbors:
                    break
                curr = neighbors[0]
                path.append(curr)

            junction = curr
            if junction not in branch_map:
                branch_map[junction] = []
            branch_map[junction].append((sid, path))

        return branch_map

    def teg_local_search(
        self, route: Route, required_stations: set[str],
        start_time: int, max_iterations: int = 2000,
        console=None,
    ) -> Route:
        """Improve a route via 2-opt/or-opt evaluated directly on the TEG.

        Unlike static local search, every move is evaluated by simulating
        through the real timetable. This respects train schedules but is
        slower (~0.3s per evaluation → ~10 min for 2000 iterations).

        Moves tried:
        - 2opt: reverse a segment of the visit order
        - oropt: relocate 1-3 consecutive stations to a new position
        - block: swap two segments of similar length
        """
        # Extract unique station visit order
        seen: set[str] = set()
        order: list[str] = []
        for v in route.visits:
            sid = v["station_id"]
            if sid not in seen and sid in required_stations:
                seen.add(sid)
                order.append(sid)

        if len(order) < 4:
            return route

        best_order = list(order)
        best_time = route.total_time_seconds
        best_visited = route.stations_visited
        n = len(order)
        rng = random.Random(42)

        no_improve = 0
        for iteration in range(max_iterations):
            move = rng.choice(["2opt", "oropt1", "oropt2", "oropt3", "block"])

            trial_order = list(best_order)

            if move == "2opt":
                i = rng.randint(1, n - 2)
                j = rng.randint(i + 1, n - 1)
                trial_order[i:j+1] = reversed(trial_order[i:j+1])

            elif move.startswith("oropt"):
                seg_len = int(move[-1])
                if n - seg_len < 2:
                    continue
                i = rng.randint(1, n - seg_len)
                seg = trial_order[i:i + seg_len]
                del trial_order[i:i + seg_len]
                j = rng.randint(1, len(trial_order) - 1)
                trial_order[j:j] = seg

            elif move == "block":
                block_size = rng.randint(2, min(8, n // 4))
                if n < 2 * block_size + 2:
                    continue
                i = rng.randint(1, n - 2 * block_size)
                j = rng.randint(i + block_size, n - block_size)
                trial_order[i:i+block_size], trial_order[j:j+block_size] = (
                    trial_order[j:j+block_size], trial_order[i:i+block_size]
                )

            trial_route = self.solve_fixed_order(trial_order, start_time=start_time)

            if trial_route.total_time_seconds <= 0:
                continue

            if (trial_route.stations_visited > best_visited
                or (trial_route.stations_visited == best_visited
                    and trial_route.total_time_seconds < best_time)):
                best_order = trial_order
                best_time = trial_route.total_time_seconds
                best_visited = trial_route.stations_visited
                no_improve = 0

                if console:
                    console.print(
                        f"  [{iteration}] {move}: "
                        f"{best_visited}/{len(required_stations)} "
                        f"{best_time // 3600}h{(best_time % 3600) // 60:02d}m"
                    )
            else:
                no_improve += 1

            if no_improve > 500:
                break

        return self.solve_fixed_order(best_order, start_time=start_time)
