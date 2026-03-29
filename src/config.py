"""City configuration loader for transit optimization."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class TimeWindow:
    """Operating time window for the solver."""

    start: str = "05:00"  # HH:MM
    end: str = "01:00"  # HH:MM (next day if < start)

    @property
    def start_seconds(self) -> int:
        h, m = map(int, self.start.split(":"))
        return h * 3600 + m * 60

    @property
    def end_seconds(self) -> int:
        h, m = map(int, self.end.split(":"))
        total = h * 3600 + m * 60
        if total <= self.start_seconds:
            total += 24 * 3600  # next day
        return total


@dataclass
class HardStationOverride:
    """Manual override for a hard station."""

    station_id: str
    force_hard: bool = False
    preferred_window: Optional[str] = None  # "morning", "evening", or None
    approach_via: Optional[str] = None  # junction station ID


@dataclass
class HardStationConfig:
    """Configuration for hard station detection and scheduling."""

    auto_detect: bool = True
    hardness_threshold: Optional[float] = None  # None = auto (90th percentile)
    overrides: list[HardStationOverride] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


@dataclass
class CityConfig:
    """Configuration for a single city's transit optimization."""

    city_name: str
    gtfs_url: str
    gtfs_path: str  # relative to data/ dir
    station_count: int  # expected number of required stations
    route_type_filter: list[int] = field(default_factory=lambda: [1])  # 1 = metro
    walking_speed_kmh: float = 5.0
    max_walk_distance_m: float = 500.0
    start_station: Optional[str] = None
    time_window: TimeWindow = field(default_factory=TimeWindow)
    excluded_stations: list[str] = field(default_factory=list)
    merge_stations: list[list[str]] = field(default_factory=list)  # [source, target] pairs to merge
    use_google_walking: bool = False
    running_speed_kmh: float = 10.0
    movement_mode: str = "walk"  # "walk" or "run"
    hard_stations: HardStationConfig = field(default_factory=HardStationConfig)
    # Transit modes available for travel (default: same as route_type_filter).
    # Set wider than route_type_filter to allow using buses/DLR/trams for
    # connections without requiring visiting their stops.
    # e.g. route_type_filter=[1] (Underground required), transit_route_types=[0,1,2,3] (use everything)
    transit_route_types: list[int] | None = None  # None = same as route_type_filter

    @property
    def data_dir(self) -> Path:
        return Path("data") / self.gtfs_path

    @property
    def effective_speed_kmh(self) -> float:
        """Base effective speed for the configured movement mode."""
        if self.movement_mode == "run":
            return self.running_speed_kmh
        return self.walking_speed_kmh

    def effective_speed_for_distance(self, distance_m: float) -> float:
        """Distance-dependent speed in km/h.

        Base speed is treated as 10k race pace. Piecewise model:
          - <500m:      1.15x base (short sprint, faster than 10k pace)
          - 500m-10km:  1.0x base  (10k race pace — this is what base means)
          - >10km:      0.9x base  (slight fatigue for very long legs)

        In walk mode, returns flat walking speed regardless of distance.
        """
        if self.movement_mode != "run":
            return self.walking_speed_kmh
        base = self.running_speed_kmh
        if distance_m < 500:
            return base * 1.15
        elif distance_m < 10000:
            return base * 1.0
        else:
            return base * 0.9


def load_config(config_path: str | Path) -> CityConfig:
    """Load a city config from a YAML file."""
    config_path = Path(config_path)
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    time_window = TimeWindow()
    if "time_window" in raw:
        tw = raw.pop("time_window")
        time_window = TimeWindow(start=tw.get("start", "05:00"), end=tw.get("end", "01:00"))

    # Parse hard_stations config
    hard_stations = HardStationConfig()
    if "hard_stations" in raw:
        hs = raw.pop("hard_stations")
        overrides = []
        for ov in hs.get("overrides", []):
            overrides.append(HardStationOverride(
                station_id=ov["station_id"],
                force_hard=ov.get("force_hard", False),
                preferred_window=ov.get("preferred_window"),
                approach_via=ov.get("approach_via"),
            ))
        hard_stations = HardStationConfig(
            auto_detect=hs.get("auto_detect", True),
            hardness_threshold=hs.get("hardness_threshold"),
            overrides=overrides,
            exclude=hs.get("exclude", []),
        )

    return CityConfig(
        city_name=raw["city_name"],
        gtfs_url=raw["gtfs_url"],
        gtfs_path=raw["gtfs_path"],
        station_count=raw.get("station_count", 0),
        route_type_filter=raw.get("route_type_filter", [1]),
        walking_speed_kmh=raw.get("walking_speed_kmh", 5.0),
        max_walk_distance_m=raw.get("max_walk_distance_m", 500.0),
        start_station=raw.get("start_station"),
        time_window=time_window,
        excluded_stations=raw.get("excluded_stations", []),
        merge_stations=raw.get("merge_stations", []),
        use_google_walking=raw.get("use_google_walking", False),
        running_speed_kmh=raw.get("running_speed_kmh", 10.0),
        movement_mode=raw.get("movement_mode", "walk"),
        hard_stations=hard_stations,
        transit_route_types=raw.get("transit_route_types"),
    )
