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
