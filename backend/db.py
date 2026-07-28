import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from models import Base

DB_PATH = os.getenv("DB_PATH", "/data/running.db")

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """create_all() only adds missing tables, not columns on tables that already exist —
    handle new columns on an already-deployed DB with plain idempotent ALTER TABLE statements."""
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(activities)")}
        if "is_ignored" not in cols:
            conn.exec_driver_sql("ALTER TABLE activities ADD COLUMN is_ignored BOOLEAN NOT NULL DEFAULT 0")
            conn.commit()


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
