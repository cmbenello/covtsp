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

    @property
    def data_dir(self) -> Path:
        return Path("data") / self.gtfs_path


def load_config(config_path: str | Path) -> CityConfig:
    """Load a city config from a YAML file."""
    config_path = Path(config_path)
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    time_window = TimeWindow()
    if "time_window" in raw:
        tw = raw.pop("time_window")
        time_window = TimeWindow(start=tw.get("start", "05:00"), end=tw.get("end", "01:00"))

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
    )
