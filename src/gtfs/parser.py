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

        if self.config.station_count > 0 and len(required) != self.config.station_count:
            import logging
            logging.getLogger(__name__).warning(
                f"Expected {self.config.station_count} stations, got {len(required)}. "
                f"Check merge_stations and excluded_stations config."
            )

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

        # Fix stations with clearly wrong coordinates (use child platform average)
        self._fix_bad_coordinates(stations, stops)

        # Auto-merge orphan stops by normalized name
        # Some GTFS feeds have stops without parent_station that duplicate existing stations
        stations, stop_to_station = self._merge_orphan_stations(stations, stop_to_station)
        stations, stop_to_station = self._merge_station_pairs(stations, stop_to_station)

        # Store mapping for segment parsing
        self._stop_to_station = stop_to_station
        self._valid_trips = valid_trips

        return stations

    @staticmethod
    def _fix_bad_coordinates(stations: dict[str, "Station"], stops_df) -> None:
        """Fix stations with clearly wrong coordinates by averaging child platforms.

        Some GTFS feeds have parent stations with coordinates far from the actual
        platforms (e.g., Belsize Park in the London feed has Irish coordinates).
        Detect by comparing parent coords against child platform coords.
        """
        import logging
        logger = logging.getLogger(__name__)

        for sid, station in stations.items():
            # Get child platforms (exclude the parent station itself)
            child_ids = [c for c in station.child_stop_ids if c != sid]
            if not child_ids:
                continue

            child_rows = stops_df[stops_df["stop_id"].isin(child_ids)]
            if child_rows.empty:
                continue

            child_lats = child_rows["stop_lat"].astype(float).values
            child_lons = child_rows["stop_lon"].astype(float).values

            avg_lat = float(child_lats.mean())
            avg_lon = float(child_lons.mean())

            # If parent is > 0.1 degrees (~11km) from child average, it's wrong
            if abs(station.lat - avg_lat) > 0.1 or abs(station.lon - avg_lon) > 0.1:
                logger.info(
                    f"Fixing bad coordinates for {station.name}: "
                    f"({station.lat:.4f}, {station.lon:.4f}) -> ({avg_lat:.4f}, {avg_lon:.4f})"
                )
                station.lat = avg_lat
                station.lon = avg_lon

    @staticmethod
    def _normalize_station_name(name: str) -> str:
        """Normalize a station name for fuzzy matching."""
        name = name.lower().strip()
        for suffix in [" underground station", " rail station", " dlr station", " station", " stn"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
        # Normalize common variations
        name = name.replace("'", "'").replace("'", "'").replace("&", "and")
        return name

    def _merge_orphan_stations(
        self,
        stations: dict[str, Station],
        stop_to_station: dict[str, str],
    ) -> tuple[dict[str, Station], dict[str, str]]:
        """Merge orphan stops (no parent_station) into matching parent stations by name.

        An orphan is a station whose station_id equals its stop_id (it was never
        grouped under a parent). If its normalized name matches an existing parent
        station, merge its child_stop_ids into that parent and update the mapping.
        """
        # Build normalized name -> station_id index for parent stations
        parent_stations = {}
        orphan_ids = []

        for sid, station in stations.items():
            # A station is a "parent" if it has children from different stop_ids
            # or if its station_id doesn't match any of its child_stop_ids
            is_orphan = len(station.child_stop_ids) == 1 and station.child_stop_ids[0] == sid
            if is_orphan:
                orphan_ids.append(sid)
            else:
                norm = self._normalize_station_name(station.name)
                parent_stations[norm] = sid

        merged_count = 0
        for orphan_id in orphan_ids:
            orphan = stations[orphan_id]
            norm = self._normalize_station_name(orphan.name)

            if norm in parent_stations:
                target_id = parent_stations[norm]
                target = stations[target_id]

                # Merge child stops
                for child in orphan.child_stop_ids:
                    if child not in target.child_stop_ids:
                        target.child_stop_ids.append(child)
                    stop_to_station[child] = target_id

                # Remove orphan
                del stations[orphan_id]
                merged_count += 1

        if merged_count > 0:
            import logging
            logging.getLogger(__name__).info(f"Merged {merged_count} orphan stops by name match")

        return stations, stop_to_station

    def _merge_station_pairs(
        self,
        stations: dict[str, Station],
        stop_to_station: dict[str, str],
    ) -> tuple[dict[str, Station], dict[str, str]]:
        """Merge explicitly configured station pairs.

        Each pair in config.merge_stations is [source_id, target_id].
        All child stops of source are moved to target, and source is deleted.
        Handles both parent stations and orphan stops as source.
        """
        import logging
        logger = logging.getLogger(__name__)

        for pair in self.config.merge_stations:
            source_id, target_id = pair[0], pair[1]

            if target_id not in stations:
                logger.warning(f"Merge target {target_id} not found in stations")
                continue

            if source_id not in stations:
                # Source might be an orphan stop already mapped via stop_to_station
                if source_id in stop_to_station:
                    stop_to_station[source_id] = target_id
                    continue
                logger.warning(f"Merge source {source_id} not found in stations")
                continue

            source = stations[source_id]
            target = stations[target_id]

            for child in source.child_stop_ids:
                if child not in target.child_stop_ids:
                    target.child_stop_ids.append(child)
                stop_to_station[child] = target_id

            stop_to_station[source_id] = target_id
            del stations[source_id]
            logger.info(f"Merged station {source.name} ({source_id}) -> {target.name} ({target_id})")

        return stations, stop_to_station

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
        """Compute walking/running transfers between nearby stations.

        Uses KD-tree to find candidate pairs within max_walk_distance_m.
        If use_google_walking is enabled, fetches real walking times from
        the Google Maps Distance Matrix API (with disk caching).
        Falls back to Haversine-based estimates if API is unavailable.

        In run mode, uses distance-dependent speed (sprints are faster than
        sustained runs) and considers larger distances.
        """
        if not stations:
            return []

        station_list = list(stations.values())
        coords = np.array([[s.lat, s.lon] for s in station_list])

        # Convert max walk distance to approximate degrees for KD-tree query
        # 1 degree lat ≈ 111km
        max_deg = self.config.max_walk_distance_m / 111_000

        tree = KDTree(coords)
        pairs = tree.query_pairs(max_deg)

        # Build candidate pairs with Haversine distances
        candidate_pairs = []
        for i, j in pairs:
            s1, s2 = station_list[i], station_list[j]
            dist = _haversine_meters(s1.lat, s1.lon, s2.lat, s2.lon)
            if dist <= self.config.max_walk_distance_m:
                candidate_pairs.append((s1, s2, dist))

        # Try Google Maps API if configured
        if self.config.use_google_walking:
            from src.gtfs.walking import compute_google_walking_transfers

            cache_path = self.config.data_dir / "walking_cache.json"
            result = compute_google_walking_transfers(candidate_pairs, cache_path)
            if result is not None and len(result) > 0:
                # Scale walking times for run mode
                if self.config.movement_mode == "run":
                    result = self._scale_transfers_for_running(result)
                return result

        # Fallback: Haversine-based times using effective speed
        # In run mode, apply 1.4x routing factor: Haversine is straight-line
        # but real pedestrian routes are ~40% longer due to streets/crossings
        routing_factor = 1.4 if self.config.movement_mode == "run" else 1.0
        transfers = []

        for s1, s2, dist in candidate_pairs:
            routed_dist = dist * routing_factor
            speed_kmh = self.config.effective_speed_for_distance(routed_dist)
            speed_ms = speed_kmh * 1000 / 3600
            transfer_time = int(routed_dist / speed_ms)
            transfers.append(WalkingTransfer(
                from_station_id=s1.station_id,
                to_station_id=s2.station_id,
                walk_time_seconds=transfer_time,
                distance_meters=dist,
            ))
            transfers.append(WalkingTransfer(
                from_station_id=s2.station_id,
                to_station_id=s1.station_id,
                walk_time_seconds=transfer_time,
                distance_meters=dist,
            ))

        return transfers

    def _scale_transfers_for_running(self, transfers: list[WalkingTransfer]) -> list[WalkingTransfer]:
        """Scale Google Maps walking times to running times.

        Google Maps returns walking times at ~5 km/h. We scale each transfer
        by the ratio of walking speed to distance-dependent running speed.
        """
        scaled = []
        for t in transfers:
            run_speed = self.config.effective_speed_for_distance(t.distance_meters)
            scale = self.config.walking_speed_kmh / run_speed
            scaled.append(WalkingTransfer(
                from_station_id=t.from_station_id,
                to_station_id=t.to_station_id,
                walk_time_seconds=max(1, int(t.walk_time_seconds * scale)),
                distance_meters=t.distance_meters,
            ))
        return scaled
