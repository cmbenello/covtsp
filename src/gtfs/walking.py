"""Google Maps walking transfers: real walking times via Distance Matrix API."""

import json
import logging
import os
from pathlib import Path

import requests

from src.gtfs.parser import Station, WalkingTransfer

logger = logging.getLogger(__name__)


def load_walking_cache(cache_path: Path) -> dict[str, dict]:
    """Load cached walking distances from disk.

    Returns:
        Dict of "from_id|to_id" -> {"walk_time_seconds": int, "distance_meters": float}
    """
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return {}


def save_walking_cache(cache_path: Path, cache: dict[str, dict]) -> None:
    """Persist walking cache to disk."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)


def _chunks(lst: list, n: int):
    """Yield successive n-sized chunks from a list."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _batch_google_distances(
    pairs: list[tuple[str, float, float, str, float, float]],
    api_key: str,
) -> dict[str, dict]:
    """Call Google Maps Distance Matrix API in batches.

    Groups pairs by origin station, sends 1 origin × up to 25 destinations per request.

    Args:
        pairs: List of (from_id, from_lat, from_lon, to_id, to_lat, to_lon) tuples.
        api_key: Google Maps API key.

    Returns:
        Dict of "from_id|to_id" -> {"walk_time_seconds": int, "distance_meters": float}
    """
    # Group by origin
    by_origin: dict[str, list[tuple[float, float, str, float, float]]] = {}
    for from_id, from_lat, from_lon, to_id, to_lat, to_lon in pairs:
        by_origin.setdefault(from_id, []).append((from_lat, from_lon, to_id, to_lat, to_lon))

    results = {}
    total_calls = 0

    for from_id, destinations in by_origin.items():
        from_lat, from_lon = destinations[0][0], destinations[0][1]
        origin = f"{from_lat},{from_lon}"

        for chunk in _chunks(destinations, 25):
            dest_strs = [f"{d[3]},{d[4]}" for d in chunk]

            try:
                resp = requests.get(
                    "https://maps.googleapis.com/maps/api/distancematrix/json",
                    params={
                        "origins": origin,
                        "destinations": "|".join(dest_strs),
                        "mode": "walking",
                        "key": api_key,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                total_calls += 1
            except requests.RequestException as e:
                logger.warning(f"Distance Matrix API request failed: {e}")
                continue

            if data.get("status") != "OK":
                logger.warning(f"Distance Matrix API error: {data.get('status')}: {data.get('error_message', '')}")
                continue

            rows = data.get("rows", [])
            if not rows:
                continue

            for j, elem in enumerate(rows[0].get("elements", [])):
                to_id = chunk[j][2]
                key = f"{from_id}|{to_id}"

                if elem.get("status") == "OK":
                    results[key] = {
                        "walk_time_seconds": elem["duration"]["value"],
                        "distance_meters": elem["distance"]["value"],
                    }
                else:
                    logger.debug(f"No walking route {from_id} -> {to_id}: {elem.get('status')}")

    logger.info(f"Google Maps API: {total_calls} calls, {len(results)} walking routes found")
    return results


def compute_google_walking_transfers(
    candidate_pairs: list[tuple[Station, Station, float]],
    cache_path: Path,
) -> list[WalkingTransfer] | None:
    """Load real walking times from the pre-populated Routes API cache.

    Cache is populated offline via scripts/compute_all_walking.py using the
    Google Routes API. At solve time we are cache-only — no live API calls.
    Pairs missing from the cache are silently skipped (caller falls back to
    Haversine for those pairs).

    Args:
        candidate_pairs: List of (from_station, to_station, haversine_distance) tuples,
                        pre-filtered by KDTree to be within max_walk_distance.
        cache_path: Path to the JSON cache file (e.g., data/london/walking_cache.json).

    Returns:
        List of WalkingTransfer objects with real walking times, or None if cache missing.
    """
    if not cache_path.exists():
        logger.info("No walking cache found, falling back to Haversine")
        return None

    cache = load_walking_cache(cache_path)
    logger.info(f"Walking cache loaded: {len(cache)} entries")

    # Build transfers from cache only — no live API calls
    transfers = []
    cache_hits = 0
    cache_misses = 0
    for s1, s2, _ in candidate_pairs:
        fwd_key = f"{s1.station_id}|{s2.station_id}"
        rev_key = f"{s2.station_id}|{s1.station_id}"

        if fwd_key in cache:
            d = cache[fwd_key]
            transfers.append(WalkingTransfer(
                from_station_id=s1.station_id,
                to_station_id=s2.station_id,
                walk_time_seconds=d["walk_time_seconds"],
                distance_meters=d["distance_meters"],
            ))
            cache_hits += 1
        else:
            cache_misses += 1

        if rev_key in cache:
            d = cache[rev_key]
            transfers.append(WalkingTransfer(
                from_station_id=s2.station_id,
                to_station_id=s1.station_id,
                walk_time_seconds=d["walk_time_seconds"],
                distance_meters=d["distance_meters"],
            ))

    logger.info(f"Walking transfers: {len(transfers)} from cache ({cache_misses} pairs not cached, using Haversine fallback)")
    return transfers
