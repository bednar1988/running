from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Activity, DailyWellness


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


def fetch_activities(db: Session, start: Optional[date] = None, end: Optional[date] = None) -> list[Activity]:
    stmt = select(Activity).order_by(Activity.start_time_local.asc())
    if start:
        stmt = stmt.where(Activity.start_time_local >= datetime.combine(start, datetime.min.time()))
    if end:
        stmt = stmt.where(Activity.start_time_local <= datetime.combine(end, datetime.max.time()))
    return list(db.execute(stmt).scalars().all())


def calendar_aggregate(db: Session, period: str, start: Optional[date] = None, end: Optional[date] = None) -> list[dict]:
    """Sum distance/time bucketed by calendar week/month/year (not rolling windows)."""
    activities = fetch_activities(db, start, end)
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
    activities = fetch_activities(db, None, end)
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
