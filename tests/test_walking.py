"""Tests for Google Maps walking transfer module."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.gtfs.parser import Station, WalkingTransfer
from src.gtfs.walking import (
    compute_google_walking_transfers,
    load_walking_cache,
    save_walking_cache,
)


@pytest.fixture
def tmp_cache(tmp_path):
    return tmp_path / "walking_cache.json"


@pytest.fixture
def sample_stations():
    s1 = Station(station_id="S1", name="Baker Street", lat=51.5226, lon=-0.1571)
    s2 = Station(station_id="S2", name="Marylebone", lat=51.5225, lon=-0.1631)
    s3 = Station(station_id="S3", name="Regent's Park", lat=51.5234, lon=-0.1466)
    return s1, s2, s3


@pytest.fixture
def sample_pairs(sample_stations):
    s1, s2, s3 = sample_stations
    return [
        (s1, s2, 380.0),  # ~380m Haversine
        (s1, s3, 420.0),  # ~420m Haversine
    ]


class TestCacheRoundTrip:
    def test_load_empty(self, tmp_cache):
        cache = load_walking_cache(tmp_cache)
        assert cache == {}

    def test_save_and_load(self, tmp_cache):
        data = {
            "S1|S2": {"walk_time_seconds": 240, "distance_meters": 312},
            "S2|S1": {"walk_time_seconds": 255, "distance_meters": 320},
        }
        save_walking_cache(tmp_cache, data)
        loaded = load_walking_cache(tmp_cache)
        assert loaded == data

    def test_save_creates_parent_dirs(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "walking_cache.json"
        save_walking_cache(deep_path, {"S1|S2": {"walk_time_seconds": 100, "distance_meters": 80}})
        assert deep_path.exists()


class TestNoApiKey:
    def test_returns_none_without_api_key(self, sample_pairs, tmp_cache):
        """Without GOOGLE_MAPS_API_KEY, should return None (fall back to Haversine)."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure key is not set
            os.environ.pop("GOOGLE_MAPS_API_KEY", None)
            result = compute_google_walking_transfers(sample_pairs, tmp_cache)
        assert result is None


class TestWithCache:
    def test_returns_transfers_from_cache(self, sample_pairs, tmp_cache):
        """With a pre-populated cache and API key, should return transfers without API calls."""
        cache = {
            "S1|S2": {"walk_time_seconds": 240, "distance_meters": 312},
            "S2|S1": {"walk_time_seconds": 255, "distance_meters": 320},
            "S1|S3": {"walk_time_seconds": 300, "distance_meters": 450},
            "S3|S1": {"walk_time_seconds": 310, "distance_meters": 455},
        }
        save_walking_cache(tmp_cache, cache)

        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "test-key"}):
            result = compute_google_walking_transfers(sample_pairs, tmp_cache)

        assert result is not None
        assert len(result) == 4  # 2 pairs × 2 directions
        assert all(isinstance(t, WalkingTransfer) for t in result)

        # Check specific values
        s1_to_s2 = [t for t in result if t.from_station_id == "S1" and t.to_station_id == "S2"]
        assert len(s1_to_s2) == 1
        assert s1_to_s2[0].walk_time_seconds == 240
        assert s1_to_s2[0].distance_meters == 312


class TestApiMocking:
    def test_batch_api_call(self, sample_pairs, tmp_cache):
        """Mock the API response and verify correct parsing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "OK",
            "rows": [{
                "elements": [
                    {
                        "status": "OK",
                        "duration": {"value": 240, "text": "4 mins"},
                        "distance": {"value": 312, "text": "0.3 km"},
                    }
                ]
            }],
        }

        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "test-key"}):
            with patch("src.gtfs.walking.requests.get", return_value=mock_response) as mock_get:
                result = compute_google_walking_transfers(sample_pairs, tmp_cache)

        assert result is not None
        assert mock_get.called
        # Should have made API calls for uncached pairs
        assert len(result) > 0

    def test_api_error_returns_none(self, sample_pairs, tmp_cache):
        """If the API raises an exception, should return None (fallback)."""
        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "test-key"}):
            with patch("src.gtfs.walking.requests.get", side_effect=Exception("API down")):
                result = compute_google_walking_transfers(sample_pairs, tmp_cache)

        assert result is None


class TestParserIntegration:
    def test_haversine_fallback_with_google_flag(self, tmp_path):
        """Parser with use_google_walking=True but no API key falls back to Haversine."""
        from src.config import CityConfig

        config = CityConfig(
            city_name="Test",
            gtfs_url="",
            gtfs_path=str(tmp_path),
            station_count=3,
            use_google_walking=True,
            max_walk_distance_m=800,
        )

        from src.gtfs.parser import GTFSParser, _haversine_meters

        parser = GTFSParser(config)
        stations = {
            "S1": Station(station_id="S1", name="A", lat=51.5226, lon=-0.1571),
            "S2": Station(station_id="S2", name="B", lat=51.5225, lon=-0.1631),
        }

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GOOGLE_MAPS_API_KEY", None)
            transfers = parser._compute_walking_transfers(stations)

        # Should fall back to Haversine and return transfers
        assert len(transfers) == 2  # bidirectional
        assert all(isinstance(t, WalkingTransfer) for t in transfers)


class TestRunningMode:
    """Tests for the running mode speed model and transfer scaling."""

    def test_effective_speed_walk_mode(self):
        from src.config import CityConfig

        cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path="test",
            station_count=0, movement_mode="walk",
        )
        assert cfg.effective_speed_kmh == 5.0
        assert cfg.effective_speed_for_distance(100) == 5.0
        assert cfg.effective_speed_for_distance(3000) == 5.0

    def test_effective_speed_run_mode(self):
        from src.config import CityConfig

        cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path="test",
            station_count=0, movement_mode="run", running_speed_kmh=10.0,
        )
        assert cfg.effective_speed_kmh == 10.0
        # Short sprint: 1.2x base
        assert cfg.effective_speed_for_distance(200) == 12.0
        # Medium: 1.0x base
        assert cfg.effective_speed_for_distance(1000) == 10.0
        # Long: 0.8x base
        assert cfg.effective_speed_for_distance(3000) == 8.0

    def test_effective_speed_custom_base(self):
        from src.config import CityConfig

        cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path="test",
            station_count=0, movement_mode="run", running_speed_kmh=12.0,
        )
        assert abs(cfg.effective_speed_for_distance(200) - 14.4) < 0.01
        assert cfg.effective_speed_for_distance(1000) == 12.0
        assert abs(cfg.effective_speed_for_distance(3000) - 9.6) < 0.01

    def test_run_mode_transfers_faster_than_walk(self, tmp_path):
        """Running mode should produce faster transfer times than walking."""
        from src.config import CityConfig
        from src.gtfs.parser import GTFSParser

        stations = {
            "S1": Station(station_id="S1", name="A", lat=51.5226, lon=-0.1571),
            "S2": Station(station_id="S2", name="B", lat=51.5225, lon=-0.1631),
        }

        walk_cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path=str(tmp_path),
            station_count=2, max_walk_distance_m=800, movement_mode="walk",
        )
        run_cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path=str(tmp_path),
            station_count=2, max_walk_distance_m=800, movement_mode="run",
            running_speed_kmh=10.0,
        )

        walk_parser = GTFSParser(walk_cfg)
        run_parser = GTFSParser(run_cfg)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GOOGLE_MAPS_API_KEY", None)
            walk_transfers = walk_parser._compute_walking_transfers(stations)
            run_transfers = run_parser._compute_walking_transfers(stations)

        assert len(walk_transfers) == len(run_transfers) == 2
        # Running should be faster
        for wt, rt in zip(
            sorted(walk_transfers, key=lambda t: (t.from_station_id, t.to_station_id)),
            sorted(run_transfers, key=lambda t: (t.from_station_id, t.to_station_id)),
        ):
            assert rt.walk_time_seconds < wt.walk_time_seconds

    def test_google_maps_scaling(self):
        """Scaling Google Maps walk times to run times."""
        from src.config import CityConfig
        from src.gtfs.parser import GTFSParser

        cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path="test",
            station_count=0, movement_mode="run", running_speed_kmh=10.0,
            walking_speed_kmh=5.0,
        )
        parser = GTFSParser(cfg)

        transfers = [
            WalkingTransfer("S1", "S2", walk_time_seconds=300, distance_meters=400),
            WalkingTransfer("S2", "S1", walk_time_seconds=310, distance_meters=400),
        ]

        scaled = parser._scale_transfers_for_running(transfers)
        # 400m is <500m, so run speed = 12 km/h. Scale = 5/12 ≈ 0.417
        assert scaled[0].walk_time_seconds == int(300 * 5.0 / 12.0)
        assert scaled[1].walk_time_seconds == int(310 * 5.0 / 12.0)

    def test_larger_max_distance_more_pairs(self, tmp_path):
        """Larger max_walk_distance_m should find more station pairs."""
        from src.config import CityConfig
        from src.gtfs.parser import GTFSParser

        # Three stations: S1-S2 close (~400m), S1-S3 far (~1200m)
        stations = {
            "S1": Station(station_id="S1", name="A", lat=51.5226, lon=-0.1571),
            "S2": Station(station_id="S2", name="B", lat=51.5225, lon=-0.1631),
            "S3": Station(station_id="S3", name="C", lat=51.5320, lon=-0.1571),
        }

        small_cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path=str(tmp_path),
            station_count=3, max_walk_distance_m=500, movement_mode="walk",
        )
        large_cfg = CityConfig(
            city_name="Test", gtfs_url="", gtfs_path=str(tmp_path),
            station_count=3, max_walk_distance_m=2000, movement_mode="run",
            running_speed_kmh=10.0,
        )

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GOOGLE_MAPS_API_KEY", None)
            small_transfers = GTFSParser(small_cfg)._compute_walking_transfers(stations)
            large_transfers = GTFSParser(large_cfg)._compute_walking_transfers(stations)

        assert len(large_transfers) >= len(small_transfers)
