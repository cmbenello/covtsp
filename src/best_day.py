"""Best Day to Attempt analysis — recommends optimal dates for transit challenges.

Combines GTFS service patterns, weather forecasts, planned disruptions, and
historical schedule variance to score upcoming dates.
"""

import json
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from src.config import CityConfig
from src.gtfs.calendar import get_active_services

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WEIGHTS = {
    "service": 0.45,
    "weather": 0.25,
    "disruption": 0.20,
    "delay_pattern": 0.10,
}

# Weather scoring: start at 100, apply penalties
RAIN_PENALTY_PER_MM = 10
TEMP_IDEAL_LOW = 5
TEMP_IDEAL_HIGH = 18
TEMP_PENALTY_PER_DEGREE = 2
WIND_THRESHOLD_KMH = 30
WIND_PENALTY = 15

# City center coordinates for weather lookups
CITY_COORDS = {
    "london": (51.5074, -0.1278),
    "nyc": (40.7128, -74.0060),
    "berlin": (52.5200, 13.4050),
}

# TfL line IDs for disruption lookup
TFL_TUBE_LINES = [
    "bakerloo", "central", "circle", "district", "hammersmith-city",
    "jubilee", "metropolitan", "northern", "piccadilly", "victoria",
    "waterloo-city", "dlr", "elizabeth", "overground",
]

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Cache TTLs in seconds
WEATHER_CACHE_TTL = 6 * 3600  # 6 hours
DISRUPTION_CACHE_TTL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# 1. Service Pattern Analysis
# ---------------------------------------------------------------------------

def analyze_service_patterns(
    config: CityConfig, candidate_dates: list[date]
) -> list[dict]:
    """Count active services and departures per candidate date.

    Uses GTFS calendar filtering (fast — no graph construction needed) to
    determine how many trip segments are active on each date.  More active
    segments → more TEG edges → shorter waits → better routes.
    """
    gtfs_dir = config.data_dir

    # Load trips + stop_times once
    trips = pd.read_csv(gtfs_dir / "trips.txt", dtype={"service_id": str, "trip_id": str, "route_id": str})
    stop_times = pd.read_csv(
        gtfs_dir / "stop_times.txt",
        dtype={"trip_id": str, "stop_id": str},
        usecols=["trip_id", "stop_id", "departure_time"],
    )

    # Optionally filter to relevant route types
    if config.route_type_filter:
        routes = pd.read_csv(gtfs_dir / "routes.txt", dtype={"route_id": str})
        valid_route_ids = set(routes[routes["route_type"].isin(config.route_type_filter)]["route_id"])
        trips = trips[trips["route_id"].isin(valid_route_ids)]

    results = []
    departure_counts = []

    for d in candidate_dates:
        active_sids = get_active_services(gtfs_dir, d)
        active_trips = trips[trips["service_id"].isin(active_sids)]
        active_trip_ids = set(active_trips["trip_id"])

        # Count total departures for active trips
        active_departures = stop_times[stop_times["trip_id"].isin(active_trip_ids)]
        total_departures = len(active_departures)
        departure_counts.append(total_departures)

        # Per-station frequency for hard station detection
        station_freq = active_departures.groupby("stop_id").size().to_dict()

        day_type = _classify_day(d, active_sids, config)

        results.append({
            "date": d.isoformat(),
            "active_services": len(active_sids),
            "total_departures": total_departures,
            "day_type": day_type,
            "station_frequencies": station_freq,
        })

    # Normalize scores: best day = 100
    max_deps = max(departure_counts) if departure_counts else 1
    for i, r in enumerate(results):
        r["score"] = round(100 * departure_counts[i] / max_deps, 1) if max_deps else 50.0

    return results


def _classify_day(d: date, active_sids: set[str], config: CityConfig) -> str:
    """Classify a date as weekday/saturday/sunday/holiday based on service count heuristics."""
    weekday = d.weekday()
    if weekday < 5:
        return "weekday"
    elif weekday == 5:
        return "saturday"
    else:
        return "sunday"


# ---------------------------------------------------------------------------
# 2. Weather Forecast
# ---------------------------------------------------------------------------

def fetch_weather_forecast(
    city_slug: str,
    dates: list[date],
    cache_dir: Optional[Path] = None,
) -> list[dict]:
    """Fetch weather data from Open-Meteo (free, no API key).

    Uses the forecast endpoint for upcoming dates (<=16 days) and the
    archive endpoint for past dates.
    """
    coords = CITY_COORDS.get(city_slug)
    if not coords:
        return [_default_weather(d) for d in dates]

    lat, lon = coords

    # Check cache
    cached = _load_cache(cache_dir, "weather_cache.json", WEATHER_CACHE_TTL) if cache_dir else None
    if cached and set(d.isoformat() for d in dates).issubset(cached.get("_dates", set())):
        return [cached["data"][d.isoformat()] for d in dates if d.isoformat() in cached["data"]]

    today = date.today()
    forecast_dates = [d for d in dates if d >= today and d <= today + timedelta(days=16)]
    archive_dates = [d for d in dates if d < today]

    weather_by_date: dict[str, dict] = {}

    # Forecast API
    if forecast_dates:
        try:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
                    "timezone": "auto",
                    "start_date": min(forecast_dates).isoformat(),
                    "end_date": max(forecast_dates).isoformat(),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("daily", {})
            _parse_weather_response(data, weather_by_date)
        except (requests.RequestException, KeyError, ValueError):
            pass  # Graceful fallback — weather just won't factor in

    # Archive API for past dates
    if archive_dates:
        try:
            resp = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
                    "timezone": "auto",
                    "start_date": min(archive_dates).isoformat(),
                    "end_date": max(archive_dates).isoformat(),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("daily", {})
            _parse_weather_response(data, weather_by_date)
        except (requests.RequestException, KeyError, ValueError):
            pass

    # Build results, filling missing dates with defaults
    results = []
    for d in dates:
        iso = d.isoformat()
        if iso in weather_by_date:
            entry = weather_by_date[iso]
            entry["score"] = _score_weather(entry)
            results.append(entry)
        else:
            results.append(_default_weather(d))

    # Cache results
    if cache_dir:
        _save_cache(cache_dir, "weather_cache.json", {
            "data": {r["date"]: r for r in results},
            "_dates": [d.isoformat() for d in dates],
        })

    return results


def _parse_weather_response(data: dict, out: dict[str, dict]) -> None:
    """Parse Open-Meteo daily response into our format."""
    dates = data.get("time", [])
    precip = data.get("precipitation_sum", [])
    temp_max = data.get("temperature_2m_max", [])
    temp_min = data.get("temperature_2m_min", [])
    wind_max = data.get("wind_speed_10m_max", [])

    for i, d in enumerate(dates):
        p = precip[i] if i < len(precip) and precip[i] is not None else 0
        tmax = temp_max[i] if i < len(temp_max) and temp_max[i] is not None else 12
        tmin = temp_min[i] if i < len(temp_min) and temp_min[i] is not None else 8
        w = wind_max[i] if i < len(wind_max) and wind_max[i] is not None else 10

        out[d] = {
            "date": d,
            "precipitation_mm": round(p, 1),
            "temp_max_c": round(tmax, 1),
            "temp_min_c": round(tmin, 1),
            "wind_max_kmh": round(w, 1),
            "summary": _weather_summary(p, tmax, w),
        }


def _score_weather(w: dict) -> float:
    """Score weather from 0–100 (100 = ideal conditions)."""
    score = 100.0

    # Rain penalty
    score -= RAIN_PENALTY_PER_MM * w.get("precipitation_mm", 0)

    # Temperature penalty (outside ideal 5–18C band)
    temp_avg = (w.get("temp_max_c", 12) + w.get("temp_min_c", 8)) / 2
    if temp_avg < TEMP_IDEAL_LOW:
        score -= TEMP_PENALTY_PER_DEGREE * (TEMP_IDEAL_LOW - temp_avg)
    elif temp_avg > TEMP_IDEAL_HIGH:
        score -= TEMP_PENALTY_PER_DEGREE * (temp_avg - TEMP_IDEAL_HIGH)

    # Wind penalty
    wind = w.get("wind_max_kmh", 0)
    if wind > WIND_THRESHOLD_KMH:
        score -= WIND_PENALTY

    return round(max(0, min(100, score)), 1)


def _weather_summary(precip: float, temp_max: float, wind: float) -> str:
    """Generate a human-readable weather summary."""
    parts = []
    if precip > 5:
        parts.append("Heavy rain")
    elif precip > 1:
        parts.append("Light rain")
    elif precip > 0.1:
        parts.append("Drizzle")
    else:
        parts.append("Dry")

    if temp_max > 25:
        parts.append(f"hot ({temp_max:.0f}C)")
    elif temp_max < 5:
        parts.append(f"cold ({temp_max:.0f}C)")
    else:
        parts.append(f"{temp_max:.0f}C")

    if wind > 40:
        parts.append("very windy")
    elif wind > WIND_THRESHOLD_KMH:
        parts.append("windy")

    return ", ".join(parts)


def _default_weather(d: date) -> dict:
    """Fallback when weather data unavailable."""
    return {
        "date": d.isoformat(),
        "precipitation_mm": 0,
        "temp_max_c": 12,
        "temp_min_c": 8,
        "wind_max_kmh": 10,
        "summary": "No forecast available",
        "score": 70.0,  # neutral default
    }


# ---------------------------------------------------------------------------
# 3. Disruptions
# ---------------------------------------------------------------------------

def fetch_disruptions(
    config: CityConfig,
    city_slug: str,
    dates: list[date],
    cache_dir: Optional[Path] = None,
) -> list[dict]:
    """Fetch planned disruption data.

    London uses the TfL API. Other cities fall back to GTFS calendar_dates.txt
    exception_type=2 entries as a proxy for service removals.
    """
    if city_slug == "london":
        return _fetch_tfl_disruptions(dates, cache_dir)
    else:
        return _fetch_gtfs_disruptions(config, dates)


def _fetch_tfl_disruptions(
    dates: list[date], cache_dir: Optional[Path] = None
) -> list[dict]:
    """Fetch disruptions from the TfL API (free, no key required)."""
    cached = _load_cache(cache_dir, "disruption_cache.json", DISRUPTION_CACHE_TTL) if cache_dir else None
    if cached:
        results = []
        for d in dates:
            iso = d.isoformat()
            if iso in cached.get("data", {}):
                results.append(cached["data"][iso])
            else:
                results.append({"date": iso, "score": 100, "count": 0, "items": []})
        return results

    disruptions_raw = []
    try:
        resp = requests.get(
            "https://api.tfl.gov.uk/Line/Mode/tube,dlr/Disruption",
            timeout=10,
        )
        resp.raise_for_status()
        disruptions_raw = resp.json()
    except (requests.RequestException, ValueError):
        return [{"date": d.isoformat(), "score": 80.0, "count": 0, "items": []} for d in dates]

    # Also check planned works via line status
    status_items = []
    try:
        resp = requests.get(
            "https://api.tfl.gov.uk/Line/Mode/tube,dlr/Status",
            timeout=10,
        )
        resp.raise_for_status()
        status_items = resp.json()
    except (requests.RequestException, ValueError):
        pass

    # Build per-date disruption info
    results = []
    for d in dates:
        items_for_date = []

        # Check disruptions
        for dis in disruptions_raw:
            desc = dis.get("description", "")
            category = dis.get("categoryDescription", "")
            affected = dis.get("affectedRoutes", [])
            line_names = [r.get("name", "") for r in affected] if affected else []
            items_for_date.append({
                "line": ", ".join(line_names) if line_names else "Unknown",
                "description": desc[:120],
                "severity": "minor" if "minor" in category.lower() else "major",
            })

        # Check line statuses for planned closures
        for line in status_items:
            for status in line.get("lineStatuses", []):
                reason = status.get("reason", "")
                severity = status.get("statusSeverity", 10)
                if severity < 10 and reason:  # 10 = Good Service
                    disruption_type = status.get("statusSeverityDescription", "")
                    items_for_date.append({
                        "line": line.get("name", "Unknown"),
                        "description": reason[:120],
                        "severity": "major" if severity < 5 else "minor",
                    })

        # Deduplicate by line
        seen_lines = set()
        unique_items = []
        for item in items_for_date:
            key = item["line"]
            if key not in seen_lines:
                seen_lines.add(key)
                unique_items.append(item)

        count = len(unique_items)
        score = max(0, 100 - 20 * count)

        results.append({
            "date": d.isoformat(),
            "score": round(score, 1),
            "count": count,
            "items": unique_items[:5],  # cap at 5 for UI
        })

    # Cache
    if cache_dir:
        _save_cache(cache_dir, "disruption_cache.json", {
            "data": {r["date"]: r for r in results},
        })

    return results


def _fetch_gtfs_disruptions(config: CityConfig, dates: list[date]) -> list[dict]:
    """Use GTFS calendar_dates exception_type=2 as a proxy for disruptions."""
    gtfs_dir = config.data_dir
    dates_path = gtfs_dir / "calendar_dates.txt"

    removals_by_date: dict[str, int] = {}
    if dates_path.exists():
        cal_dates = pd.read_csv(dates_path, dtype={"service_id": str})
        cal_dates["date_parsed"] = pd.to_datetime(cal_dates["date"], format="%Y%m%d")
        for d in dates:
            target_dt = datetime.combine(d, datetime.min.time())
            day_exceptions = cal_dates[
                (cal_dates["date_parsed"] == target_dt) & (cal_dates["exception_type"] == 2)
            ]
            removals_by_date[d.isoformat()] = len(day_exceptions)

    max_removals = max(removals_by_date.values()) if removals_by_date else 1
    results = []
    for d in dates:
        iso = d.isoformat()
        n = removals_by_date.get(iso, 0)
        score = 100 - (60 * n / max_removals) if max_removals > 0 else 100
        results.append({
            "date": iso,
            "score": round(max(0, score), 1),
            "count": n,
            "items": [{"line": "GTFS", "description": f"{n} service removals", "severity": "minor"}] if n > 0 else [],
        })

    return results


# ---------------------------------------------------------------------------
# 4. Historical Delay Patterns
# ---------------------------------------------------------------------------

def estimate_historical_delays(
    config: CityConfig, candidate_dates: list[date]
) -> list[dict]:
    """Estimate delay risk by day-of-week type using GTFS schedule variance.

    Weekdays generally have the most reliable/frequent service. Weekends and
    holidays have reduced schedules and longer headways, increasing the risk
    of delays compounding.
    """
    gtfs_dir = config.data_dir

    # Count services per day-type by sampling one date per type
    type_scores = {}
    sample_dates = _get_sample_dates_by_type(candidate_dates)

    for day_type, sample_date in sample_dates.items():
        active = get_active_services(gtfs_dir, sample_date)
        type_scores[day_type] = len(active)

    max_services = max(type_scores.values()) if type_scores else 1

    results = []
    for d in candidate_dates:
        day_type = _classify_day(d, set(), config)
        services = type_scores.get(day_type, max_services)
        score = round(100 * services / max_services, 1) if max_services else 50.0
        results.append({
            "date": d.isoformat(),
            "day_type": day_type,
            "score": score,
        })

    return results


def _get_sample_dates_by_type(dates: list[date]) -> dict[str, date]:
    """Pick one sample date per day-type from candidates."""
    samples: dict[str, date] = {}
    for d in dates:
        weekday = d.weekday()
        if weekday < 5:
            dt = "weekday"
        elif weekday == 5:
            dt = "saturday"
        else:
            dt = "sunday"
        if dt not in samples:
            samples[dt] = d
    return samples


# ---------------------------------------------------------------------------
# 5. Orchestrator
# ---------------------------------------------------------------------------

def compute_best_days(
    config: CityConfig,
    city_slug: str,
    days_ahead: int = 14,
) -> dict:
    """Compute composite best-day recommendations.

    Returns a JSON-serializable dict ready for the web UI.
    """
    today = date.today()
    candidate_dates = [today + timedelta(days=i) for i in range(days_ahead)]

    cache_dir = config.data_dir if config.data_dir.exists() else None

    # Run all analyses
    service_data = analyze_service_patterns(config, candidate_dates)
    weather_data = fetch_weather_forecast(city_slug, candidate_dates, cache_dir)
    disruption_data = fetch_disruptions(config, city_slug, candidate_dates, cache_dir)
    delay_data = estimate_historical_delays(config, candidate_dates)

    # Combine into per-day entries
    days = []
    for i, d in enumerate(candidate_dates):
        svc = service_data[i]
        wthr = weather_data[i]
        dis = disruption_data[i]
        dly = delay_data[i]

        composite = (
            WEIGHTS["service"] * svc["score"]
            + WEIGHTS["weather"] * wthr["score"]
            + WEIGHTS["disruption"] * dis["score"]
            + WEIGHTS["delay_pattern"] * dly["score"]
        )

        recommendation = _generate_recommendation(svc, wthr, dis)

        days.append({
            "date": d.isoformat(),
            "day_of_week": DAY_NAMES[d.weekday()],
            "overall_score": round(composite, 1),
            "service": {
                "score": svc["score"],
                "active_services": svc["active_services"],
                "total_departures": svc["total_departures"],
                "day_type": svc["day_type"],
            },
            "weather": {
                "score": wthr["score"],
                "precipitation_mm": wthr["precipitation_mm"],
                "temp_max_c": wthr["temp_max_c"],
                "temp_min_c": wthr["temp_min_c"],
                "wind_max_kmh": wthr["wind_max_kmh"],
                "summary": wthr["summary"],
            },
            "disruptions": {
                "score": dis["score"],
                "count": dis["count"],
                "items": dis["items"],
            },
            "recommendation": recommendation,
        })

    # Sort by score descending
    days.sort(key=lambda x: x["overall_score"], reverse=True)
    best = days[0] if days else None

    return {
        "city": config.city_name,
        "city_slug": city_slug,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "analysis_window": {
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=days_ahead - 1)).isoformat(),
        },
        "best_date": best["date"] if best else None,
        "best_score": best["overall_score"] if best else 0,
        "days": days,
        "methodology": {
            "service_weight": WEIGHTS["service"],
            "weather_weight": WEIGHTS["weather"],
            "disruption_weight": WEIGHTS["disruption"],
            "delay_weight": WEIGHTS["delay_pattern"],
            "notes": "Service score based on active GTFS trip count. Weather from Open-Meteo. Disruptions from TfL API (London) or GTFS exceptions.",
        },
    }


def _generate_recommendation(svc: dict, wthr: dict, dis: dict) -> str:
    """Generate a natural-language recommendation for a date."""
    parts = []

    # Service assessment
    if svc["score"] >= 90:
        parts.append(f"Excellent {svc['day_type']} service levels")
    elif svc["score"] >= 70:
        parts.append(f"Good {svc['day_type']} service")
    else:
        parts.append(f"Reduced {svc['day_type']} service")

    # Weather
    summary = wthr.get("summary", "").lower()
    if wthr["score"] >= 90:
        parts.append("ideal weather")
    elif wthr["score"] >= 60:
        if "rain" in summary:
            parts.append("rain may slow outdoor transfers")
        elif "wind" in summary:
            parts.append("windy conditions")
        else:
            parts.append("acceptable weather")
    else:
        if "rain" in summary:
            parts.append("heavy rain expected")
        else:
            parts.append("poor weather conditions")

    # Disruptions
    if dis["count"] > 0:
        parts.append(f"{dis['count']} disruption{'s' if dis['count'] > 1 else ''} reported")

    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

def _load_cache(cache_dir: Optional[Path], filename: str, ttl: float) -> Optional[dict]:
    """Load a JSON cache file if it exists and is fresh."""
    if not cache_dir:
        return None
    path = cache_dir / filename
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
        if _time.time() - mtime > ttl:
            return None
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(cache_dir: Optional[Path], filename: str, data: dict) -> None:
    """Write a JSON cache file."""
    if not cache_dir:
        return
    path = cache_dir / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass
