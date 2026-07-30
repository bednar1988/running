import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

import aggregates
import export as export_module
import garmin_sync
from db import get_session, init_db
from garmin_sync import run_full_sync
from models import Activity, HrZone, Lap
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("running.main")

FRONTEND_DIR = os.getenv("FRONTEND_DIR", "/app/frontend")

# Changes on every container start (i.e. every redeploy), so browsers stop serving a stale
# cached app.js/style.css after a version bump instead of needing a hard-refresh.
CACHE_BUST = str(int(time.time()))


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
    limit: int = 20,  # <=0 means "all" (frontend sends 0 for its "Wszystkie" option)
    offset: int = 0,
    db: Session = Depends(get_session),
):
    # include_ignored=True: this is a management view (toggle ignore/un-ignore here), unlike
    # the HR-derived aggregates which exclude ignored activities by default.
    activities = aggregates.fetch_activities(db, start, end, include_ignored=True)
    activities = sorted(activities, key=lambda a: a.start_time_local, reverse=True)
    page = activities[offset:] if limit <= 0 else activities[offset : offset + limit]
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
                "cadence_spm": aggregates.round_int(a.avg_cadence_spm),
                "aerobic_te": aggregates.round_opt(a.aerobic_te),
                "anaerobic_te": aggregates.round_opt(a.anaerobic_te),
                "elevation_gain_m": a.elevation_gain_m,
                "is_ignored": a.is_ignored,
                "has_note": bool(a.note),
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
        "avg_cadence_spm": aggregates.round_int(activity.avg_cadence_spm),
        "max_cadence_spm": aggregates.round_int(activity.max_cadence_spm),
        "elevation_gain_m": activity.elevation_gain_m,
        "elevation_loss_m": activity.elevation_loss_m,
        "calories": activity.calories,
        "aerobic_te": aggregates.round_opt(activity.aerobic_te),
        "aerobic_te_label": activity.aerobic_te_label,
        "anaerobic_te": aggregates.round_opt(activity.anaerobic_te),
        "anaerobic_te_label": activity.anaerobic_te_label,
        "vo2max_estimate": activity.vo2max_estimate,
        "is_ignored": activity.is_ignored,
        "note": activity.note,
        "decoupling_pct": aggregates.compute_decoupling(laps),
        "weather_temp_c": aggregates.f_to_c(activity.weather_temp_c),
        "weather_humidity_pct": activity.weather_humidity_pct,
        "weather_condition": activity.weather_condition,
        "laps": [
            {
                "lap_index": lap.lap_index,
                "distance_km": round(lap.distance_m / 1000, 2) if lap.distance_m else None,
                "duration_min": round(lap.duration_s / 60, 1) if lap.duration_s else None,
                "avg_pace_per_km": aggregates.format_pace(lap.avg_pace_s_per_km),
                "avg_hr": lap.avg_hr,
                "max_hr": lap.max_hr,
                "avg_cadence_spm": aggregates.round_int(lap.avg_cadence_spm),
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


@app.post("/api/activities/{activity_id}/toggle-ignore")
def toggle_ignore(activity_id: int, db: Session = Depends(get_session)):
    """Flip is_ignored — for runs with bad HR data (e.g. watch-wrist instead of chest strap)
    that should still count toward distance/time totals but be excluded from anything
    HR-derived (pace/HR progression, Efficiency Factor, zone breakdown)."""
    activity = db.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    activity.is_ignored = not activity.is_ignored
    db.commit()
    return {"id": activity.id, "is_ignored": activity.is_ignored}


class NoteUpdate(BaseModel):
    note: str


@app.post("/api/activities/{activity_id}/note")
def update_note(activity_id: int, body: NoteUpdate, db: Session = Depends(get_session)):
    activity = db.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    activity.note = body.note or None
    db.commit()
    return {"id": activity.id, "note": activity.note}


@app.post("/api/activities/{activity_id}/resync-laps")
def resync_laps(activity_id: int, db: Session = Depends(get_session)):
    if not db.get(Activity, activity_id):
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        count = garmin_sync.resync_laps(activity_id, db)
    except Exception as e:
        logger.exception("Failed to resync laps for activity %s", activity_id)
        raise HTTPException(status_code=502, detail=f"Garmin lap fetch failed: {e}")
    return {"id": activity_id, "laps": count}


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


@app.get("/api/weekly-volume")
def weekly_volume(
    weeks: int = 12,
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return aggregates.weekly_volume_comparison(db, weeks, end)


@app.get("/api/progression/pace-hr")
def pace_hr(
    weeks: int = 12,
    activity_type: Optional[str] = "running",
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return aggregates.pace_hr_progression(db, weeks, activity_type, end)


@app.get("/api/progression/efficiency-factor")
def efficiency_factor(
    weeks: int = 26,
    activity_type: Optional[str] = "running",
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return aggregates.efficiency_factor_progression(db, weeks, activity_type, end)


@app.get("/api/hr-zones/weekly")
def hr_zones_weekly(
    weeks: int = 12,
    end: Optional[date] = None,
    db: Session = Depends(get_session),
):
    return aggregates.hr_zone_weekly_breakdown(db, weeks, end)


@app.get("/api/activities/{activity_id}/track")
def activity_track(activity_id: int, db: Session = Depends(get_session)):
    if not db.get(Activity, activity_id):
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        points = garmin_sync.get_or_fetch_track(activity_id, db)
    except Exception as e:
        logger.exception("Failed to fetch GPS track for activity %s", activity_id)
        raise HTTPException(status_code=502, detail=f"Garmin track fetch failed: {e}")
    if not points:
        raise HTTPException(status_code=404, detail="No GPS data for this activity")
    return {"points": points}


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
    _index_path = Path(FRONTEND_DIR, "index.html")

    @app.get("/", include_in_schema=False)
    def index():
        html = _index_path.read_text(encoding="utf-8")
        html = html.replace('src="/app.js"', f'src="/app.js?v={CACHE_BUST}"')
        html = html.replace('href="/style.css"', f'href="/style.css?v={CACHE_BUST}"')
        return HTMLResponse(html)

    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
