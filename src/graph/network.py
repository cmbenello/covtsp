"""Static transit network graph with minimum travel times between stations."""

from dataclasses import dataclass

import networkx as nx
import numpy as np

from src.gtfs.parser import ParsedGTFS, _haversine_meters


@dataclass
class StationInfo:
    """Metadata for a station node in the static graph."""

    station_id: str
    name: str
    lat: float
    lon: float


class TransitNetwork:
    """Static weighted graph of a transit network.

    Nodes are station IDs. Edge weights are minimum travel times (seconds)
    across all observed trips. Used for LP relaxation lower bound and
    as a baseline for the greedy solver.
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self._stations: dict[str, StationInfo] = {}

    @classmethod
    def from_gtfs(cls, parsed: ParsedGTFS) -> "TransitNetwork":
        """Build a static network from parsed GTFS data.

        Edge weight = minimum observed travel time between each station pair.
        """
        net = cls()

        # Add stations
        for sid, station in parsed.stations.items():
            net.add_station(sid, station.name, station.lat, station.lon)

        # Add transit edges (min travel time per pair)
        min_times: dict[tuple[str, str], int] = {}
        for seg in parsed.segments:
            key = (seg.from_station_id, seg.to_station_id)
            travel = seg.arrival_time - seg.departure_time
            if key not in min_times or travel < min_times[key]:
                min_times[key] = travel

        for (from_id, to_id), weight in min_times.items():
            net.add_edge(from_id, to_id, weight, edge_type="transit")

        # Add walking edges
        for wt in parsed.walking_transfers:
            existing = net.graph.get_edge_data(wt.from_station_id, wt.to_station_id)
            if existing is None or wt.walk_time_seconds < existing["weight"]:
                net.add_edge(
                    wt.from_station_id,
                    wt.to_station_id,
                    wt.walk_time_seconds,
                    edge_type="walk",
                )

        return net

    def add_station(self, station_id: str, name: str, lat: float, lon: float):
        """Add a station node."""
        self._stations[station_id] = StationInfo(station_id, name, lat, lon)
        self.graph.add_node(station_id, name=name, lat=lat, lon=lon)

    def add_edge(self, from_id: str, to_id: str, weight: int, edge_type: str = "transit"):
        """Add or update a directed edge with travel time weight."""
        self.graph.add_edge(from_id, to_id, weight=weight, edge_type=edge_type)

    def get_neighbors(self, station_id: str) -> list[tuple[str, int]]:
        """Get neighbors of a station with travel times."""
        return [
            (nbr, data["weight"])
            for nbr, data in self.graph[station_id].items()
        ]

    def travel_time(self, from_id: str, to_id: str) -> int | None:
        """Get minimum travel time between two stations, or None if not connected."""
        data = self.graph.get_edge_data(from_id, to_id)
        return data["weight"] if data else None

    def shortest_path(self, from_id: str, to_id: str) -> tuple[list[str], int]:
        """Find shortest path by travel time. Returns (path, total_seconds)."""
        try:
            path = nx.shortest_path(self.graph, from_id, to_id, weight="weight")
            total = nx.shortest_path_length(self.graph, from_id, to_id, weight="weight")
            return path, int(total)
        except nx.NetworkXNoPath:
            return [], -1

    def all_pairs_shortest(self, station_ids: set[str] | None = None) -> dict[str, dict[str, int]]:
        """Compute shortest path lengths between all pairs of stations.

        Args:
            station_ids: If provided, only compute for these stations.
                        Otherwise, use all stations in the graph.

        Returns:
            Nested dict: distances[from_id][to_id] = seconds
        """
        nodes = station_ids or set(self.graph.nodes)
        lengths = dict(nx.all_pairs_dijkstra_path_length(self.graph, weight="weight"))
        return {
            src: {dst: int(dist) for dst, dist in dists.items() if dst in nodes}
            for src, dists in lengths.items()
            if src in nodes
        }

    @property
    def stations(self) -> dict[str, StationInfo]:
        return self._stations

    @property
    def station_count(self) -> int:
        return len(self._stations)

    @property
    def edge_count(self) -> int:
        return self.graph.number_of_edges()
