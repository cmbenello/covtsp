"""GTFS parser: reads GTFS files and builds graph-ready data structures."""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import KDTree

from src.config import CityConfig

from .calendar import get_active_services


@dataclass
class Station:
    """A physical station (may contain multiple GTFS stops/platforms)."""

    station_id: str
    name: str
    lat: float
    lon: float
    child_stop_ids: list[str] = field(default_factory=list)


@dataclass
class TripSegment:
    """A single segment of a trip: one stop to the next."""

    trip_id: str
    route_id: str
    route_name: str
    from_station_id: str
    to_station_id: str
    departure_time: int  # seconds since midnight
    arrival_time: int  # seconds since midnight


@dataclass
class WalkingTransfer:
    """A walking connection between two nearby stations."""

    from_station_id: str
    to_station_id: str
    walk_time_seconds: int
    distance_meters: float


@dataclass
class ParsedGTFS:
    """Fully parsed GTFS data ready for graph construction."""

    stations: dict[str, Station]
    segments: list[TripSegment]
    walking_transfers: list[WalkingTransfer]
    required_station_ids: set[str]


def _parse_time(time_str: str) -> int:
    """Parse GTFS time string (HH:MM:SS) to seconds since midnight.

    Handles times > 24:00:00 for post-midnight service.
    """
    parts = time_str.strip().split(":")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    return h * 3600 + m * 60 + s


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two lat/lon points in meters."""
    R = 6_371_000  # Earth radius in meters
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(R * 2 * np.arcsin(np.sqrt(a)))


class GTFSParser:
    """Parse GTFS data and produce graph-ready structures."""

    def __init__(self, config: CityConfig):
        self.config = config
        self.gtfs_dir = config.data_dir

    def parse(self, target_date=None) -> ParsedGTFS:
        """Parse GTFS files and return structured data.

        Args:
            target_date: If provided, filter trips to services active on this date.
                        If None, include all trips.
        """
        stations = self._parse_stations()
        segments = self._parse_segments(stations, target_date)
        walking = self._compute_walking_transfers(stations)

        required = {
            sid for sid in stations
            if sid not in self.config.excluded_stations
        }

        return ParsedGTFS(
            stations=stations,
            segments=segments,
            walking_transfers=walking,
            required_station_ids=required,
        )

    def _parse_stations(self) -> dict[str, Station]:
        """Parse stops.txt, group by parent_station, filter by route_type."""
        stops = pd.read_csv(
            self.gtfs_dir / "stops.txt",
            dtype={"stop_id": str, "parent_station": str},
        )
        routes = pd.read_csv(
            self.gtfs_dir / "routes.txt",
            dtype={"route_id": str},
        )
        trips = pd.read_csv(
            self.gtfs_dir / "trips.txt",
            dtype={"trip_id": str, "route_id": str},
        )
        stop_times = pd.read_csv(
            self.gtfs_dir / "stop_times.txt",
            dtype={"trip_id": str, "stop_id": str},
        )

        # Filter routes by type
        valid_routes = set(
            routes[routes["route_type"].isin(self.config.route_type_filter)]["route_id"]
        )
        valid_trips = set(trips[trips["route_id"].isin(valid_routes)]["trip_id"])
        served_stop_ids = set(stop_times[stop_times["trip_id"].isin(valid_trips)]["stop_id"])

        # Build station groups
        stations: dict[str, Station] = {}
        stop_to_station: dict[str, str] = {}

        # Pre-pass: find parent stations that have at least one served child
        served_parents = set()
        for _, stop in stops.iterrows():
            stop_id = str(stop["stop_id"])
            if stop_id in served_stop_ids:
                parent_id = str(stop.get("parent_station", "")) if pd.notna(stop.get("parent_station")) else ""
                if parent_id:
                    served_parents.add(parent_id)

        for _, stop in stops.iterrows():
            stop_id = str(stop["stop_id"])
            parent_id = str(stop.get("parent_station", "")) if pd.notna(stop.get("parent_station")) else ""
            station_id = parent_id if parent_id else stop_id

            # Only include stops that are served or belong to a served station group
            if stop_id not in served_stop_ids and station_id not in served_parents:
                continue

            if station_id not in stations:
                # Use the parent stop's info if available, otherwise use this stop's
                if parent_id and parent_id in stops["stop_id"].values:
                    parent_row = stops[stops["stop_id"] == parent_id].iloc[0]
                    stations[station_id] = Station(
                        station_id=station_id,
                        name=str(parent_row.get("stop_name", station_id)),
                        lat=float(parent_row["stop_lat"]),
                        lon=float(parent_row["stop_lon"]),
                    )
                else:
                    stations[station_id] = Station(
                        station_id=station_id,
                        name=str(stop.get("stop_name", stop_id)),
                        lat=float(stop["stop_lat"]),
                        lon=float(stop["stop_lon"]),
                    )

            if stop_id not in stations[station_id].child_stop_ids:
                stations[station_id].child_stop_ids.append(stop_id)
            stop_to_station[stop_id] = station_id

        # Store mapping for segment parsing
        self._stop_to_station = stop_to_station
        self._valid_trips = valid_trips

        return stations

    def _parse_segments(self, stations: dict[str, Station], target_date=None) -> list[TripSegment]:
        """Parse stop_times.txt into trip segments between stations."""
        stop_times = pd.read_csv(
            self.gtfs_dir / "stop_times.txt",
            dtype={"trip_id": str, "stop_id": str},
        )
        trips = pd.read_csv(
            self.gtfs_dir / "trips.txt",
            dtype={"trip_id": str, "route_id": str, "service_id": str},
        )
        routes = pd.read_csv(
            self.gtfs_dir / "routes.txt",
            dtype={"route_id": str},
        )

        # Filter by active services if date provided
        if target_date is not None:
            active_services = get_active_services(self.gtfs_dir, target_date)
            valid_trip_ids = set(
                trips[trips["service_id"].isin(active_services)]["trip_id"]
            ) & self._valid_trips
        else:
            valid_trip_ids = self._valid_trips

        # Build route name lookup
        route_names = dict(zip(routes["route_id"], routes.get("route_short_name", routes["route_id"])))
        trip_route = dict(zip(trips["trip_id"], trips["route_id"]))

        # Filter and sort stop_times
        st = stop_times[stop_times["trip_id"].isin(valid_trip_ids)].copy()
        st = st.sort_values(["trip_id", "stop_sequence"])

        segments = []
        for trip_id, group in st.groupby("trip_id"):
            rows = group.itertuples()
            prev = next(rows, None)
            if prev is None:
                continue

            route_id = trip_route.get(str(trip_id), "")
            route_name = str(route_names.get(route_id, route_id))

            for curr in rows:
                from_stop = str(prev.stop_id)
                to_stop = str(curr.stop_id)

                from_station = self._stop_to_station.get(from_stop)
                to_station = self._stop_to_station.get(to_stop)

                if from_station and to_station:
                    dep = _parse_time(str(prev.departure_time))
                    arr = _parse_time(str(curr.arrival_time))

                    if arr > dep:  # valid segment
                        segments.append(TripSegment(
                            trip_id=str(trip_id),
                            route_id=route_id,
                            route_name=route_name,
                            from_station_id=from_station,
                            to_station_id=to_station,
                            departure_time=dep,
                            arrival_time=arr,
                        ))
                prev = curr

        return segments

    def _compute_walking_transfers(self, stations: dict[str, Station]) -> list[WalkingTransfer]:
        """Compute walking transfers between nearby stations using KD-tree."""
        if not stations:
            return []

        station_list = list(stations.values())
        coords = np.array([[s.lat, s.lon] for s in station_list])

        # Convert max walk distance to approximate degrees for KD-tree query
        # 1 degree lat ≈ 111km
        max_deg = self.config.max_walk_distance_m / 111_000

        tree = KDTree(coords)
        pairs = tree.query_pairs(max_deg)

        transfers = []
        walk_speed_ms = self.config.walking_speed_kmh * 1000 / 3600  # m/s

        for i, j in pairs:
            s1, s2 = station_list[i], station_list[j]
            dist = _haversine_meters(s1.lat, s1.lon, s2.lat, s2.lon)

            if dist <= self.config.max_walk_distance_m:
                walk_time = int(dist / walk_speed_ms)
                transfers.append(WalkingTransfer(
                    from_station_id=s1.station_id,
                    to_station_id=s2.station_id,
                    walk_time_seconds=walk_time,
                    distance_meters=dist,
                ))
                transfers.append(WalkingTransfer(
                    from_station_id=s2.station_id,
                    to_station_id=s1.station_id,
                    walk_time_seconds=walk_time,
                    distance_meters=dist,
                ))

        return transfers
