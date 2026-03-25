"""Tests for the branch-aware segment solver."""

import pytest

from src.solver.segment_solver import Branch, BranchDecomposer, SegmentSolver
from tests.test_solver import toy_parsed, time_expanded  # reuse fixtures


class TestBranchDecomposer:
    """Tests for BranchDecomposer on the toy network."""

    def test_decompose_produces_branches(self, toy_parsed):
        decomposer = BranchDecomposer()
        branches = decomposer.decompose(toy_parsed)
        assert len(branches) > 0

    def test_all_stations_covered(self, toy_parsed):
        decomposer = BranchDecomposer()
        branches = decomposer.decompose(toy_parsed)
        covered = set()
        for b in branches:
            covered.update(b.stations)
        assert toy_parsed.required_station_ids.issubset(covered)

    def test_branch_has_valid_terminals(self, toy_parsed):
        decomposer = BranchDecomposer()
        branches = decomposer.decompose(toy_parsed)
        for b in branches:
            assert b.terminal_a == b.stations[0]
            assert b.terminal_b == b.stations[-1]
            assert len(b.stations) >= 2

    def test_branch_has_line_name(self, toy_parsed):
        decomposer = BranchDecomposer()
        branches = decomposer.decompose(toy_parsed)
        for b in branches:
            assert b.line_name, f"Branch {b.branch_id} has no line name"


class TestSegmentSolver:
    """Tests for the full segment solver pipeline."""

    def test_visits_all_stations(self, time_expanded, toy_parsed):
        solver = SegmentSolver(time_expanded, toy_parsed, lookahead=1)
        route = solver.solve(
            toy_parsed.required_station_ids,
            start_time=6 * 3600,
        )
        assert route.stations_visited > 0
        visited = {v["station_id"] for v in route.visits}
        assert toy_parsed.required_station_ids.issubset(visited)

    def test_positive_time(self, time_expanded, toy_parsed):
        solver = SegmentSolver(time_expanded, toy_parsed, lookahead=1)
        route = solver.solve(
            toy_parsed.required_station_ids,
            start_time=6 * 3600,
        )
        assert route.total_time_seconds > 0

    def test_finds_start_candidates(self, time_expanded, toy_parsed):
        solver = SegmentSolver(time_expanded, toy_parsed, lookahead=1)
        decomposer = BranchDecomposer()
        branches = decomposer.decompose(toy_parsed)
        candidates = solver._find_start_candidates(
            branches, toy_parsed.required_station_ids
        )
        assert len(candidates) > 0
        for c in candidates:
            assert c in toy_parsed.required_station_ids
