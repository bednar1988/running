from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class SyncState(Base):
    __tablename__ = "sync_state"

    id = Column(Integer, primary_key=True)
    last_sync_at = Column(DateTime)
    last_daily_wellness_date = Column(Date)

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True)  # Garmin activityId
    activity_type = Column(String, nullable=False)
    name = Column(String)
    start_time_utc = Column(DateTime, nullable=False)
    start_time_local = Column(DateTime, nullable=False, index=True)
    duration_s = Column(Float, nullable=False)
    distance_m = Column(Float, nullable=False)
    avg_pace_s_per_km = Column(Float)
    avg_hr = Column(Integer)
    max_hr = Column(Integer)
    avg_cadence_spm = Column(Float)
    max_cadence_spm = Column(Float)
    elevation_gain_m = Column(Float)
    elevation_loss_m = Column(Float)
    calories = Column(Integer)
    aerobic_te = Column(Float)
    aerobic_te_label = Column(String)
    anaerobic_te = Column(Float)
    anaerobic_te_label = Column(String)
    vo2max_estimate = Column(Float)
    synced_at = Column(DateTime, nullable=False)
    is_ignored = Column(Boolean, nullable=False, default=False, server_default="0")
    # JSON-encoded [[lat, lon], ...] — NULL means "never fetched", "[]" means "fetched, no GPS"
    track_points_json = Column(Text)
    weather_temp_c = Column(Float)  # despite the name, Garmin returns this in Fahrenheit — convert at display time
    weather_humidity_pct = Column(Integer)
    weather_condition = Column(String)
    note = Column(Text)  # user's own free-text note, not from Garmin
    # JSON-encoded [[cumulative_distance_m, elapsed_s], ...] sample stream from
    # get_activity_details(), used for best-effort (PR) search — NULL = never fetched,
    # "[]" = fetched but unusable (no distance/time channels on this activity)
    stream_json = Column(Text)

    laps = relationship("Lap", back_populates="activity", cascade="all, delete-orphan")
    hr_zones = relationship("HrZone", back_populates="activity", cascade="all, delete-orphan")


class HrZone(Base):
    __tablename__ = "hr_zones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(Integer, ForeignKey("activities.id", ondelete="CASCADE"), nullable=False)
    zone_number = Column(Integer, nullable=False)
    zone_low_bpm = Column(Integer)
    zone_high_bpm = Column(Integer)
    time_in_zone_s = Column(Float)

    activity = relationship("Activity", back_populates="hr_zones")

    __table_args__ = (UniqueConstraint("activity_id", "zone_number", name="uq_hrzone_activity_zone"),)


class Lap(Base):
    __tablename__ = "laps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(Integer, ForeignKey("activities.id", ondelete="CASCADE"), nullable=False)
    lap_index = Column(Integer, nullable=False)
    distance_m = Column(Float)
    duration_s = Column(Float)
    avg_pace_s_per_km = Column(Float)
    avg_hr = Column(Integer)
    max_hr = Column(Integer)
    avg_cadence_spm = Column(Float)
    elevation_gain_m = Column(Float)
    elevation_loss_m = Column(Float)

    activity = relationship("Activity", back_populates="laps")

    __table_args__ = (UniqueConstraint("activity_id", "lap_index", name="uq_lap_activity_index"),)


class DailyWellness(Base):
    __tablename__ = "daily_wellness"

    date = Column(Date, primary_key=True)
    resting_hr = Column(Integer)
    hrv_last_night_avg = Column(Float)
    hrv_status = Column(String)
    synced_at = Column(DateTime, nullable=False)
