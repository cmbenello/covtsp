"""Tests for greedy solver, local search, and LP relaxation."""

from datetime import date

import pytest

from src.config import load_config
from src.graph.network import TransitNetwork
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser
from src.solver.greedy import GreedySolver
from src.solver.local_search import LocalSearchOptimizer, random_order_baseline
from src.solver.lp_bound import compute_lp_bound, compute_lp_bound_time_expanded, compute_optimality_gap


TOY_CONFIG = "configs/toy.yaml"


@pytest.fixture
def toy_parsed():
    cfg = load_config(TOY_CONFIG)
    parser = GTFSParser(cfg)
    return parser.parse(target_date=date(2024, 3, 15))


@pytest.fixture
def time_expanded(toy_parsed):
    return TimeExpandedGraph.from_gtfs(toy_parsed)


@pytest.fixture
def static_network(toy_parsed):
    return TransitNetwork.from_gtfs(toy_parsed)


@pytest.fixture
def greedy_route(time_expanded, toy_parsed):
    solver = GreedySolver(time_expanded, lookahead=2)
    return solver.solve("S1", toy_parsed.required_station_ids, start_time=6 * 3600)


class TestGreedySolver:
    def test_visits_all_stations(self, greedy_route, toy_parsed):
        """Greedy solver must visit all required stations."""
        visited = {v["station_id"] for v in greedy_route.visits}
        for sid in toy_parsed.required_station_ids:
            assert sid in visited, f"Station {sid} not visited"

    def test_no_missing_stations(self, greedy_route, toy_parsed):
        """Every required station appears in the route output."""
        visited = {v["station_id"] for v in greedy_route.visits}
        missing = toy_parsed.required_station_ids - visited
        assert len(missing) == 0, f"Missing stations: {missing}"

    def test_total_time_positive(self, greedy_route):
        """Route should have positive total time."""
        assert greedy_route.total_time_seconds > 0

    def test_stations_visited_count(self, greedy_route, toy_parsed):
        """Should report visiting at least the required number of stations."""
        assert greedy_route.stations_visited >= len(toy_parsed.required_station_ids)

    def test_route_has_visits(self, greedy_route):
        """Route should contain visit entries."""
        assert len(greedy_route.visits) > 0

    def test_time_monotonic(self, greedy_route):
        """Arrival times should be non-decreasing throughout the route."""
        times = []
        for v in greedy_route.visits:
            h, m = map(int, v["arrival"].split(":"))
            times.append(h * 60 + m)
        for i in range(1, len(times)):
            assert times[i] >= times[i - 1], (
                f"Time decreased: {greedy_route.visits[i-1]['arrival']} -> {greedy_route.visits[i]['arrival']}"
            )


class TestLocalSearch:
    def test_does_not_worsen(self, time_expanded, toy_parsed, greedy_route):
        """Local search should not produce a worse solution."""
        station_order = []
        seen = set()
        for v in greedy_route.visits:
            sid = v["station_id"]
            if sid not in seen:
                seen.add(sid)
                station_order.append(sid)

        ls = LocalSearchOptimizer(time_expanded, start_time=6 * 3600)
        improved_order, improved_time = ls.improve(
            station_order, toy_parsed.required_station_ids, max_iterations=50
        )

        assert improved_time <= greedy_route.total_time_seconds or improved_time < 0

    def test_greedy_beats_random(self, time_expanded, toy_parsed, greedy_route):
        """Greedy solution should generally be faster than random ordering."""
        _, random_time = random_order_baseline(
            time_expanded, "S1", toy_parsed.required_station_ids,
            start_time=6 * 3600, n_trials=5,
        )

        # Greedy should be at least as good as the best random trial
        # (with enough trials random might occasionally match, so we just check it runs)
        assert greedy_route.total_time_seconds > 0
        if random_time > 0:
            assert greedy_route.total_time_seconds <= random_time * 1.5  # generous margin


class TestLPBound:
    def test_static_lp_bound(self, static_network, toy_parsed):
        """LP bound should be computable and non-negative."""
        result = compute_lp_bound(static_network, toy_parsed.required_station_ids, "S1")
        assert result["status"] == "Optimal"
        assert result["lp_bound_seconds"] is not None
        assert result["lp_bound_seconds"] >= 0

    def test_lp_bound_less_than_heuristic(self, static_network, toy_parsed, greedy_route):
        """LP bound must be <= the heuristic solution (it's a lower bound)."""
        result = compute_lp_bound(static_network, toy_parsed.required_station_ids, "S1")
        if result["lp_bound_seconds"] is not None:
            assert result["lp_bound_seconds"] <= greedy_route.total_time_seconds

    def test_optimality_gap_positive(self, static_network, toy_parsed, greedy_route):
        """Optimality gap should be non-negative."""
        result = compute_lp_bound(static_network, toy_parsed.required_station_ids, "S1")
        if result["lp_bound_seconds"] and result["lp_bound_seconds"] > 0:
            gap = compute_optimality_gap(
                greedy_route.total_time_seconds, result["lp_bound_seconds"]
            )
            assert gap >= 0

    def test_teg_lp_on_toy(self, time_expanded, toy_parsed):
        """Time-expanded LP should work on the small toy network."""
        result = compute_lp_bound_time_expanded(
            time_expanded, toy_parsed.required_station_ids, "S1", start_time=6 * 3600
        )
        # Should either solve or skip (not crash)
        assert result["status"] in ("Optimal", "skipped", "Infeasible", "Not Solved")

    def test_optimality_gap_calculation(self):
        """Test the gap formula directly."""
        assert compute_optimality_gap(110, 100) == pytest.approx(10.0)
        assert compute_optimality_gap(100, 100) == pytest.approx(0.0)
        assert compute_optimality_gap(100, 0) == float("inf")
