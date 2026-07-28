from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Activity, DailyWellness


def round_opt(value: Optional[float], ndigits: int = 1) -> Optional[float]:
    """Garmin's training-effect floats arrive with float32 noise (e.g. 3.0999999046325684)."""
    return round(value, ndigits) if value is not None else None


def format_pace(seconds_per_km: Optional[float]) -> Optional[str]:
    if seconds_per_km is None:
        return None
    minutes, seconds = divmod(round(seconds_per_km), 60)
    return f"{minutes}:{seconds:02d}"


def _period_key(d: date, period: str) -> str:
    if period == "week":
        year, week, _ = d.isocalendar()
        return f"{year}-W{week:02d}"
    if period == "month":
        return d.strftime("%Y-%m")
    if period == "year":
        return d.strftime("%Y")
    raise ValueError(f"Unknown period: {period}")


def fetch_activities(
    db: Session,
    start: Optional[date] = None,
    end: Optional[date] = None,
    include_ignored: bool = False,
) -> list[Activity]:
    """include_ignored=False (the default) excludes activities flagged via is_ignored — used for
    anything HR-derived (pace/HR, EF, zone breakdown), since a bad HR source (e.g. watch-wrist
    instead of chest strap) poisons those. Distance/time-only aggregates (calendar totals, rolling
    volume) pass include_ignored=True since the user still wants those runs counted toward km."""
    stmt = select(Activity).order_by(Activity.start_time_local.asc())
    if not include_ignored:
        stmt = stmt.where(Activity.is_ignored.is_(False))
    if start:
        stmt = stmt.where(Activity.start_time_local >= datetime.combine(start, datetime.min.time()))
    if end:
        stmt = stmt.where(Activity.start_time_local <= datetime.combine(end, datetime.max.time()))
    return list(db.execute(stmt).scalars().all())


def calendar_aggregate(db: Session, period: str, start: Optional[date] = None, end: Optional[date] = None) -> list[dict]:
    """Sum distance/time bucketed by calendar week/month/year (not rolling windows)."""
    activities = fetch_activities(db, start, end, include_ignored=True)
    buckets: dict[str, dict] = defaultdict(lambda: {"distance_m": 0.0, "duration_s": 0.0, "count": 0})

    for a in activities:
        key = _period_key(a.start_time_local.date(), period)
        buckets[key]["distance_m"] += a.distance_m
        buckets[key]["duration_s"] += a.duration_s
        buckets[key]["count"] += 1

    result = []
    for key in sorted(buckets.keys()):
        b = buckets[key]
        result.append(
            {
                "period": key,
                "distance_km": round(b["distance_m"] / 1000, 2),
                "duration_h": round(b["duration_s"] / 3600, 2),
                "activity_count": b["count"],
                "avg_pace_per_km": format_pace(b["duration_s"] / b["distance_m"] * 1000) if b["distance_m"] else None,
            }
        )
    return result


def rolling_weekly_volume(db: Session, window_days: int = 7, start: Optional[date] = None, end: Optional[date] = None) -> list[dict]:
    """Daily rolling sum of distance over the trailing `window_days`, plus % change vs the
    prior equal-length window (the "max 10% weekly progression" check)."""
    activities = fetch_activities(db, None, end, include_ignored=True)
    if not activities:
        return []

    daily_distance: dict[date, float] = defaultdict(float)
    for a in activities:
        daily_distance[a.start_time_local.date()] += a.distance_m

    range_start = start or min(daily_distance.keys())
    range_end = end or date.today()

    result = []
    day = range_start
    while day <= range_end:
        window_sum = sum(
            daily_distance.get(day - timedelta(days=i), 0.0) for i in range(window_days)
        )
        prior_window_sum = sum(
            daily_distance.get(day - timedelta(days=window_days + i), 0.0) for i in range(window_days)
        )
        pct_change = None
        if prior_window_sum > 0:
            pct_change = round((window_sum - prior_window_sum) / prior_window_sum * 100, 1)

        result.append(
            {
                "date": day.isoformat(),
                "rolling_distance_km": round(window_sum / 1000, 2),
                "pct_change_vs_prior_window": pct_change,
            }
        )
        day += timedelta(days=1)

    return result


def compute_decoupling(laps: list) -> Optional[float]:
    """Pw:Hr-style aerobic decoupling from lap splits: % drop in Efficiency Factor (speed/HR)
    from the first half of a run to the second half. Positive = fatigue / less aerobic
    durability. Needs at least 4 laps with HR data to split meaningfully."""
    valid = [lap for lap in laps if lap.avg_hr and lap.distance_m and lap.duration_s]
    if len(valid) < 4:
        return None

    half = len(valid) // 2
    efs = []
    for half_laps in (valid[:half], valid[half:]):
        distance_m = sum(lap.distance_m for lap in half_laps)
        duration_s = sum(lap.duration_s for lap in half_laps)
        hr_weighted = sum(lap.avg_hr * lap.distance_m for lap in half_laps)
        if not distance_m or not duration_s or not hr_weighted:
            return None
        speed_m_per_min = distance_m / duration_s * 60
        avg_hr = hr_weighted / distance_m
        efs.append(speed_m_per_min / avg_hr)

    ef_first, ef_second = efs
    if not ef_first:
        return None
    return round((ef_first - ef_second) / ef_first * 100, 1)


def pace_hr_progression(
    db: Session,
    weeks: int = 12,
    activity_type: Optional[str] = "running",
    end: Optional[date] = None,
) -> list[dict]:
    """Weekly avg pace vs avg HR — the core aerobic-adaptation signal (more reliable than a
    watch's VO2max estimate when training is mostly Zone 2)."""
    end = end or date.today()
    start = end - timedelta(weeks=weeks)

    activities = fetch_activities(db, start, end)
    if activity_type:
        activities = [a for a in activities if a.activity_type == activity_type]
    activities = [a for a in activities if a.avg_hr and a.distance_m]

    buckets: dict[str, dict] = defaultdict(lambda: {"distance_m": 0.0, "duration_s": 0.0, "hr_weighted": 0.0})
    for a in activities:
        year, week, _ = a.start_time_local.date().isocalendar()
        key = f"{year}-W{week:02d}"
        buckets[key]["distance_m"] += a.distance_m
        buckets[key]["duration_s"] += a.duration_s
        buckets[key]["hr_weighted"] += a.avg_hr * a.distance_m

    result = []
    for key in sorted(buckets.keys()):
        b = buckets[key]
        result.append(
            {
                "week": key,
                "avg_pace_per_km": format_pace(b["duration_s"] / b["distance_m"] * 1000),
                "avg_hr": round(b["hr_weighted"] / b["distance_m"]),
                "distance_km": round(b["distance_m"] / 1000, 2),
            }
        )
    return result


def efficiency_factor_progression(
    db: Session,
    weeks: int = 26,
    activity_type: Optional[str] = "running",
    end: Optional[date] = None,
) -> list[dict]:
    """Weekly Efficiency Factor (speed per heartbeat) — same aerobic-adaptation signal as
    pace_hr_progression but as a single rising-is-better number instead of two axes."""
    end = end or date.today()
    start = end - timedelta(weeks=weeks)

    activities = fetch_activities(db, start, end)
    if activity_type:
        activities = [a for a in activities if a.activity_type == activity_type]
    activities = [a for a in activities if a.avg_hr and a.distance_m and a.duration_s]

    buckets: dict[str, dict] = defaultdict(lambda: {"distance_m": 0.0, "duration_s": 0.0, "hr_weighted": 0.0})
    for a in activities:
        year, week, _ = a.start_time_local.date().isocalendar()
        key = f"{year}-W{week:02d}"
        buckets[key]["distance_m"] += a.distance_m
        buckets[key]["duration_s"] += a.duration_s
        buckets[key]["hr_weighted"] += a.avg_hr * a.distance_m

    result = []
    for key in sorted(buckets.keys()):
        b = buckets[key]
        avg_hr = b["hr_weighted"] / b["distance_m"]
        speed_m_per_min = b["distance_m"] / b["duration_s"] * 60
        result.append(
            {
                "week": key,
                "ef": round(speed_m_per_min / avg_hr, 3),
                "distance_km": round(b["distance_m"] / 1000, 2),
            }
        )
    return result


def hr_zone_weekly_breakdown(db: Session, weeks: int = 12, end: Optional[date] = None) -> list[dict]:
    """Weekly time-in-zone (minutes) — verifies training is actually mostly Zone 1-2, not
    'grey zone' 3, for aerobic base building."""
    end = end or date.today()
    start = end - timedelta(weeks=weeks)
    activities = fetch_activities(db, start, end)

    buckets: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for a in activities:
        year, week, _ = a.start_time_local.date().isocalendar()
        key = f"{year}-W{week:02d}"
        for zone in a.hr_zones:
            if zone.time_in_zone_s and zone.zone_number:
                buckets[key][zone.zone_number] += zone.time_in_zone_s

    result = []
    for key in sorted(buckets.keys()):
        zones = buckets[key]
        row = {"week": key}
        for z in range(1, 6):
            row[f"zone_{z}_min"] = round(zones.get(z, 0.0) / 60, 1)
        result.append(row)
    return result


def wellness_series(db: Session, start: Optional[date] = None, end: Optional[date] = None) -> list[dict]:
    stmt = select(DailyWellness).order_by(DailyWellness.date.asc())
    if start:
        stmt = stmt.where(DailyWellness.date >= start)
    if end:
        stmt = stmt.where(DailyWellness.date <= end)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "date": r.date.isoformat(),
            "resting_hr": r.resting_hr,
            "hrv_avg_ms": r.hrv_last_night_avg,
            "hrv_status": r.hrv_status,
        }
        for r in rows
    ]
