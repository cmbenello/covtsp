"""Branch-aware segment solver for the Covering TSP on transit networks.

Decomposes the network into line branches, optimizes branch traversal order
via simulated annealing, then simulates through the time-expanded graph.
"""

import logging
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from src.graph.time_expanded import Route, TimeExpandedGraph
from src.gtfs.parser import ParsedGTFS
from src.solver.greedy import GreedySolver

logger = logging.getLogger(__name__)


@dataclass
class Branch:
    """A contiguous segment of track that must be traversed."""

    branch_id: str
    line_name: str
    stations: list[str]  # ordered from terminal_a to terminal_b
    terminal_a: str  # one endpoint
    terminal_b: str  # other endpoint
    is_dead_end_a: bool = False  # terminal_a has no other lines
    is_dead_end_b: bool = False  # terminal_b has no other lines


class BranchDecomposer:
    """Extracts line branches from GTFS data."""

    def decompose(self, parsed: ParsedGTFS) -> list[Branch]:
        """Decompose the transit network into traversable branches.

        Groups GTFS segments by route_id, builds line graphs, and splits
        at junction nodes (degree >= 3) to produce individual branches.
        """
        # Build undirected line graph per unique route_id
        route_edges: dict[str, set[tuple[str, str]]] = defaultdict(set)
        route_names: dict[str, str] = {}
        for seg in parsed.segments:
            route_edges[seg.route_id].add((seg.from_station_id, seg.to_station_id))
            route_names[seg.route_id] = seg.route_name

        # Deduplicate routes with identical topology
        seen: dict[frozenset, str] = {}
        unique_routes: dict[str, set[tuple[str, str]]] = {}
        for rid, edges in route_edges.items():
            frozen = frozenset(edges)
            if frozen not in seen:
                seen[frozen] = rid
                unique_routes[rid] = edges

        # Build station-to-lines mapping for dead-end detection
        station_lines: dict[str, set[str]] = defaultdict(set)
        for rid, edges in unique_routes.items():
            for a, b in edges:
                station_lines[a].add(route_names[rid])
                station_lines[b].add(route_names[rid])

        branches: list[Branch] = []

        for rid, edges in unique_routes.items():
            line_name = route_names[rid]
            g = nx.Graph()
            for a, b in edges:
                g.add_edge(a, b)

            terminals = {n for n in g.nodes() if g.degree(n) == 1}
            junctions = {n for n in g.nodes() if g.degree(n) >= 3}
            split_points = terminals | junctions

            if not junctions:
                # Simple line with no branches — one branch from terminal to terminal
                if len(terminals) == 2:
                    t = list(terminals)
                    path = nx.shortest_path(g, t[0], t[1])
                    branches.append(self._make_branch(
                        line_name, path, station_lines, len(branches)
                    ))
                elif len(terminals) == 0:
                    # Circle line — pick arbitrary start, do full cycle
                    nodes = list(g.nodes())
                    # Use DFS ordering for a reasonable traversal
                    path = list(nx.dfs_preorder_nodes(g, nodes[0]))
                    branches.append(self._make_branch(
                        line_name, path, station_lines, len(branches)
                    ))
                continue

            # Line with branches — decompose into segments between split points.
            # Find all paths between adjacent split points.
            visited_edges: set[tuple[str, str]] = set()
            for node in split_points:
                for neighbor in g.neighbors(node):
                    edge = (min(node, neighbor), max(node, neighbor))
                    if edge in visited_edges:
                        continue

                    # Walk from node through neighbor until we hit another split point
                    path = [node, neighbor]
                    visited_edges.add(edge)
                    current = neighbor
                    prev = node

                    while current not in split_points:
                        nexts = [n for n in g.neighbors(current) if n != prev]
                        if not nexts:
                            break
                        prev = current
                        current = nexts[0]
                        e = (min(prev, current), max(prev, current))
                        visited_edges.add(e)
                        path.append(current)

                    if len(path) >= 2:
                        branches.append(self._make_branch(
                            line_name, path, station_lines, len(branches)
                        ))

        # Verify coverage
        covered = set()
        for b in branches:
            covered.update(b.stations)

        missing = parsed.required_station_ids - covered
        if missing:
            logger.warning(
                f"Branch decomposition missing {len(missing)} stations: "
                f"{[parsed.stations[s].name for s in list(missing)[:5]]}"
            )

        return branches

    def _make_branch(
        self,
        line_name: str,
        path: list[str],
        station_lines: dict[str, set[str]],
        idx: int,
    ) -> Branch:
        terminal_a = path[0]
        terminal_b = path[-1]
        return Branch(
            branch_id=f"{line_name.lower().replace(' & ', '_').replace(' ', '_')}_{idx}",
            line_name=line_name,
            stations=path,
            terminal_a=terminal_a,
            terminal_b=terminal_b,
            is_dead_end_a=len(station_lines.get(terminal_a, set())) <= 1,
            is_dead_end_b=len(station_lines.get(terminal_b, set())) <= 1,
        )


class SegmentOrderOptimizer:
    """Optimizes branch traversal order using simulated annealing."""

    def __init__(self, teg: TimeExpandedGraph, start_time: int = 0):
        self.teg = teg
        self.start_time = start_time

    def optimize(
        self,
        branches: list[Branch],
        start_station: str,
        required_stations: set[str],
        max_iterations: int = 3000,
        t_start: float = 3600.0,
        t_end: float = 30.0,
        seed: int | None = None,
    ) -> list[tuple[int, bool]]:
        """Find a good branch traversal order via simulated annealing.

        Args:
            branches: List of Branch objects from decomposition.
            start_station: Station to begin from.
            required_stations: All stations that must be visited.
            max_iterations: SA iterations.
            t_start: Initial temperature (seconds).
            t_end: Final temperature.
            seed: Random seed for reproducibility.

        Returns:
            List of (branch_index, forward) tuples. forward=True means
            traverse from terminal_a to terminal_b.
        """
        rng = random.Random(seed)
        n = len(branches)

        # Precompute branch traversal costs (sum of static distances along stations)
        branch_costs = []
        for b in branches:
            cost = 0
            for i in range(len(b.stations) - 1):
                d = self.teg.static_dist(b.stations[i], b.stations[i + 1])
                if d == float("inf"):
                    d = 600  # fallback: 10 min
                cost += d
            branch_costs.append(cost)

        # Build initial solution: find which branch contains start_station
        # and order branches using nearest-exit heuristic
        state = self._initial_solution(branches, start_station, rng)

        best_state = list(state)
        best_cost = self._evaluate(state, branches, branch_costs, start_station, required_stations)
        current_cost = best_cost

        alpha = (t_end / t_start) ** (1.0 / max_iterations)
        temp = t_start

        for iteration in range(max_iterations):
            # Generate neighbor
            new_state = self._neighbor(state, n, rng)
            new_cost = self._evaluate(new_state, branches, branch_costs, start_station, required_stations)

            delta = new_cost - current_cost
            if delta < 0 or rng.random() < math.exp(-delta / max(temp, 1)):
                state = new_state
                current_cost = new_cost
                if current_cost < best_cost:
                    best_cost = current_cost
                    best_state = list(state)

            temp *= alpha

        logger.info(f"SA best cost: {best_cost}s ({best_cost // 3600}h{(best_cost % 3600) // 60}m)")
        return best_state

    def _initial_solution(
        self, branches: list[Branch], start_station: str, rng: random.Random
    ) -> list[tuple[int, bool]]:
        """Build initial ordering: start branch first, then nearest-exit greedy."""
        n = len(branches)

        # Find branch containing start_station
        start_idx = 0
        start_forward = True
        for i, b in enumerate(branches):
            if start_station in b.stations:
                start_idx = i
                # Orient so we start from the correct end
                if b.stations[0] == start_station or b.terminal_a == start_station:
                    start_forward = True
                elif b.stations[-1] == start_station or b.terminal_b == start_station:
                    start_forward = False
                else:
                    # Start station is in the middle — prefer forward
                    pos = b.stations.index(start_station)
                    start_forward = pos <= len(b.stations) // 2
                break

        used = {start_idx}
        order = [(start_idx, start_forward)]

        # Greedy nearest-exit for remaining branches
        for _ in range(n - 1):
            last_b = branches[order[-1][0]]
            last_forward = order[-1][1]
            exit_station = last_b.terminal_b if last_forward else last_b.terminal_a

            best_idx = -1
            best_cost = float("inf")
            best_fwd = True

            for i in range(n):
                if i in used:
                    continue
                b = branches[i]
                # Cost to reach terminal_a
                d_a = self.teg.static_dist(exit_station, b.terminal_a)
                # Cost to reach terminal_b
                d_b = self.teg.static_dist(exit_station, b.terminal_b)

                if d_a <= d_b and d_a < best_cost:
                    best_cost = d_a
                    best_idx = i
                    best_fwd = True
                if d_b < d_a and d_b < best_cost:
                    best_cost = d_b
                    best_idx = i
                    best_fwd = False

            if best_idx == -1:
                # Pick a random unused branch
                remaining = [i for i in range(n) if i not in used]
                best_idx = rng.choice(remaining)
                best_fwd = rng.choice([True, False])

            used.add(best_idx)
            order.append((best_idx, best_fwd))

        return order

    def _neighbor(
        self, state: list[tuple[int, bool]], n: int, rng: random.Random
    ) -> list[tuple[int, bool]]:
        """Generate a neighbor solution via random move."""
        new_state = list(state)
        move = rng.randint(0, 3)

        if move == 0 and n > 2:
            # Swap two branches (keep first fixed)
            i = rng.randint(1, n - 1)
            j = rng.randint(1, n - 1)
            if i != j:
                new_state[i], new_state[j] = new_state[j], new_state[i]
        elif move == 1:
            # Reverse direction of a random branch
            i = rng.randint(0, n - 1)
            idx, fwd = new_state[i]
            new_state[i] = (idx, not fwd)
        elif move == 2 and n > 2:
            # Or-opt: move a branch to a different position
            i = rng.randint(1, n - 1)
            branch = new_state.pop(i)
            j = rng.randint(1, len(new_state))
            new_state.insert(j, branch)
        elif move == 3 and n > 3:
            # Reverse a sub-sequence (2-opt style)
            i = rng.randint(1, n - 2)
            j = rng.randint(i + 1, n - 1)
            new_state[i : j + 1] = reversed(new_state[i : j + 1])

        return new_state

    def _evaluate(
        self,
        state: list[tuple[int, bool]],
        branches: list[Branch],
        branch_costs: list[int],
        start_station: str,
        required_stations: set[str],
    ) -> int:
        """Evaluate total estimated time for a branch ordering."""
        total = 0
        visited = set()
        prev_exit = start_station

        for branch_idx, forward in state:
            b = branches[branch_idx]
            stations = b.stations if forward else list(reversed(b.stations))
            entry = stations[0]
            exit_st = stations[-1]

            # Transfer cost to reach this branch
            if prev_exit != entry:
                transfer = self.teg.static_dist(prev_exit, entry)
                if transfer == float("inf"):
                    transfer = 1800  # 30 min penalty for unreachable
                total += transfer

            # Branch traversal cost — only count unvisited portions
            for i in range(len(stations) - 1):
                if stations[i + 1] not in visited:
                    d = self.teg.static_dist(stations[i], stations[i + 1])
                    if d == float("inf"):
                        d = 600
                    total += d

            visited.update(stations)
            prev_exit = exit_st

        # Penalty for missing stations
        missing = required_stations - visited
        total += len(missing) * 3600  # 1 hour penalty per missing station

        return int(total)


class SegmentSolver:
    """Top-level solver: decompose → optimize order → simulate through TEG.

    Strategy: decompose network into branches, find good start stations
    (outer terminals), and run the full greedy solver from each. The greedy
    solver with lookahead naturally follows line branches and handles the
    TEG timing correctly. The branch decomposition identifies which terminals
    to start from.
    """

    def __init__(self, teg: TimeExpandedGraph, parsed: ParsedGTFS, lookahead: int = 3):
        self.teg = teg
        self.parsed = parsed
        self.lookahead = lookahead

    def solve(
        self,
        required_stations: set[str],
        start_time: int = 0,
        start_stations: list[str] | None = None,
        sa_iterations: int = 3000,
    ) -> Route:
        """Run the segment solver pipeline.

        Tries multiple start stations (outer terminals from branch decomposition)
        with the greedy solver and picks the best result.

        Args:
            required_stations: Stations that must be visited.
            start_time: Earliest departure (seconds since midnight).
            start_stations: Candidate start stations to try.
            sa_iterations: SA iterations for branch ordering (used for SA-based path).

        Returns:
            Best Route found across all start station candidates.
        """
        # Step 1: Decompose into branches to find good start candidates
        decomposer = BranchDecomposer()
        branches = decomposer.decompose(self.parsed)
        logger.info(f"Decomposed into {len(branches)} branches")

        # Step 2: Auto-detect start station candidates
        if start_stations is None:
            start_stations = self._find_start_candidates(branches, required_stations)

        logger.info(
            f"Trying {len(start_stations)} start stations: "
            f"{[self.parsed.stations[s].name for s in start_stations if s in self.parsed.stations]}"
        )

        # Step 3: Run greedy solver from each start station
        best_route: Route | None = None
        greedy = GreedySolver(self.teg, lookahead=self.lookahead, line_aware=True)

        for start_station in start_stations:
            start_nodes = self.teg.get_start_nodes(start_station, start_time)
            if not start_nodes:
                logger.info(f"  Skipping {start_station}: no departures after {start_time}")
                continue

            name = self.parsed.stations[start_station].name if start_station in self.parsed.stations else start_station
            logger.info(f"  Trying start: {name}")

            route = greedy.solve(start_station, required_stations, start_time)

            if route.total_time_seconds > 0:
                logger.info(
                    f"    Time: {route.total_time_seconds}s "
                    f"({route.total_time_seconds // 3600}h"
                    f"{(route.total_time_seconds % 3600) // 60}m), "
                    f"{route.stations_visited}/{len(required_stations)} stations"
                )
                # Prefer: more stations visited, then shorter time
                if best_route is None or (
                    route.stations_visited > best_route.stations_visited
                ) or (
                    route.stations_visited == best_route.stations_visited
                    and route.total_time_seconds < best_route.total_time_seconds
                ):
                    best_route = route

        # Step 4: Also try SA-based branch ordering for the best start
        if best_route is not None and best_route.visits:
            best_start = best_route.visits[0]["station_id"]
            logger.info("Running SA branch ordering refinement...")
            optimizer = SegmentOrderOptimizer(self.teg, start_time)
            branch_order = optimizer.optimize(
                branches, best_start, required_stations,
                max_iterations=sa_iterations, seed=0,
            )
            sa_sequence = self._build_station_sequence(
                branches, branch_order, best_start, required_stations
            )
            sa_greedy = GreedySolver(self.teg, lookahead=1)
            sa_route = sa_greedy.solve_fixed_order(sa_sequence, start_time)
            if sa_route.total_time_seconds > 0:
                logger.info(
                    f"  SA route: {sa_route.total_time_seconds}s, "
                    f"{sa_route.stations_visited} stations"
                )
                if (sa_route.stations_visited > best_route.stations_visited) or (
                    sa_route.stations_visited == best_route.stations_visited
                    and sa_route.total_time_seconds < best_route.total_time_seconds
                ):
                    best_route = sa_route

        if best_route is None:
            logger.warning("Segment solver produced no routes")
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        return best_route

    def _find_start_candidates(
        self, branches: list[Branch], required_stations: set[str]
    ) -> list[str]:
        """Find the best outer terminal stations to start from.

        Prioritizes dead-end terminals that are hardest to reach (would require
        the most backtracking if not started there).
        """
        # Collect all dead-end terminals
        candidates: list[tuple[str, int]] = []
        for b in branches:
            if b.is_dead_end_a and b.terminal_a in required_stations:
                # Score by branch length — longer dead-end = more backtracking saved
                candidates.append((b.terminal_a, len(b.stations)))
            if b.is_dead_end_b and b.terminal_b in required_stations:
                candidates.append((b.terminal_b, len(b.stations)))

        # Deduplicate and sort by branch length (longest dead-end first)
        seen = set()
        unique: list[tuple[str, int]] = []
        for station, length in candidates:
            if station not in seen:
                seen.add(station)
                unique.append((station, length))
        unique.sort(key=lambda x: -x[1])

        # Take top 3 candidates — each full greedy solve takes ~3-5 minutes
        return [s for s, _ in unique[:3]]

    def _build_station_sequence(
        self,
        branches: list[Branch],
        branch_order: list[tuple[int, bool]],
        start_station: str,
        required_stations: set[str],
    ) -> list[str]:
        """Expand branch ordering into a flat station sequence.

        Keeps ALL stations in sequence order (including already-visited ones
        as waypoints) so the TEG simulation has a continuous path. The TEG
        will naturally pass through already-visited stations without counting
        them twice.
        """
        sequence: list[str] = []
        visited: set[str] = set()

        for branch_idx, forward in branch_order:
            b = branches[branch_idx]
            stations = b.stations if forward else list(reversed(b.stations))

            # Check if this branch has any unvisited required stations
            has_new = any(s in required_stations and s not in visited for s in stations)
            if not has_new:
                continue  # Skip entirely redundant branches

            for sid in stations:
                if sid in required_stations and sid not in visited:
                    sequence.append(sid)
                    visited.add(sid)
                elif sid in required_stations:
                    # Already visited — include as waypoint for path continuity
                    # but only if it helps bridge to the next unvisited station
                    sequence.append(sid)

        # Ensure start station is first
        if sequence and sequence[0] != start_station:
            if start_station in sequence:
                sequence.remove(start_station)
            sequence.insert(0, start_station)

        # Add any required stations that weren't covered by branches
        missing = required_stations - visited
        if missing:
            logger.warning(f"Adding {len(missing)} stations not covered by branch order")
            sequence.extend(missing)

        return sequence
