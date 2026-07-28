from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from aggregates import fetch_activities, format_pace, pace_hr_progression, round_opt, wellness_series


def export_json(db: Session, start: Optional[date] = None, end: Optional[date] = None) -> dict:
    """Compact, pre-processed export for pasting into a Claude chat — not a raw Garmin dump."""
    activities = fetch_activities(db, start, end)

    total_distance_m = sum(a.distance_m for a in activities)
    total_duration_s = sum(a.duration_s for a in activities)

    activity_list = [
        {
            "date": a.start_time_local.date().isoformat(),
            "type": a.activity_type,
            "distance_km": round(a.distance_m / 1000, 2),
            "duration_min": round(a.duration_s / 60, 1),
            "avg_pace_per_km": format_pace(a.avg_pace_s_per_km),
            "avg_hr": a.avg_hr,
            "max_hr": a.max_hr,
            "cadence_spm": a.avg_cadence_spm,
            "aerobic_te": round_opt(a.aerobic_te),
            "anaerobic_te": round_opt(a.anaerobic_te),
            "elevation_gain_m": a.elevation_gain_m,
        }
        for a in activities
    ]

    return {
        "range": {
            "start": start.isoformat() if start else (activities[0].start_time_local.date().isoformat() if activities else None),
            "end": end.isoformat() if end else date.today().isoformat(),
        },
        "summary": {
            "total_distance_km": round(total_distance_m / 1000, 1),
            "total_time_h": round(total_duration_s / 3600, 1),
            "activity_count": len(activities),
            "avg_pace_per_km": format_pace(total_duration_s / total_distance_m * 1000) if total_distance_m else None,
        },
        "activities": activity_list,
        "weekly_pace_hr_progression": pace_hr_progression(db, weeks=52, end=end),
        "wellness": wellness_series(db, start, end),
    }
