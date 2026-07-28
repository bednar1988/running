import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import aggregates
import export as export_module
from db import get_session, init_db
from garmin_sync import run_full_sync
from models import Activity, HrZone, Lap
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("running.main")

FRONTEND_DIR = os.getenv("FRONTEND_DIR", "/app/frontend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Running Tracker", lifespan=lifespan)


@app.post("/api/sync")
def sync(db: Session = Depends(get_session)):
    try:
        result = run_full_sync(db)
    except Exception as e:
        logger.exception("Manual sync failed")
        raise HTTPException(status_code=502, detail=f"Garmin sync failed: {e}")
    return result


@app.get("/api/activities")
def list_activities(
    start: Optional[date] = None,
    end: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    activities = aggregates.fetch_activities(db, start, end)
    activities = sorted(activities, key=lambda a: a.start_time_local, reverse=True)
    page = activities[offset : offset + limit]
    return {
        "total": len(activities),
        "activities": [
            {
                "id": a.id,
                "date": a.start_time_local.isoformat(),
                "type": a.activity_type,
                "name": a.name,
                "distance_km": round(a.distance_m / 1000, 2),
                "duration_min": round(a.duration_s / 60, 1),
                "avg_pace_per_km": aggregates.format_pace(a.avg_pace_s_per_km),
                "avg_hr": a.avg_hr,
                "max_hr": a.max_hr,
                "cadence_spm": a.avg_cadence_spm,
                "aerobic_te": a.aerobic_te,
                "elevation_gain_m": a.elevation_gain_m,
            }
            for a in page
        ],
    }


@app.get("/api/activities/{activity_id}")
def activity_detail(activity_id: int, db: Session = Depends(get_session)):
    activity = db.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    laps = db.query(Lap).filter(Lap.activity_id == activity_id).order_by(Lap.lap_index).all()
    zones = db.query(HrZone).filter(HrZone.activity_id == activity_id).order_by(HrZone.zone_number).all()

    return {
        "id": activity.id,
        "date": activity.start_time_local.isoformat(),
        "type": activity.activity_type,
        "name": activity.name,
        "distance_km": round(activity.distance_m / 1000, 2),
        "duration_min": round(activity.duration_s / 60, 1),
        "avg_pace_per_km": aggregates.format_pace(activity.avg_pace_s_per_km),
        "avg_hr": activity.avg_hr,
        "max_hr": activity.max_hr,
        "avg_cadence_spm": activity.avg_cadence_spm,
        "max_cadence_spm": activity.max_cadence_spm,
        "elevation_gain_m": activity.elevation_gain_m,
        "elevation_loss_m": activity.elevation_loss_m,
        "calories": activity.calories,
        "aerobic_te": activity.aerobic_te,
        "aerobic_te_label": activity.aerobic_te_label,
        "anaerobic_te": activity.anaerobic_te,
        "anaerobic_te_label": activity.anaerobic_te_label,
        "vo2max_estimate": activity.vo2max_estimate,
        "laps": [
            {
                "lap_index": lap.lap_index,
                "distance_km": round(lap.distance_m / 1000, 2) if lap.distance_m else None,
                "duration_min": round(lap.duration_s / 60, 1) if lap.duration_s else None,
                "avg_pace_per_km": aggregates.format_pace(lap.avg_pace_s_per_km),
                "avg_hr": lap.avg_hr,
                "max_hr": lap.max_hr,
                "avg_cadence_spm": lap.avg_cadence_spm,
                "elevation_gain_m": lap.elevation_gain_m,
            }
            for lap in laps
        ],
        "hr_zones": [
            {
                "zone_number": z.zone_number,
                "zone_low_bpm": z.zone_low_bpm,
                "zone_high_bpm": z.zone_high_bpm,
                "time_in_zone_min": round(z.time_in_zone_s / 60, 1) if z.time_in_zone_s else None,
            }
            for z in zones
        ],
    }


@app.get("/api/aggregate")
def aggregate(
    period: str = "week",
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    if period not in ("week", "month", "year"):
        raise HTTPException(status_code=400, detail="period must be week, month, or year")
    return aggregates.calendar_aggregate(db, period, start, end)


@app.get("/api/rolling-volume")
def rolling_volume(
    window_days: int = 7,
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return aggregates.rolling_weekly_volume(db, window_days, start, end)


@app.get("/api/progression/pace-hr")
def pace_hr(
    weeks: int = 12,
    activity_type: Optional[str] = "running",
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return aggregates.pace_hr_progression(db, weeks, activity_type, end)


@app.get("/api/wellness")
def wellness(
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return aggregates.wellness_series(db, start, end)


@app.get("/api/export")
def export(
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return export_module.export_json(db, start, end)


if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
