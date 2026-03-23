"""Tests for static and time-expanded graph construction."""

from datetime import date

import pytest

from src.config import load_config
from src.graph.network import TransitNetwork
from src.graph.time_expanded import TimeExpandedGraph
from src.gtfs.parser import GTFSParser


TOY_CONFIG = "configs/toy.yaml"


@pytest.fixture
def toy_parsed():
    cfg = load_config(TOY_CONFIG)
    parser = GTFSParser(cfg)
    return parser.parse(target_date=date(2024, 3, 15))


@pytest.fixture
def static_network(toy_parsed):
    return TransitNetwork.from_gtfs(toy_parsed)


@pytest.fixture
def time_expanded(toy_parsed):
    return TimeExpandedGraph.from_gtfs(toy_parsed)


class TestStaticNetwork:
    def test_station_count(self, static_network):
        assert static_network.station_count == 10

    def test_has_edges(self, static_network):
        assert static_network.edge_count > 0

    def test_edge_weights_positive(self, static_network):
        for u, v, data in static_network.graph.edges(data=True):
            assert data["weight"] > 0

    def test_shortest_path_exists(self, static_network):
        """S1 should be reachable from itself."""
        path, dist = static_network.shortest_path("S1", "S1")
        assert dist == 0 or path == ["S1"]

    def test_shortest_path_between_connected(self, static_network):
        """Should find a path between S1 and S3 (connected via Blue line)."""
        path, dist = static_network.shortest_path("S1", "S3")
        assert len(path) > 0
        assert dist > 0

    def test_all_pairs_shortest(self, static_network, toy_parsed):
        distances = static_network.all_pairs_shortest(toy_parsed.required_station_ids)
        assert "S1" in distances
        assert len(distances) > 0

    def test_get_neighbors(self, static_network):
        neighbors = static_network.get_neighbors("S1")
        assert len(neighbors) > 0
        for nbr, weight in neighbors:
            assert isinstance(nbr, str)
            assert weight > 0


class TestTimeExpandedGraph:
    def test_has_nodes(self, time_expanded):
        assert time_expanded.node_count > 0

    def test_has_edges(self, time_expanded):
        assert time_expanded.edge_count > 0

    def test_node_count_reasonable(self, time_expanded):
        """Toy network with 10 stations and ~50 stop_times should have < 500 nodes."""
        assert time_expanded.node_count < 500

    def test_station_count(self, time_expanded):
        assert time_expanded.station_count == 10

    def test_earliest_arrival(self, time_expanded):
        """Should find a path from S1 at 06:00 to S3."""
        start_nodes = time_expanded.get_start_nodes("S1", 6 * 3600)
        assert len(start_nodes) > 0
        node, travel, path = time_expanded.earliest_arrival(start_nodes[0], "S3")
        assert node is not None
        assert travel > 0
        assert len(path) > 0

    def test_earliest_arrivals_from(self, time_expanded):
        """Should find arrivals to multiple stations."""
        start_nodes = time_expanded.get_start_nodes("S1", 6 * 3600)
        arrivals = time_expanded.earliest_arrivals_from(start_nodes[0], {"S3", "S4", "S5"})
        assert len(arrivals) > 0

    def test_edge_types(self, time_expanded):
        """Graph should contain transit, wait, and possibly walk edges."""
        edge_types = set()
        for u, v, data in time_expanded.graph.edges(data=True):
            edge_types.add(data["edge_type"])
        assert "transit" in edge_types
        assert "wait" in edge_types

    def test_time_monotonic_on_edges(self, time_expanded):
        """All edges should go forward in time."""
        for (u_sid, u_time), (v_sid, v_time), data in time_expanded.graph.edges(data=True):
            assert v_time >= u_time, f"Edge goes backwards: ({u_sid},{u_time}) -> ({v_sid},{v_time})"
