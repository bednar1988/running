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
import plan
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
                "calories": a.calories,
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
                "elevation_loss_m": lap.elevation_loss_m,
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


@app.post("/api/activities/resync-laps")
def resync_laps_all(db: Session = Depends(get_session)):
    """Force a fresh laps/hr-zones re-fetch for every already-synced running activity — for
    backfilling a field-mapping fix (cadence, elevation_loss_m, ...) across historical data at
    once, instead of hitting the single-activity endpoint one id at a time. One activity failing
    (rate limit, network blip) doesn't stop the rest; failures are reported, not raised."""
    activities = (
        db.query(Activity)
        .filter(Activity.activity_type.contains("running"))
        .order_by(Activity.start_time_local.desc())
        .all()
    )
    succeeded = 0
    failed = []
    for activity in activities:
        try:
            garmin_sync.resync_laps(activity.id, db)
            succeeded += 1
        except Exception:
            logger.exception("Failed to resync laps for activity %s", activity.id)
            failed.append(activity.id)
    return {"total": len(activities), "succeeded": succeeded, "failed": failed}


@app.post("/api/wellness/resync")
def resync_wellness(start: date, end: Optional[date] = None, db: Session = Depends(get_session)):
    """Force-refetch resting HR / HRV for an explicit date range — for filling gaps left by a
    past sync failure (e.g. a Garmin-side outage) without waiting for the normal watermark-driven
    sync to work its way back that far. `end` defaults to today."""
    end = end or date.today()
    if start > end:
        raise HTTPException(status_code=400, detail="start must be <= end")
    return garmin_sync.resync_wellness_range(db, start, end)


@app.get("/api/records")
def personal_records(db: Session = Depends(get_session)):
    """Best-effort (PR) search per standard distance — the same algorithm Garmin itself uses:
    minimum elapsed time to cover the distance starting anywhere within an activity, not just
    lap-aligned or whole-activity averages. Streams are fetched from Garmin once per activity
    and cached (activities.stream_json); a cold cache can take a while the first time this is
    requested, but every activity is only ever fetched once.

    Also returns the full record *progression* per distance (every activity, in chronological
    order, that beat the previous best) so the current PR isn't the only thing visible — the
    history of how it got there is too."""
    activities = [a for a in aggregates.fetch_activities(db, include_ignored=True) if a.activity_type == "running"]

    progressions: dict[str, list[dict]] = {label: [] for label, _ in aggregates.BEST_EFFORT_DISTANCES}
    for a in activities:  # fetch_activities orders by start_time_local ascending
        applicable = [(label, target_m) for label, target_m in aggregates.BEST_EFFORT_DISTANCES if a.distance_m >= target_m]
        if not applicable:
            continue
        stream = garmin_sync.get_or_fetch_stream(a.id, db)
        if not stream:
            continue
        for label, target_m in applicable:
            seconds = aggregates.best_effort_seconds(stream, target_m)
            if seconds is None:
                continue
            history = progressions[label]
            if not history or seconds < history[-1]["seconds"]:
                history.append({"seconds": seconds, "activity_id": a.id, "date": a.start_time_local.date().isoformat()})

    def entry(e: dict, target_m: float) -> dict:
        return {
            "activity_id": e["activity_id"],
            "date": e["date"],
            "time": aggregates.format_duration(e["seconds"]),
            "pace_per_km": aggregates.format_pace(e["seconds"] / (target_m / 1000)),
        }

    result = []
    for label, target_m in aggregates.BEST_EFFORT_DISTANCES:
        history = progressions[label]
        current = entry(history[-1], target_m) if history else {"time": None, "pace_per_km": None, "activity_id": None, "date": None}
        result.append({"label": label, **current, "progression": [entry(e, target_m) for e in history]})
    return result


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


# --- Training plan ----------------------------------------------------------
# A reusable block library (PlanBlockTemplate) placed onto calendar days (PlanBlock) — purely
# manual planning for now, no link to synced activities.


class TemplateCreate(BaseModel):
    name: str
    block_type: str
    zone: Optional[int] = None
    volume_text: Optional[str] = None
    note: Optional[str] = None


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    block_type: Optional[str] = None
    zone: Optional[int] = None
    volume_text: Optional[str] = None
    note: Optional[str] = None


@app.get("/api/plan/templates")
def list_plan_templates(db: Session = Depends(get_session)):
    return plan.list_templates(db)


@app.post("/api/plan/templates")
def create_plan_template(body: TemplateCreate, db: Session = Depends(get_session)):
    return plan.create_template(db, body.name, body.block_type, body.zone, body.volume_text, body.note)


@app.patch("/api/plan/templates/{template_id}")
def update_plan_template(template_id: int, body: TemplateUpdate, db: Session = Depends(get_session)):
    result = plan.update_template(db, template_id, body.model_dump(exclude_unset=True))
    if result is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return result


@app.delete("/api/plan/templates/{template_id}")
def delete_plan_template(template_id: int, db: Session = Depends(get_session)):
    deleted, error = plan.delete_template(db, template_id)
    if not deleted:
        raise HTTPException(status_code=400, detail=error)
    return {"deleted": True}


class BlockCreate(BaseModel):
    day: date
    template_id: int
    note: Optional[str] = None


class BlockUpdate(BaseModel):
    note: Optional[str] = None
    sort_order: Optional[int] = None


@app.get("/api/plan")
def list_plan_blocks(start: date, end: date, db: Session = Depends(get_session)):
    return plan.list_blocks(db, start, end)


@app.post("/api/plan")
def create_plan_block(body: BlockCreate, db: Session = Depends(get_session)):
    result = plan.create_block(db, body.day, body.template_id, body.note)
    if result is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return result


@app.patch("/api/plan/{block_id}")
def update_plan_block(block_id: int, body: BlockUpdate, db: Session = Depends(get_session)):
    result = plan.update_block(db, block_id, body.model_dump(exclude_unset=True))
    if result is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return result


@app.delete("/api/plan/{block_id}")
def delete_plan_block(block_id: int, db: Session = Depends(get_session)):
    if not plan.delete_block(db, block_id):
        raise HTTPException(status_code=404, detail="Block not found")
    return {"deleted": True}


class CopyWeekRequest(BaseModel):
    from_monday: date
    to_monday: date


@app.post("/api/plan/copy-week")
def copy_plan_week(body: CopyWeekRequest, db: Session = Depends(get_session)):
    return plan.copy_week(db, body.from_monday, body.to_monday)


if os.path.isdir(FRONTEND_DIR):
    _index_path = Path(FRONTEND_DIR, "index.html")

    @app.get("/", include_in_schema=False)
    def index():
        html = _index_path.read_text(encoding="utf-8")
        html = html.replace('src="/app.js"', f'src="/app.js?v={CACHE_BUST}"')
        html = html.replace('href="/style.css"', f'href="/style.css?v={CACHE_BUST}"')
        return HTMLResponse(html)

    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
