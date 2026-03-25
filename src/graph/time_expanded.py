"""Time-expanded graph: nodes are (station, time) pairs."""

from dataclasses import dataclass, field

import networkx as nx

from src.gtfs.parser import ParsedGTFS, WalkingTransfer


# Type alias for time-expanded nodes
TENode = tuple[str, int]  # (station_id, time_seconds)


@dataclass
class Route:
    """A solved route through the transit network."""

    visits: list[dict]  # [{station_id, station_name, arrival, departure, line, type}]
    total_time_seconds: int = 0
    stations_visited: int = 0
    walk_segments: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_time_seconds": self.total_time_seconds,
            "stations_visited": self.stations_visited,
            "route": self.visits,
            "walk_segments": self.walk_segments,
        }


def _format_time(seconds: int) -> str:
    """Format seconds since midnight as HH:MM.

    Wraps hours >= 24 back to 00:XX (GTFS uses 24+ for post-midnight service).
    """
    h = (seconds // 3600) % 24
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"


class TimeExpandedGraph:
    """Time-expanded graph where nodes are (station_id, time) pairs.

    Edge types:
    - transit: ride a train from one station to the next
    - wait: wait at a station for the next departure
    - walk: walk between nearby stations
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self._station_nodes: dict[str, list[TENode]] = {}  # station_id -> sorted time nodes
        self._station_names: dict[str, str] = {}
        self._station_coords: dict[str, tuple[float, float]] = {}  # station_id -> (lat, lon)

    @classmethod
    def from_gtfs(cls, parsed: ParsedGTFS) -> "TimeExpandedGraph":
        """Build a time-expanded graph from parsed GTFS data."""
        teg = cls()

        # Store station names and coordinates
        for sid, station in parsed.stations.items():
            teg._station_names[sid] = station.name
            teg._station_coords[sid] = (station.lat, station.lon)

        # Collect all time nodes per station from segments
        station_times: dict[str, set[int]] = {}

        for seg in parsed.segments:
            station_times.setdefault(seg.from_station_id, set()).add(seg.departure_time)
            station_times.setdefault(seg.to_station_id, set()).add(seg.arrival_time)

        # Add all nodes
        for sid, times in station_times.items():
            sorted_times = sorted(times)
            teg._station_nodes[sid] = [(sid, t) for t in sorted_times]
            for t in sorted_times:
                teg.graph.add_node((sid, t), station_id=sid, time=t)

        # Add transit arcs
        for seg in parsed.segments:
            from_node = (seg.from_station_id, seg.departure_time)
            to_node = (seg.to_station_id, seg.arrival_time)
            weight = seg.arrival_time - seg.departure_time

            # Keep the fastest transit arc if multiple exist
            existing = teg.graph.get_edge_data(from_node, to_node)
            if existing is None or weight < existing["weight"]:
                teg.graph.add_edge(
                    from_node, to_node,
                    weight=weight,
                    edge_type="transit",
                    route_name=seg.route_name,
                    trip_id=seg.trip_id,
                )

        # Add waiting arcs (connect consecutive times at same station)
        for sid, nodes in teg._station_nodes.items():
            for i in range(len(nodes) - 1):
                curr = nodes[i]
                nxt = nodes[i + 1]
                wait_time = nxt[1] - curr[1]
                teg.graph.add_edge(
                    curr, nxt,
                    weight=wait_time,
                    edge_type="wait",
                )

        # Add walking transfer arcs
        teg._add_walking_arcs(parsed.walking_transfers)

        return teg

    def _add_walking_arcs(self, transfers: list[WalkingTransfer]):
        """Add walking transfer edges between nearby stations."""
        for wt in transfers:
            from_nodes = self._station_nodes.get(wt.from_station_id, [])
            to_nodes = self._station_nodes.get(wt.to_station_id, [])

            if not from_nodes or not to_nodes:
                continue

            # For each departure time at the from_station, find the earliest
            # arrival time node at the to_station after walking
            to_times = [n[1] for n in to_nodes]

            for from_node in from_nodes:
                arrival_time = from_node[1] + wt.walk_time_seconds

                # Find the closest time node at destination >= arrival_time
                # Use the arrival time directly as a node if it exists,
                # otherwise snap to the next available departure
                import bisect
                idx = bisect.bisect_left(to_times, arrival_time)

                if idx < len(to_times):
                    to_node = to_nodes[idx]
                    total_weight = to_node[1] - from_node[1]

                    existing = self.graph.get_edge_data(from_node, to_node)
                    if existing is None or total_weight < existing["weight"]:
                        self.graph.add_edge(
                            from_node, to_node,
                            weight=total_weight,
                            edge_type="walk",
                            walk_seconds=wt.walk_time_seconds,
                            distance_m=wt.distance_meters,
                        )

    def earliest_arrival(self, from_node: TENode, to_station_id: str) -> tuple[TENode | None, int, list]:
        """Find earliest arrival at a station from a given node.

        Uses a custom Dijkstra that terminates as soon as any node at the
        target station is settled, avoiding exploring the full graph.

        Returns:
            (destination_node, travel_time, path) or (None, -1, []) if unreachable.
        """
        import heapq

        target_nodes = set(self._station_nodes.get(to_station_id, []))
        if not target_nodes:
            return None, -1, []

        if from_node not in self.graph:
            return None, -1, []

        # Custom Dijkstra with multi-target early termination
        dist = {from_node: 0}
        prev = {from_node: None}
        heap = [(0, id(from_node), from_node)]
        visited = set()

        while heap:
            d, _, u = heapq.heappop(heap)
            if u in visited:
                continue
            visited.add(u)

            # Early termination: found shortest path to target station
            if u in target_nodes:
                # Reconstruct path
                path = []
                node = u
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                return u, int(d), path

            for v, edge_data in self.graph[u].items():
                w = edge_data.get("weight", 1)
                new_dist = d + w
                if v not in dist or new_dist < dist[v]:
                    dist[v] = new_dist
                    prev[v] = u
                    heapq.heappush(heap, (new_dist, id(v), v))

        return None, -1, []

    def static_distances_from(self, station_id: str) -> dict[str, float]:
        """Get approximate travel times from a station using min edge weights.

        Used for fast lookahead estimation — avoids full TEG Dijkstra.
        Returns dict of station_id -> min_seconds (lower bound, not timetable-exact).
        """
        if not hasattr(self, "_static_apsp"):
            self._build_static_cache()
        return self._static_apsp.get(station_id, {})

    def earliest_arrival_nearest(
        self, from_node: TENode, target_stations: set[str]
    ) -> tuple[str | None, TENode | None, int, list[TENode]]:
        """Find the nearest station in target_stations via early-termination Dijkstra.

        Unlike earliest_arrivals_from (which explores the entire graph), this
        stops the moment it settles ANY node belonging to a target station.
        Typical speedup: 100-500x for dense transit graphs.

        Returns:
            (station_id, arrival_node, travel_time, path) or (None, None, -1, []).
        """
        import heapq

        if from_node not in self.graph:
            return None, None, -1, []

        dist = {from_node: 0}
        prev: dict[TENode, TENode | None] = {from_node: None}
        heap = [(0, from_node[1], from_node[0], from_node)]
        visited: set[TENode] = set()

        while heap:
            d, _, _, u = heapq.heappop(heap)
            if u in visited:
                continue
            visited.add(u)

            # Check if this settled node belongs to a target station
            sid = u[0]
            if sid in target_stations:
                path: list[TENode] = []
                node: TENode | None = u
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                return sid, u, int(d), path

            for v, edge_data in self.graph[u].items():
                w = edge_data.get("weight", 1)
                new_dist = d + w
                if v not in dist or new_dist < dist[v]:
                    dist[v] = new_dist
                    prev[v] = u
                    heapq.heappush(heap, (new_dist, v[1], v[0], v))

        return None, None, -1, []

    def earliest_arrival_k_nearest(
        self, from_node: TENode, target_stations: set[str], k: int = 5
    ) -> list[tuple[str, TENode, int, list[TENode]]]:
        """Find the k nearest stations in target_stations via Dijkstra.

        Terminates after settling k distinct target stations.
        Returns list of (station_id, arrival_node, travel_time, path).
        """
        import heapq

        if from_node not in self.graph:
            return []

        dist = {from_node: 0}
        prev: dict[TENode, TENode | None] = {from_node: None}
        heap = [(0, from_node[1], from_node[0], from_node)]
        visited: set[TENode] = set()
        found: list[tuple[str, TENode, int, list[TENode]]] = []
        found_stations: set[str] = set()

        while heap and len(found) < k:
            d, _, _, u = heapq.heappop(heap)
            if u in visited:
                continue
            visited.add(u)

            sid = u[0]
            if sid in target_stations and sid not in found_stations:
                found_stations.add(sid)
                path: list[TENode] = []
                node: TENode | None = u
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                found.append((sid, u, int(d), path))
                if len(found) >= k:
                    break

            for v, edge_data in self.graph[u].items():
                w = edge_data.get("weight", 1)
                new_dist = d + w
                if v not in dist or new_dist < dist[v]:
                    dist[v] = new_dist
                    prev[v] = u
                    heapq.heappush(heap, (new_dist, v[1], v[0], v))

        return found

    def earliest_arrivals_from(
        self, from_node: TENode, station_ids: set[str]
    ) -> dict[str, tuple[TENode, int, list]]:
        """Find earliest arrival times from a node to multiple stations.

        Returns:
            Dict of station_id -> (arrival_node, travel_time, path) for reachable stations.
        """
        try:
            lengths, paths = nx.single_source_dijkstra(
                self.graph, from_node, weight="weight"
            )
        except nx.NodeNotFound:
            return {}

        results = {}
        for sid in station_ids:
            nodes = self._station_nodes.get(sid, [])
            best_node = None
            best_time = float("inf")
            for node in nodes:
                if node in lengths and lengths[node] < best_time:
                    best_time = lengths[node]
                    best_node = node
            if best_node is not None:
                results[sid] = (best_node, int(best_time), paths[best_node])

        return results

    def get_start_nodes(self, station_id: str, earliest_time: int = 0) -> list[TENode]:
        """Get available starting nodes at a station from a given time."""
        nodes = self._station_nodes.get(station_id, [])
        return [n for n in nodes if n[1] >= earliest_time]

    def reconstruct_route(self, path: list[TENode], parsed: ParsedGTFS) -> Route:
        """Convert a path of time-expanded nodes into a human-readable Route."""
        visits = []
        walk_segments = []
        visited_stations = set()

        for i, node in enumerate(path):
            sid, time = node
            station_name = self._station_names.get(sid, sid)

            # Determine edge type from previous node
            if i > 0:
                edge_data = self.graph.get_edge_data(path[i - 1], node) or {}
                edge_type = edge_data.get("edge_type", "unknown")
                route_name = edge_data.get("route_name", "")

                if edge_type == "walk":
                    walk_segments.append({
                        "from_station": self._station_names.get(path[i - 1][0], path[i - 1][0]),
                        "to_station": station_name,
                        "walk_seconds": edge_data.get("walk_seconds", 0),
                        "distance_m": edge_data.get("distance_m", 0),
                    })
            else:
                edge_type = "start"
                route_name = ""

            if sid not in visited_stations or edge_type not in ("wait", "walk"):
                # Only count transit/start arrivals toward stations_visited
                visited_stations.add(sid)  # count all arrivals, including walks
                # Determine departure time (next edge's departure or same as arrival)
                dep_time = time
                if i < len(path) - 1:
                    dep_time = path[i + 1][1] if path[i + 1][0] == sid else time

                lat, lon = self._station_coords.get(sid, (0, 0))
                visits.append({
                    "station_id": sid,
                    "station_name": station_name,
                    "arrival": _format_time(time),
                    "departure": _format_time(dep_time),
                    "line": route_name,
                    "type": edge_type,
                    "lat": lat,
                    "lon": lon,
                })

        if not path:
            return Route(visits=[], total_time_seconds=0, stations_visited=0)

        total_time = path[-1][1] - path[0][1]
        return Route(
            visits=visits,
            total_time_seconds=total_time,
            stations_visited=len(visited_stations),
            walk_segments=walk_segments,
        )

    def _build_static_cache(self):
        """Build a collapsed station-level graph and all-pairs distance table."""
        import collections
        g = nx.DiGraph()
        min_weights: dict[tuple, int] = collections.defaultdict(lambda: 10**9)
        for u, v, data in self.graph.edges(data=True):
            su, sv = u[0], v[0]
            if su != sv:
                w = data.get("weight", 0)
                if w < min_weights[(su, sv)]:
                    min_weights[(su, sv)] = w
        for (su, sv), w in min_weights.items():
            g.add_edge(su, sv, weight=w)
        self._static_graph = g
        # All-pairs shortest paths on the static graph (computed once, O(N²))
        self._static_apsp: dict[str, dict[str, float]] = dict(
            nx.all_pairs_dijkstra_path_length(g, weight="weight")
        )

    def static_dist(self, from_station: str, to_station: str) -> float:
        """Fast O(1) static distance lookup between two stations."""
        if not hasattr(self, "_static_apsp"):
            self._build_static_cache()
        return self._static_apsp.get(from_station, {}).get(to_station, float("inf"))

    @property
    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    @property
    def station_count(self) -> int:
        return len(self._station_nodes)
