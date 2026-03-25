"""Pure static-graph optimizer: simulated annealing over station visit order.

Design
------
This is Phase 1 of a two-phase pipeline:

  Phase 1 — Static plan (this file)
    Find the best station *ordering* ignoring timetable timing.
    The static graph includes running legs (pre-computed from the Google
    Routes API cache), so running between nearby stations is a first-class
    option alongside train edges.

  Phase 2 — TEG backtest (backtest.py)
    Take the static ordering and simulate it through the real GTFS
    time-expanded graph at several candidate start times. Report the
    best achievable actual time.

Why separate the two phases?
  The TEG greedy solver is forced to make irrevocable decisions at each
  step based on the current timetable state. It can't reason about whether
  a globally better ordering exists. The static optimizer can explore
  hundreds of thousands of orderings cheaply (O(1) lookup per edge),
  find something close to optimal, and hand it to the TEG to validate.

SA acceptance criterion:
  Worse solutions are accepted with probability exp(-delta / T) where
  delta is the cost increase in seconds and T is the current temperature
  (also in seconds). A temperature of 300 means we'll accept a 5-minute
  detour with ~37% probability. Temperature decays geometrically to t_end.

Interview depth:
  - Why SA over pure 2-opt? Escapes local minima; the tour space has many
    local optima that 2-opt gets stuck in.
  - Why static distances as proxy? O(1) lookup vs O(V log V) Dijkstra per
    move; enables 50k–100k iterations in seconds.
  - Why multiple restarts? SA is sensitive to initial conditions on
    asymmetric problems (our graph is asymmetric because running edges
    are often faster in one direction due to routing geometry).
  - Integrality gap: static cost is a lower bound on real TEG cost because
    it ignores timetable waits. The gap between static optimum and TEG
    reality tells us how much timetable structure is hurting us.
"""

import logging
import math
import random
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from src.graph.time_expanded import TimeExpandedGraph

logger = logging.getLogger(__name__)
console = Console()


class StaticOptimizer:
    """Optimize station visit order on the static graph via simulated annealing.

    Running legs (from Google Routes API cache) are included in the static
    graph as first-class edges, so the optimizer naturally considers running
    between nearby stations when it's cheaper than waiting for a train.
    """

    def __init__(self, teg: TimeExpandedGraph, seed: Optional[int] = None):
        self.teg = teg
        self._deadlines: dict[str, int] = {}  # station_id -> latest service time
        self._start_time: int = 0  # estimated start time for deadline estimation
        if seed is not None:
            random.seed(seed)

    def set_deadlines(self, start_time: int, teg: TimeExpandedGraph):
        """Pre-compute service deadlines for each station.

        The deadline is the latest time a station has any TEG node (i.e., the
        last departure/arrival at that station). Stations visited after their
        deadline in the real timetable will be missed.
        """
        self._start_time = start_time
        for sid, nodes in teg._station_nodes.items():
            if nodes:
                self._deadlines[sid] = nodes[-1][1]

    # ------------------------------------------------------------------
    # Cost and move primitives
    # ------------------------------------------------------------------

    def _cost(self, order: list[str]) -> float:
        """Sum of static edge weights along the ordering (seconds).

        Returns inf if any leg is unreachable in the static graph.
        """
        total = 0.0
        for i in range(len(order) - 1):
            d = self.teg.static_dist(order[i], order[i + 1])
            if d == float("inf"):
                return float("inf")
            total += d
        return total

    def _greedy_nn(self, start: str, required: list[str]) -> list[str]:
        """Nearest-neighbor greedy seed: always go to the closest unvisited station."""
        remaining = set(required) - {start}
        order = [start]
        current = start
        while remaining:
            best = min(remaining, key=lambda s: self.teg.static_dist(current, s))
            order.append(best)
            remaining.remove(best)
            current = best
        return order

    def _two_opt(self, order: list[str]) -> list[str]:
        """Reverse a random interior subsequence (first station stays fixed)."""
        n = len(order)
        if n < 4:
            return order[:]
        i = random.randint(1, n - 3)
        j = random.randint(i + 1, n - 1)
        return order[:i] + order[i : j + 1][::-1] + order[j + 1 :]

    def _or_opt(self, order: list[str], seg_size: Optional[int] = None) -> list[str]:
        """Relocate a segment of 1–3 stations to a different position.

        Or-opt is more effective than 2-opt on asymmetric problems because
        it can move a cluster of stations to a different part of the route
        without reversing direction.
        """
        n = len(order)
        if n < 4:
            return order[:]
        if seg_size is None:
            seg_size = random.randint(1, min(3, n - 2))
        seg_start = random.randint(1, n - seg_size)
        segment = order[seg_start : seg_start + seg_size]
        remaining = order[:seg_start] + order[seg_start + seg_size :]
        if len(remaining) < 2:
            return order[:]
        insert_pos = random.randint(1, len(remaining))
        return remaining[:insert_pos] + segment + remaining[insert_pos:]

    def _random_double_bridge(self, order: list[str]) -> list[str]:
        """4-opt double-bridge perturbation for escaping deep local minima.

        Splits the tour into 4 segments and reconnects them in a way that
        cannot be achieved by any 2-opt or 3-opt move. Used as a kick when
        SA gets stuck.
        """
        n = len(order)
        if n < 8:
            return self._two_opt(order)
        # Pick 3 cut points (keep first station fixed)
        cuts = sorted(random.sample(range(1, n), 3))
        a, b, c = cuts
        # Reconnect: [0:a] + [c:n] + [b:c] + [a:b]  — standard double-bridge
        return order[:a] + order[c:] + order[b:c] + order[a:b]

    # ------------------------------------------------------------------
    # Main optimizer
    # ------------------------------------------------------------------

    def optimize(
        self,
        required_stations: set[str],
        start_station: str,
        max_iterations: int = 50_000,
        t_start: float = 1800.0,
        t_end: float = 30.0,
        n_restarts: int = 3,
        double_bridge_every: int = 2000,
    ) -> tuple[list[str], float]:
        """Run SA to find the best static station ordering.

        Args:
            required_stations: All station IDs that must be visited.
            start_station: Fixed first station (kept at index 0).
            max_iterations: SA iterations per restart.
            t_start: Initial temperature in seconds. At T=1800 the solver
                     accepts a 30-minute detour with ~37% probability.
            t_end: Final temperature. At T=30 only ~10s detours accepted.
            n_restarts: Number of independent SA runs (best is kept).
            double_bridge_every: Apply a 4-opt kick every N iterations to
                                 escape deep local minima.

        Returns:
            (best_order, best_static_cost_seconds)
        """
        required_list = list(required_stations)
        global_best_order: list[str] = []
        global_best_cost = float("inf")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                f"Static SA optimizer ({n_restarts} restarts × {max_iterations:,} iterations)...",
                total=None,
            )

            for restart in range(n_restarts):
                # Seed: greedy NN for restart 0, random shuffles for the rest
                if restart == 0:
                    order = self._greedy_nn(start_station, required_list)
                else:
                    other = [s for s in required_list if s != start_station]
                    random.shuffle(other)
                    order = [start_station] + other

                cost = self._cost(order)
                best_order_local = order[:]
                best_cost_local = cost

                # Geometric cooling schedule
                cooling = (t_end / t_start) ** (1.0 / max(max_iterations, 1))
                T = t_start

                for i in range(max_iterations):
                    # Periodic double-bridge kick to escape local minima
                    if i > 0 and i % double_bridge_every == 0:
                        candidate = self._random_double_bridge(order)
                    elif i % 3 == 0:
                        candidate = self._two_opt(order)
                    elif i % 3 == 1:
                        candidate = self._or_opt(order)
                    else:
                        candidate = self._or_opt(order, seg_size=1)

                    new_cost = self._cost(candidate)
                    delta = new_cost - cost

                    # Metropolis acceptance
                    if delta < 0 or (T > 1e-9 and random.random() < math.exp(-delta / T)):
                        order = candidate
                        cost = new_cost
                        if cost < best_cost_local:
                            best_order_local = order[:]
                            best_cost_local = cost

                    T *= cooling

                progress.update(
                    task,
                    description=(
                        f"Static SA: restart {restart + 1}/{n_restarts} done | "
                        f"best so far: {best_cost_local / 3600:.2f}h"
                    ),
                )

                if best_cost_local < global_best_cost:
                    global_best_cost = best_cost_local
                    global_best_order = best_order_local[:]

        return global_best_order, global_best_cost
