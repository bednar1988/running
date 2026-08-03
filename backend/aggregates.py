from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Activity, DailyWellness


def round_opt(value: Optional[float], ndigits: int = 1) -> Optional[float]:
    """Garmin's training-effect floats arrive with float32 noise (e.g. 3.0999999046325684)."""
    return round(value, ndigits) if value is not None else None


def round_int(value: Optional[float]) -> Optional[int]:
    """Cadence (steps/min) is a whole number — Garmin returns it as a noisy float."""
    return round(value) if value is not None else None


def f_to_c(fahrenheit: Optional[float]) -> Optional[float]:
    """Garmin's activity weather endpoint returns temperature in Fahrenheit regardless of
    account locale — Activity.weather_temp_c is stored as-received (raw F) and converted here."""
    return round((fahrenheit - 32) * 5 / 9, 1) if fahrenheit is not None else None


BEST_EFFORT_DISTANCES = [
    ("1 km", 1000.0),
    ("5 km", 5000.0),
    ("10 km", 10000.0),
    ("15 km", 15000.0),
    ("Półmaraton", 21097.5),
    ("Maraton", 42195.0),
]


def best_effort_seconds(stream: list[tuple[float, float]], target_m: float) -> Optional[float]:
    """Minimum elapsed time to cover target_m starting anywhere in the activity — the same
    "best effort" search Garmin itself runs for personal records, not just a whole-activity or
    lap-aligned average. `stream` is (cumulative_distance_m, elapsed_s) samples sorted by time;
    the exact crossing point is linearly interpolated between the two straddling samples.
    O(n) two-pointer since both the start index and its target-reaching index only move forward."""
    n = len(stream)
    if n < 2 or stream[-1][0] - stream[0][0] < target_m:
        return None

    best = None
    j = 1
    for i in range(n - 1):
        dist_i, time_i = stream[i]
        target_dist = dist_i + target_m
        if j < i + 1:
            j = i + 1
        while j < n and stream[j][0] < target_dist:
            j += 1
        if j >= n:
            break

        d0, t0 = stream[j - 1]
        d1, t1 = stream[j]
        time_at_target = t0 + (target_dist - d0) / (d1 - d0) * (t1 - t0) if d1 > d0 else t1

        elapsed = time_at_target - time_i
        if elapsed > 0 and (best is None or elapsed < best):
            best = elapsed

    return best


def format_pace(seconds_per_km: Optional[float]) -> Optional[str]:
    if seconds_per_km is None:
        return None
    minutes, seconds = divmod(round(seconds_per_km), 60)
    return f"{minutes}:{seconds:02d}"


def format_duration(total_seconds: Optional[float]) -> Optional[str]:
    if total_seconds is None:
        return None
    total = round(total_seconds)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
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
    instead of chest strap) poisons those. Distance/time-only aggregates (calendar totals, weekly
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
    buckets: dict[str, dict] = defaultdict(lambda: {"distance_m": 0.0, "duration_s": 0.0, "count": 0, "calories": 0})

    for a in activities:
        key = _period_key(a.start_time_local.date(), period)
        buckets[key]["distance_m"] += a.distance_m
        buckets[key]["duration_s"] += a.duration_s
        buckets[key]["count"] += 1
        buckets[key]["calories"] += a.calories or 0

    result = []
    for key in sorted(buckets.keys()):
        b = buckets[key]
        result.append(
            {
                "period": key,
                "distance_km": round(b["distance_m"] / 1000, 2),
                "duration_h": round(b["duration_s"] / 3600, 2),
                "activity_count": b["count"],
                "calories": b["calories"],
                "avg_pace_per_km": format_pace(b["duration_s"] / b["distance_m"] * 1000) if b["distance_m"] else None,
            }
        )
    return result


def weekly_volume_comparison(db: Session, weeks: int = 12, end: Optional[date] = None) -> list[dict]:
    """Calendar-week (Mon-Sun) distance totals, each compared to the immediately preceding
    calendar week — the "max 10% weekly progression" check. Deliberately NOT a rolling/trailing
    window: a day-by-day rolling sum swings noisily (a run falling in/out of the trailing 7 days
    can flip the % between runs), while calendar weeks give one stable comparison per week."""
    end = end or date.today()
    end_monday = end - timedelta(days=end.weekday())
    range_start_monday = end_monday - timedelta(weeks=weeks - 1)
    fetch_start = range_start_monday - timedelta(days=7)  # one extra week for the first % change

    activities = fetch_activities(db, fetch_start, end, include_ignored=True)

    weekly_distance: dict[date, float] = defaultdict(float)  # keyed by that week's Monday
    for a in activities:
        d = a.start_time_local.date()
        monday = d - timedelta(days=d.weekday())
        weekly_distance[monday] += a.distance_m

    result = []
    monday = range_start_monday
    while monday <= end_monday:
        this_week = weekly_distance.get(monday, 0.0)
        prior_week = weekly_distance.get(monday - timedelta(days=7), 0.0)
        pct_change = round((this_week - prior_week) / prior_week * 100, 1) if prior_week > 0 else None
        year, iso_week, _ = monday.isocalendar()

        result.append(
            {
                "week": f"{year}-W{iso_week:02d}",
                "distance_km": round(this_week / 1000, 2),
                "pct_change_vs_prior_week": pct_change,
            }
        )
        monday += timedelta(days=7)

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
