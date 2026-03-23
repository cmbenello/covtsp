"""Date-aware service filtering for GTFS calendar data."""

from datetime import date, datetime
from pathlib import Path

import pandas as pd

DAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def get_active_services(gtfs_dir: str | Path, target_date: date) -> set[str]:
    """Determine which service_ids are active on a given date.

    Uses calendar.txt for regular schedules and calendar_dates.txt for exceptions.

    Args:
        gtfs_dir: Path to directory containing GTFS files.
        target_date: The date to query.

    Returns:
        Set of active service_id strings.
    """
    gtfs_dir = Path(gtfs_dir)
    active = set()

    # Step 1: Check calendar.txt for regular service
    calendar_path = gtfs_dir / "calendar.txt"
    if calendar_path.exists():
        cal = pd.read_csv(calendar_path, dtype={"service_id": str})
        cal["start_date"] = pd.to_datetime(cal["start_date"], format="%Y%m%d")
        cal["end_date"] = pd.to_datetime(cal["end_date"], format="%Y%m%d")

        target_dt = datetime.combine(target_date, datetime.min.time())
        day_name = DAY_COLUMNS[target_date.weekday()]

        for _, row in cal.iterrows():
            if row["start_date"] <= target_dt <= row["end_date"] and row[day_name] == 1:
                active.add(str(row["service_id"]))

    # Step 2: Apply exceptions from calendar_dates.txt
    dates_path = gtfs_dir / "calendar_dates.txt"
    if dates_path.exists():
        cal_dates = pd.read_csv(dates_path, dtype={"service_id": str})
        cal_dates["date"] = pd.to_datetime(cal_dates["date"], format="%Y%m%d")

        target_dt = datetime.combine(target_date, datetime.min.time())
        for _, row in cal_dates.iterrows():
            if row["date"] == target_dt:
                sid = str(row["service_id"])
                if row["exception_type"] == 1:
                    active.add(sid)  # Service added
                elif row["exception_type"] == 2:
                    active.discard(sid)  # Service removed

    return active
