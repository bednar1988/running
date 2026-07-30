"""Garmin Connect sync: login, delta-fetch activities/laps/hr-zones/daily wellness, upsert into SQLite.

python-garminconnect has no official field-name documentation (community reverse-engineered
API), so summary/lap/zone/wellness parsing below tries a short list of candidate key names per
field and falls back to None rather than crashing the whole sync on one unexpected shape. Verify
`_dig` candidate lists against a real account and trim/extend them once confirmed (see README).
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any, Optional

from garminconnect import Garmin
from sqlalchemy.orm import Session

from models import Activity, DailyWellness, HrZone, Lap, SyncState

logger = logging.getLogger("running.garmin_sync")

TOKEN_DIR = os.getenv("GARMIN_TOKEN_DIR", "/data/garmin_tokens")
ACTIVITIES_PAGE_SIZE = 20
MAX_PAGES = 100  # hard stop so a parsing bug can't loop forever on first full-history sync
BACKFILL_WELLNESS_DAYS = 90  # how far back to pull resting HR / HRV on first sync

_client: Optional[Garmin] = None


def _sync_start_date() -> Optional[date]:
    """Earliest date to pull data for (e.g. when serious training started) — everything older
    is ignored, so old sporadic one-off activities from past years aren't dragged in."""
    raw = os.getenv("SYNC_START_DATE")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        logger.warning("Invalid SYNC_START_DATE=%r (expected YYYY-MM-DD), ignoring", raw)
        return None


def _dig(d: dict, *candidates: str) -> Any:
    """Try dotted key paths in order (e.g. 'a.b.0.c', numeric segments index into lists),
    return the first that resolves to a non-None value, else None."""
    for path in candidates:
        node = d
        for key in path.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            elif isinstance(node, list) and key.lstrip("-").isdigit():
                idx = int(key)
                node = node[idx] if -len(node) <= idx < len(node) else None
            else:
                node = None
            if node is None:
                break
        if node is not None:
            return node
    return None


def get_client() -> Garmin:
    global _client
    if _client is not None:
        return _client
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]
    client = Garmin(email, password)
    client.login(TOKEN_DIR)
    _client = client
    return client


def _round_te(value: Optional[float]) -> Optional[float]:
    """Garmin's training-effect floats arrive with float32 noise (e.g. 3.0999999046325684)."""
    return round(value, 1) if value is not None else None


def _round_cadence(value: Optional[float]) -> Optional[float]:
    """Cadence (steps/min) is a whole number — Garmin returns it as a noisy float."""
    return round(value) if value is not None else None


def _parse_activity(raw: dict) -> dict:
    activity_type = _dig(raw, "activityType.typeKey") or "unknown"
    start_local_raw = _dig(raw, "startTimeLocal")
    start_gmt_raw = _dig(raw, "startTimeGMT")
    duration_s = _dig(raw, "movingDuration", "duration") or 0.0
    distance_m = _dig(raw, "distance") or 0.0

    return {
        "id": int(raw["activityId"]),
        "activity_type": activity_type,
        "name": _dig(raw, "activityName"),
        "start_time_local": _parse_dt(start_local_raw),
        "start_time_utc": _parse_dt(start_gmt_raw) or _parse_dt(start_local_raw),
        "duration_s": duration_s,
        "distance_m": distance_m,
        "avg_pace_s_per_km": (duration_s / distance_m * 1000) if distance_m else None,
        "avg_hr": _dig(raw, "averageHR"),
        "max_hr": _dig(raw, "maxHR"),
        "avg_cadence_spm": _round_cadence(_dig(raw, "averageRunningCadenceInStepsPerMinute")),
        "max_cadence_spm": _round_cadence(_dig(raw, "maxRunningCadenceInStepsPerMinute")),
        "elevation_gain_m": _dig(raw, "elevationGain"),
        "elevation_loss_m": _dig(raw, "elevationLoss"),
        "calories": _dig(raw, "calories"),
        "aerobic_te": _round_te(_dig(raw, "aerobicTrainingEffect")),
        "aerobic_te_label": _dig(raw, "aerobicTrainingEffectMessage"),
        "anaerobic_te": _round_te(_dig(raw, "anaerobicTrainingEffect")),
        "anaerobic_te_label": _dig(raw, "anaerobicTrainingEffectMessage"),
        "vo2max_estimate": _dig(raw, "vO2MaxValue"),
        "synced_at": datetime.utcnow(),
    }


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    logger.warning("Unrecognized datetime format from Garmin: %r", value)
    return None


def _parse_laps(activity_id: int, raw: dict) -> list[dict]:
    lap_dtos = _dig(raw, "lapDTOs") or []
    laps = []
    any_cadence = False
    for idx, lap in enumerate(lap_dtos, start=1):
        duration_s = _dig(lap, "movingDuration", "duration") or 0.0
        distance_m = _dig(lap, "distance") or 0.0
        cadence = _dig(
            lap,
            "averageRunningCadenceInStepsPerMinute",
            "averageRunCadence",
            "avgRunCadence",
            "averageBikingCadenceInRevPerMinute",
        )
        any_cadence = any_cadence or cadence is not None
        laps.append(
            {
                "activity_id": activity_id,
                "lap_index": idx,
                "distance_m": distance_m,
                "duration_s": duration_s,
                "avg_pace_s_per_km": (duration_s / distance_m * 1000) if distance_m else None,
                "avg_hr": _dig(lap, "averageHR"),
                "max_hr": _dig(lap, "maxHR"),
                "avg_cadence_spm": _round_cadence(cadence),
                "elevation_gain_m": _dig(lap, "elevationGain"),
            }
        )

    if lap_dtos and not any_cadence:
        logger.warning(
            "No cadence field matched any candidate key on activity %s laps; raw first lap: %s",
            activity_id,
            lap_dtos[0],
        )

    return laps


def resync_laps(activity_id: int, db: Session) -> int:
    """Re-fetch and overwrite laps for one already-synced activity — used to pick up a field-
    mapping fix (like the cadence key above) without wiping/re-pulling the whole activity."""
    client = get_client()
    raw = client.get_activity_splits(str(activity_id))
    db.query(Lap).filter(Lap.activity_id == activity_id).delete()
    laps = _parse_laps(activity_id, raw)
    for lap in laps:
        db.add(Lap(**lap))
    db.commit()
    return len(laps)


def _parse_hr_zones(activity_id: int, raw: Any) -> list[dict]:
    zones_raw = raw if isinstance(raw, list) else _dig(raw, "hrTimeInZones") or []
    zones_raw = sorted(zones_raw, key=lambda z: _dig(z, "zoneNumber") or 0)
    zones = []
    for i, z in enumerate(zones_raw):
        low = _dig(z, "zoneLowBoundary")
        high = None
        if i + 1 < len(zones_raw):
            high = _dig(zones_raw[i + 1], "zoneLowBoundary")
        zones.append(
            {
                "activity_id": activity_id,
                "zone_number": _dig(z, "zoneNumber"),
                "zone_low_bpm": low,
                "zone_high_bpm": high,
                "time_in_zone_s": _dig(z, "secsInZone"),
            }
        )
    return zones


def _parse_rhr(raw: dict) -> Optional[int]:
    value = _dig(
        raw,
        "allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE.0.value",
        "restingHeartRate",
    )
    return int(value) if value is not None else None


def _parse_hrv(raw: dict) -> tuple[Optional[float], Optional[str]]:
    if raw is None:
        return None, None
    avg = _dig(raw, "hrvSummary.lastNightAvg", "lastNightAvg")
    status = _dig(raw, "hrvSummary.status", "status")
    return avg, status


def _parse_weather(activity_id: int, raw: dict) -> dict:
    temp = _dig(raw, "temp", "temperature", "apparentTemp")
    humidity = _dig(raw, "relativeHumidity", "humidity")
    condition = _dig(raw, "weatherTypeDTO.desc", "conditions", "weatherCondition", "conditionDescription")
    if temp is None and humidity is None and condition is None:
        logger.warning("No weather field matched any candidate key for activity %s; raw: %s", activity_id, raw)
    return {
        "weather_temp_c": temp,
        "weather_humidity_pct": int(humidity) if humidity is not None else None,
        "weather_condition": condition,
    }


def sync_activities(db: Session) -> int:
    """Fetch new activities newest-first, stop at the first one already stored. Returns count added.

    Fetches ALL activity types (not just activitytype="running") so trail runs / indoor runs
    aren't missed by an exact typeKey filter, but only persists running-family activities.
    """
    client = get_client()
    existing_ids = {row.id for row in db.query(Activity.id).all()}
    start_date = _sync_start_date()
    added = 0

    for page in range(MAX_PAGES):
        start = page * ACTIVITIES_PAGE_SIZE
        batch = client.get_activities(start=start, limit=ACTIVITIES_PAGE_SIZE)
        if not batch:
            break

        hit_known = False
        past_start_date = False
        for raw in batch:
            activity_id = int(raw["activityId"])
            if activity_id in existing_ids:
                hit_known = True
                continue

            activity_date = _parse_dt(_dig(raw, "startTimeLocal"))
            if start_date and activity_date and activity_date.date() < start_date:
                # Activities are returned newest-first, so everything from here on
                # (this batch and all further pages) is even older — stop entirely.
                past_start_date = True
                break

            type_key = _dig(raw, "activityType.typeKey") or ""
            if "running" not in type_key:
                continue

            parsed = _parse_activity(raw)
            activity = Activity(**parsed)
            db.add(activity)
            db.flush()

            try:
                splits_raw = client.get_activity_splits(str(activity_id))
                for lap in _parse_laps(activity_id, splits_raw):
                    db.add(Lap(**lap))
            except Exception:
                logger.exception("Failed to fetch/parse laps for activity %s", activity_id)

            try:
                zones_raw = client.get_activity_hr_in_timezones(str(activity_id))
                for zone in _parse_hr_zones(activity_id, zones_raw):
                    if zone["zone_number"] is not None:
                        db.add(HrZone(**zone))
            except Exception:
                logger.exception("Failed to fetch/parse HR zones for activity %s", activity_id)

            try:
                weather_raw = client.get_activity_weather(str(activity_id))
                for key, value in _parse_weather(activity_id, weather_raw).items():
                    setattr(activity, key, value)
            except Exception:
                logger.exception("Failed to fetch/parse weather for activity %s", activity_id)

            existing_ids.add(activity_id)
            added += 1

        db.commit()
        if hit_known or past_start_date or len(batch) < ACTIVITIES_PAGE_SIZE:
            break

    return added


def sync_daily_wellness(db: Session) -> int:
    client = get_client()
    state = db.get(SyncState, 1)

    if state and state.last_daily_wellness_date:
        start_day = state.last_daily_wellness_date + timedelta(days=1)
    else:
        earliest_activity = db.query(Activity).order_by(Activity.start_time_local.asc()).first()
        if earliest_activity:
            start_day = earliest_activity.start_time_local.date()
        else:
            start_day = date.today() - timedelta(days=BACKFILL_WELLNESS_DAYS)

    sync_start = _sync_start_date()
    if sync_start and start_day < sync_start:
        start_day = sync_start

    today = date.today()
    if start_day > today:
        return 0

    added = 0
    day = start_day
    while day <= today:
        cdate = day.isoformat()
        try:
            rhr_raw = client.get_rhr_day(cdate)
            resting_hr = _parse_rhr(rhr_raw)
            if resting_hr is None:
                logger.warning("Resting HR parse returned None for %s; raw response: %s", cdate, rhr_raw)
        except Exception:
            logger.exception("Failed to fetch resting HR for %s", cdate)
            resting_hr = None

        try:
            hrv_raw = client.get_hrv_data(cdate)
            hrv_avg, hrv_status = _parse_hrv(hrv_raw)
            if hrv_avg is None:
                logger.warning("HRV parse returned None for %s; raw response: %s", cdate, hrv_raw)
        except Exception:
            logger.exception("Failed to fetch HRV for %s", cdate)
            hrv_avg, hrv_status = None, None

        existing = db.get(DailyWellness, day)
        if existing:
            existing.resting_hr = resting_hr
            existing.hrv_last_night_avg = hrv_avg
            existing.hrv_status = hrv_status
            existing.synced_at = datetime.utcnow()
        else:
            db.add(
                DailyWellness(
                    date=day,
                    resting_hr=resting_hr,
                    hrv_last_night_avg=hrv_avg,
                    hrv_status=hrv_status,
                    synced_at=datetime.utcnow(),
                )
            )
            added += 1

        day += timedelta(days=1)

    if not state:
        state = SyncState(id=1)
        db.add(state)
    state.last_daily_wellness_date = today
    state.last_sync_at = datetime.utcnow()
    db.commit()

    return added


def run_full_sync(db: Session) -> dict:
    n_activities = sync_activities(db)
    n_wellness = sync_daily_wellness(db)
    return {"new_activities": n_activities, "wellness_days_synced": n_wellness}


def fetch_activity_track(activity_id: int) -> Optional[list[tuple[float, float]]]:
    """Fetch the GPS track for one activity straight from Garmin."""
    client = get_client()
    raw = client.download_activity(str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.GPX)
    if not raw:
        return None

    root = ET.fromstring(raw)
    points = []
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        lat = elem.get("lat")
        lon = elem.get("lon")
        if lat is not None and lon is not None:
            points.append((float(lat), float(lon)))

    return points or None


def get_or_fetch_track(activity_id: int, db: Session) -> Optional[list[tuple[float, float]]]:
    """Cached wrapper: reads activities.track_points_json if already fetched, else pulls from
    Garmin once and stores the result (including a cached "no GPS" negative as "[]", so treadmill/
    indoor activities aren't re-queried on every row expansion)."""
    activity = db.get(Activity, activity_id)
    if activity is None:
        return None

    if activity.track_points_json is not None:
        points = json.loads(activity.track_points_json)
        return [tuple(p) for p in points] or None

    points = fetch_activity_track(activity_id)
    activity.track_points_json = json.dumps(points or [])
    db.commit()
    return points
