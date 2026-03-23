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
    """Fetch real walking times from Google Maps Distance Matrix API.

    Uses a disk cache to avoid re-querying known pairs. Falls back to None
    (signaling caller to use Haversine) if API key is missing or API fails.

    Args:
        candidate_pairs: List of (from_station, to_station, haversine_distance) tuples,
                        pre-filtered by KDTree to be within max_walk_distance.
        cache_path: Path to the JSON cache file (e.g., data/london/walking_cache.json).

    Returns:
        List of WalkingTransfer objects with real walking times, or None if API unavailable.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        logger.info("GOOGLE_MAPS_API_KEY not set, falling back to Haversine")
        return None

    cache = load_walking_cache(cache_path)

    # Find uncached pairs (bidirectional)
    uncached = []
    for s1, s2, _ in candidate_pairs:
        fwd_key = f"{s1.station_id}|{s2.station_id}"
        rev_key = f"{s2.station_id}|{s1.station_id}"
        if fwd_key not in cache:
            uncached.append((s1.station_id, s1.lat, s1.lon, s2.station_id, s2.lat, s2.lon))
        if rev_key not in cache:
            uncached.append((s2.station_id, s2.lat, s2.lon, s1.station_id, s1.lat, s1.lon))

    # Deduplicate
    seen = set()
    deduped = []
    for pair in uncached:
        key = (pair[0], pair[3])
        if key not in seen:
            seen.add(key)
            deduped.append(pair)
    uncached = deduped

    if uncached:
        logger.info(f"Fetching {len(uncached)} walking routes from Google Maps API...")
        try:
            new_results = _batch_google_distances(uncached, api_key)
            cache.update(new_results)
            save_walking_cache(cache_path, cache)
            logger.info(f"Walking cache updated: {len(cache)} total entries")
        except Exception as e:
            logger.warning(f"Google Maps API failed: {e}, falling back to Haversine")
            return None
    else:
        logger.info(f"All {len(candidate_pairs)} walking pairs found in cache")

    # Build transfers from cache
    transfers = []
    for s1, s2, haversine_dist in candidate_pairs:
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

        if rev_key in cache:
            d = cache[rev_key]
            transfers.append(WalkingTransfer(
                from_station_id=s2.station_id,
                to_station_id=s1.station_id,
                walk_time_seconds=d["walk_time_seconds"],
                distance_meters=d["distance_meters"],
            ))

    return transfers
