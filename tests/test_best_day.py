"""Tests for best_day analysis module."""

from datetime import date
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

from src.best_day import (
    _score_weather,
    _weather_summary,
    _default_weather,
    _generate_recommendation,
    _classify_day,
    _get_sample_dates_by_type,
    WEIGHTS,
)


# ── Weather Scoring ─────────────────────────────────────────────────────────


class TestWeatherScoring:
    def test_ideal_weather_scores_100(self):
        w = {"precipitation_mm": 0, "temp_max_c": 14, "temp_min_c": 8, "wind_max_kmh": 10}
        assert _score_weather(w) == 100.0

    def test_rain_penalty(self):
        w = {"precipitation_mm": 3, "temp_max_c": 14, "temp_min_c": 8, "wind_max_kmh": 10}
        assert _score_weather(w) == 70.0  # 100 - 10*3

    def test_heavy_rain_capped_at_zero(self):
        w = {"precipitation_mm": 15, "temp_max_c": 14, "temp_min_c": 8, "wind_max_kmh": 10}
        assert _score_weather(w) == 0.0

    def test_cold_temperature_penalty(self):
        w = {"precipitation_mm": 0, "temp_max_c": 2, "temp_min_c": -2, "wind_max_kmh": 10}
        score = _score_weather(w)
        # avg temp = 0, ideal low = 5, penalty = 2 * 5 = 10
        assert score == 90.0

    def test_hot_temperature_penalty(self):
        w = {"precipitation_mm": 0, "temp_max_c": 30, "temp_min_c": 20, "wind_max_kmh": 10}
        score = _score_weather(w)
        # avg temp = 25, ideal high = 18, penalty = 2 * 7 = 14
        assert score == 86.0

    def test_wind_penalty(self):
        w = {"precipitation_mm": 0, "temp_max_c": 14, "temp_min_c": 8, "wind_max_kmh": 35}
        score = _score_weather(w)
        assert score == 85.0  # 100 - 15

    def test_combined_penalties(self):
        w = {"precipitation_mm": 2, "temp_max_c": 14, "temp_min_c": 8, "wind_max_kmh": 35}
        score = _score_weather(w)
        assert score == 65.0  # 100 - 20(rain) - 15(wind)


class TestWeatherSummary:
    def test_dry(self):
        assert "Dry" in _weather_summary(0, 14, 10)

    def test_light_rain(self):
        assert "Light rain" in _weather_summary(2, 14, 10)

    def test_heavy_rain(self):
        assert "Heavy rain" in _weather_summary(8, 14, 10)

    def test_windy(self):
        assert "windy" in _weather_summary(0, 14, 35)

    def test_hot(self):
        assert "hot" in _weather_summary(0, 30, 10)

    def test_cold(self):
        assert "cold" in _weather_summary(0, 3, 10)


class TestDefaultWeather:
    def test_returns_neutral_score(self):
        w = _default_weather(date(2026, 4, 1))
        assert w["score"] == 70.0
        assert w["date"] == "2026-04-01"


# ── Day Classification ──────────────────────────────────────────────────────


class TestDayClassification:
    def test_weekday(self):
        # 2026-04-01 is a Wednesday
        assert _classify_day(date(2026, 4, 1), set(), None) == "weekday"

    def test_saturday(self):
        # 2026-04-04 is a Saturday
        assert _classify_day(date(2026, 4, 4), set(), None) == "saturday"

    def test_sunday(self):
        # 2026-04-05 is a Sunday
        assert _classify_day(date(2026, 4, 5), set(), None) == "sunday"


class TestSampleDatesByType:
    def test_picks_one_per_type(self):
        dates = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 4), date(2026, 4, 5)]
        samples = _get_sample_dates_by_type(dates)
        assert "weekday" in samples
        assert "saturday" in samples
        assert "sunday" in samples


# ── Recommendation Text ─────────────────────────────────────────────────────


class TestRecommendation:
    def test_excellent_service(self):
        svc = {"score": 95, "day_type": "weekday"}
        wthr = {"score": 85, "summary": "Dry, 14C"}
        dis = {"count": 0}
        rec = _generate_recommendation(svc, wthr, dis)
        assert "Excellent" in rec

    def test_disruptions_mentioned(self):
        svc = {"score": 80, "day_type": "weekday"}
        wthr = {"score": 90, "summary": "Dry, 14C"}
        dis = {"count": 2}
        rec = _generate_recommendation(svc, wthr, dis)
        assert "2 disruptions" in rec

    def test_rain_mentioned(self):
        svc = {"score": 80, "day_type": "weekday"}
        wthr = {"score": 65, "summary": "Light rain, 12C"}
        dis = {"count": 0}
        rec = _generate_recommendation(svc, wthr, dis)
        assert "rain" in rec.lower()


# ── Weights ─────────────────────────────────────────────────────────────────


class TestWeights:
    def test_weights_sum_to_one(self):
        total = sum(WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9
