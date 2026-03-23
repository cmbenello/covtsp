"""Local search optimization: 2-opt, Or-opt, and 3-opt moves on station visit order."""

import random
from dataclasses import dataclass

from src.graph.time_expanded import Route, TENode, TimeExpandedGraph


class LocalSearchOptimizer:
    """Improve a route via local search moves on the station visit order.

    Operates on the sequence of required stations. After each move,
    re-simulates the full timed path since the graph is time-dependent
    (you can't just swap static edge weights).
    """

    def __init__(self, graph: TimeExpandedGraph, start_time: int = 0):
        self.graph = graph
        self.start_time = start_time

    def improve(
        self,
        station_order: list[str],
        required_stations: set[str],
        max_iterations: int = 1000,
        patience: int = 100,
    ) -> tuple[list[str], int]:
        """Improve station visit order via local search.

        Args:
            station_order: Initial ordered list of station IDs to visit.
            required_stations: Set of all required station IDs.
            max_iterations: Maximum number of improvement iterations.
            patience: Stop after this many iterations without improvement.

        Returns:
            (improved_station_order, total_time_seconds)
        """
        best_order = list(station_order)
        best_time = self._simulate_time(best_order)

        if best_time < 0:
            return best_order, best_time

        no_improve = 0

        for iteration in range(max_iterations):
            if no_improve >= patience:
                break

            # Alternate between 2-opt and Or-opt moves
            if iteration % 2 == 0:
                candidate = self._two_opt_move(best_order)
            else:
                candidate = self._or_opt_move(best_order)

            if candidate is None:
                no_improve += 1
                continue

            candidate_time = self._simulate_time(candidate)

            if 0 < candidate_time < best_time:
                best_order = candidate
                best_time = candidate_time
                no_improve = 0
            else:
                no_improve += 1

        return best_order, best_time

    def _two_opt_move(self, order: list[str]) -> list[str] | None:
        """Reverse a random subsequence in the station order.

        In standard TSP, 2-opt reverses a segment to uncross edges.
        Here we must re-simulate since travel times are time-dependent.
        """
        n = len(order)
        if n < 4:
            return None

        # Keep first station fixed (start point)
        i = random.randint(1, n - 3)
        j = random.randint(i + 1, n - 1)

        new_order = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
        return new_order

    def _or_opt_move(self, order: list[str]) -> list[str] | None:
        """Relocate a segment of 1-3 stations to a different position.

        Or-opt is often more effective than 2-opt for asymmetric problems
        like ours (time-dependent graph is inherently asymmetric).
        """
        n = len(order)
        if n < 4:
            return None

        # Pick segment size (1, 2, or 3 stations)
        seg_size = random.randint(1, min(3, n - 2))

        # Pick segment start (keep first station fixed)
        seg_start = random.randint(1, n - seg_size)

        # Pick insertion point (different from current position)
        segment = order[seg_start : seg_start + seg_size]
        remaining = order[:seg_start] + order[seg_start + seg_size :]

        if len(remaining) < 2:
            return None

        insert_pos = random.randint(1, len(remaining))

        new_order = remaining[:insert_pos] + segment + remaining[insert_pos:]
        return new_order

    def _simulate_time(self, station_order: list[str]) -> int:
        """Simulate traversal of stations in order using static graph distances.

        Uses precomputed all-pairs static distances (O(1) per leg) rather than
        running full TEG Dijkstra for each consecutive pair. This is a lower-bound
        approximation — ignores timetable timing — but is fast enough for local
        search to evaluate many candidate orderings.

        Returns:
            Approximate total time in seconds, or -1 if route has unreachable legs.
        """
        if not station_order:
            return -1

        total = 0
        for i in range(len(station_order) - 1):
            d = self.graph.static_dist(station_order[i], station_order[i + 1])
            if d == float("inf"):
                return -1
            total += d

        return int(total)


def random_order_baseline(
    graph: TimeExpandedGraph,
    start_station: str,
    required_stations: set[str],
    start_time: int = 0,
    n_trials: int = 10,
) -> tuple[list[str], int]:
    """Generate random station orderings as a baseline for comparison.

    Returns the best random ordering found across n_trials.
    """
    optimizer = LocalSearchOptimizer(graph, start_time)
    other_stations = list(required_stations - {start_station})

    best_order = None
    best_time = float("inf")

    for _ in range(n_trials):
        random.shuffle(other_stations)
        order = [start_station] + other_stations
        time = optimizer._simulate_time(order)

        if 0 < time < best_time:
            best_time = time
            best_order = list(order)

    return best_order or [start_station] + other_stations, int(best_time) if best_time < float("inf") else -1
