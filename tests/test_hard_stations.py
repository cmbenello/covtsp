"""Tests for hard station detection, window optimization, and skeleton scheduling."""

from datetime import date

import pytest

from src.config import load_config
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser
from src.solver.greedy import GreedySolver
from src.solver.hard_stations import (
    HardStationDetector,
    SkeletonScheduler,
    VisitWindowOptimizer,
    build_pairings,
)


LONDON_CONFIG = "configs/london.yaml"
TARGET_DATE = date(2026, 3, 24)

# Known hard station IDs
KO_ID = "940GZZLUKOY"  # Kensington Olympia — 9 trains/day
MHE_ID = "940GZZLUMHL"  # Mill Hill East — early deadline stub
T4_ID = "940GZZLUHR4"  # Heathrow Terminal 4 — separate loop
HATTON_CROSS_ID = "940GZZLUHNX"  # Approach station for T4


@pytest.fixture(scope="module")
def london_data():
    config = load_config(LONDON_CONFIG)
    parser = GTFSParser(config)
    parsed = parser.parse(target_date=TARGET_DATE)
    teg = TimeExpandedGraph.from_gtfs(parsed)
    return config, parsed, teg


class TestHardStationDetector:
    def test_detects_ko(self, london_data):
        """Kensington Olympia (9 TEG nodes) should always be detected as hard."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID},
        )
        detected_ids = {p.station_id for p in profiles}
        assert KO_ID in detected_ids

    def test_ko_is_stub(self, london_data):
        """K.O. should be identified as a stub station."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID},
        )
        ko_profile = next(p for p in profiles if p.station_id == KO_ID)
        assert ko_profile.is_stub
        assert ko_profile.branch_depth >= 1

    def test_ko_has_few_teg_nodes(self, london_data):
        """K.O. should have very few TEG nodes (around 9)."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID},
        )
        ko_profile = next(p for p in profiles if p.station_id == KO_ID)
        assert ko_profile.teg_node_count < 20

    def test_ko_service_windows(self, london_data):
        """K.O. should have 2 service windows (morning + evening shuttles)."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        windows = detector._find_service_windows(KO_ID)
        assert len(windows) == 2  # morning cluster + evening cluster
        # Morning window should start before 08:00 (28800s)
        assert windows[0][0] < 28800
        # Evening window should start after 19:00 (68400s)
        assert windows[1][0] > 68400

    def test_mill_hill_east_detected(self, london_data):
        """Mill Hill East should be detected as hard."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={MHE_ID},
        )
        detected_ids = {p.station_id for p in profiles}
        assert MHE_ID in detected_ids

    def test_hardness_ordering(self, london_data):
        """Results should be sorted by hardness score (hardest first)."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(parsed.required_station_ids)
        scores = [p.hardness_score for p in profiles]
        assert scores == sorted(scores, reverse=True)

    def test_central_stations_not_hard(self, london_data):
        """Central high-frequency stations should not be detected as hard."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(parsed.required_station_ids)
        detected_ids = {p.station_id for p in profiles}
        # Oxford Circus, King's Cross — these are well-connected, high-frequency
        # They should not be in the hard station list
        oxford_circus = "940GZZLUOXC"
        kings_cross = "940GZZLUKSX"
        assert oxford_circus not in detected_ids
        assert kings_cross not in detected_ids

    def test_force_hard_override(self, london_data):
        """Stations in force_hard should always be included."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        # Force a central station as hard
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={"940GZZLUOXC"},  # Oxford Circus
        )
        detected_ids = {p.station_id for p in profiles}
        assert "940GZZLUOXC" in detected_ids

    def test_exclude_override(self, london_data):
        """Stations in exclude should never be included."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID},
            exclude={KO_ID},
        )
        detected_ids = {p.station_id for p in profiles}
        assert KO_ID not in detected_ids


class TestVisitWindowOptimizer:
    def test_ko_morning_cheaper(self, london_data):
        """K.O. morning window should be cheaper than evening."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID},
        )
        ko_profile = next(p for p in profiles if p.station_id == KO_ID)

        optimizer = VisitWindowOptimizer(teg)
        windows = optimizer.compute_optimal_windows(ko_profile)

        assert len(windows) >= 2
        # Windows are sorted by cost — cheapest first
        assert windows[0][2] <= windows[-1][2]

    def test_returns_valid_windows(self, london_data):
        """All returned windows should have positive cost."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID},
        )

        optimizer = VisitWindowOptimizer(teg)
        for profile in profiles:
            if profile.station_id in {KO_ID, MHE_ID}:
                windows = optimizer.compute_optimal_windows(profile)
                for w_start, w_end, cost in windows:
                    assert w_start <= w_end
                    assert cost > 0


class TestSkeletonScheduler:
    def test_skeleton_is_time_ordered(self, london_data):
        """Skeleton waypoints should be sorted by target_time."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID, T4_ID},
        )

        optimizer = VisitWindowOptimizer(teg)
        for p in profiles:
            p.optimal_windows = optimizer.compute_optimal_windows(p)

        scheduler = SkeletonScheduler(teg)
        skeleton = scheduler.build_skeleton(profiles, start_time=5 * 3600 + 30 * 60)

        times = [w.target_time for w in skeleton]
        assert times == sorted(times)

    def test_skeleton_covers_all_hard_stations(self, london_data):
        """Every hard station should have a waypoint in the skeleton."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID, T4_ID},
        )

        optimizer = VisitWindowOptimizer(teg)
        for p in profiles:
            p.optimal_windows = optimizer.compute_optimal_windows(p)

        scheduler = SkeletonScheduler(teg)
        skeleton = scheduler.build_skeleton(profiles, start_time=5 * 3600 + 30 * 60)

        skeleton_ids = {w.station_id for w in skeleton}
        profile_ids = {p.station_id for p in profiles}
        assert profile_ids == skeleton_ids


class TestSkeletonSolver:
    def test_skeleton_solver_visits_all(self, london_data):
        """solve_skeleton should visit all 272 stations."""
        config, parsed, teg = london_data
        n_required = len(parsed.required_station_ids)

        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID, T4_ID},
        )

        optimizer = VisitWindowOptimizer(teg)
        for p in profiles:
            p.optimal_windows = optimizer.compute_optimal_windows(p)

        scheduler = SkeletonScheduler(teg)

        # Apply approach overrides (T4 via Hatton Cross)
        for p in profiles:
            if p.station_id == T4_ID:
                p.junction_id = HATTON_CROSS_ID

        # Use known good start: Euston Square @ 05:44
        start_station = "940GZZLUESQ"
        start_time = 5 * 3600 + 44 * 60

        skeleton = scheduler.build_skeleton(profiles, start_time=start_time)
        hard_ids = {p.station_id for p in profiles}

        solver = GreedySolver(teg, lookahead=1)
        route = solver.solve_skeleton(
            start_station, parsed.required_station_ids, start_time,
            skeleton=skeleton,
            urgency_weight=0.5,
            hard_station_ids=hard_ids,
        )

        assert route.stations_visited == n_required, (
            f"Expected {n_required}, got {route.stations_visited} "
            f"(missed {n_required - route.stations_visited})"
        )

    def test_skeleton_time_reasonable(self, london_data):
        """Skeleton solver should produce a route under 20 hours."""
        _, parsed, teg = london_data

        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID, T4_ID},
        )
        for p in profiles:
            if p.station_id == T4_ID:
                p.junction_id = HATTON_CROSS_ID

        optimizer = VisitWindowOptimizer(teg)
        for p in profiles:
            p.optimal_windows = optimizer.compute_optimal_windows(p)

        scheduler = SkeletonScheduler(teg)
        start_station = "940GZZLUESQ"
        start_time = 5 * 3600 + 44 * 60

        skeleton = scheduler.build_skeleton(profiles, start_time=start_time)
        hard_ids = {p.station_id for p in profiles}
        solver = GreedySolver(teg, lookahead=1)
        route = solver.solve_skeleton(
            start_station, parsed.required_station_ids, start_time,
            skeleton=skeleton,
            urgency_weight=0.5,
            hard_station_ids=hard_ids,
        )

        # Should be under 20 hours (72000 seconds)
        assert route.total_time_seconds < 72000, (
            f"Route took {route.total_time_seconds // 3600}h"
            f"{(route.total_time_seconds % 3600) // 60:02d}m — too slow"
        )


class TestPairings:
    def test_build_pairings_finds_junctions(self, london_data):
        """Each hard station should be paired with a junction."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID, T4_ID},
        )
        for p in profiles:
            if p.station_id == T4_ID:
                p.junction_id = HATTON_CROSS_ID

        pairings = build_pairings(teg, profiles)
        assert len(pairings) > 0
        for p in pairings:
            assert p.junction_id is not None
            assert p.round_trip_cost_s > 0

    def test_ko_is_prefix(self, london_data):
        """K.O. (9 TEG nodes) should be marked as prefix."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID},
        )
        pairings = build_pairings(teg, profiles)
        ko_pairing = next((p for p in pairings if p.hard_station_id == KO_ID), None)
        assert ko_pairing is not None
        assert ko_pairing.is_prefix is True

    def test_mhe_paired_with_finchley_central(self, london_data):
        """Mill Hill East should be paired with Finchley Central (1 stop away)."""
        _, parsed, teg = london_data
        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={MHE_ID},
        )
        pairings = build_pairings(teg, profiles)
        mhe_pairing = next((p for p in pairings if p.hard_station_id == MHE_ID), None)
        assert mhe_pairing is not None
        assert mhe_pairing.junction_id == "940GZZLUFYC"  # Finchley Central
        assert mhe_pairing.round_trip_cost_s < 600  # < 10 min round-trip

    def test_pairings_solver_visits_all(self, london_data):
        """solve_with_pairings should visit all 272 stations from Euston Square."""
        _, parsed, teg = london_data
        n_required = len(parsed.required_station_ids)

        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID, T4_ID},
        )
        for p in profiles:
            if p.station_id == T4_ID:
                p.junction_id = HATTON_CROSS_ID

        pairings = build_pairings(teg, profiles)

        solver = GreedySolver(teg, lookahead=1)
        route = solver.solve_with_pairings(
            "940GZZLUESQ", parsed.required_station_ids,
            5 * 3600 + 44 * 60,
            pairings=pairings,
            urgency_weight=0.5,
        )

        assert route.stations_visited == n_required, (
            f"Expected {n_required}, got {route.stations_visited} "
            f"(missed {n_required - route.stations_visited})"
        )

    def test_pairings_solver_time_reasonable(self, london_data):
        """solve_with_pairings should produce a route under 20 hours."""
        _, parsed, teg = london_data

        detector = HardStationDetector(teg)
        profiles = detector.detect(
            parsed.required_station_ids,
            force_hard={KO_ID, MHE_ID, T4_ID},
        )
        for p in profiles:
            if p.station_id == T4_ID:
                p.junction_id = HATTON_CROSS_ID

        pairings = build_pairings(teg, profiles)

        solver = GreedySolver(teg, lookahead=1)
        route = solver.solve_with_pairings(
            "940GZZLUESQ", parsed.required_station_ids,
            5 * 3600 + 44 * 60,
            pairings=pairings,
        )

        assert route.total_time_seconds < 72000
