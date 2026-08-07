from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import Activity, PlanBlock, PlanBlockTemplate


def _template_dict(t: PlanBlockTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "block_type": t.block_type,
        "zone": t.zone,
        "volume_text": t.volume_text,
        "note": t.note,
    }


def list_templates(db: Session) -> list[dict]:
    templates = db.query(PlanBlockTemplate).order_by(PlanBlockTemplate.name).all()
    return [_template_dict(t) for t in templates]


def create_template(
    db: Session,
    name: str,
    block_type: str,
    zone: Optional[int] = None,
    volume_text: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    t = PlanBlockTemplate(
        name=name,
        block_type=block_type,
        zone=zone,
        volume_text=volume_text,
        note=note,
        created_at=datetime.utcnow(),
    )
    db.add(t)
    db.commit()
    return _template_dict(t)


def update_template(db: Session, template_id: int, fields: dict) -> Optional[dict]:
    t = db.get(PlanBlockTemplate, template_id)
    if not t:
        return None
    for key, value in fields.items():
        setattr(t, key, value)
    db.commit()
    return _template_dict(t)


def delete_template(db: Session, template_id: int) -> tuple[bool, Optional[str]]:
    """Returns (deleted, error_message). Refuses to delete a template still placed on any day —
    cleaning up the library shouldn't silently blow a hole in an already-laid-out plan."""
    t = db.get(PlanBlockTemplate, template_id)
    if not t:
        return False, "Szablon nie istnieje."
    count = db.query(PlanBlock).filter(PlanBlock.template_id == template_id).count()
    if count:
        return False, f"Szablon jest użyty w {count} dniach — usuń najpierw te przypisania."
    db.delete(t)
    db.commit()
    return True, None


def _block_dict(b: PlanBlock, done: bool = False) -> dict:
    t = b.template
    return {
        "id": b.id,
        "day": b.day.isoformat(),
        "template_id": b.template_id,
        "name": t.name,
        "block_type": t.block_type,
        "zone": t.zone,
        "volume_text": t.volume_text,
        "note": b.note if b.note is not None else t.note,
        "sort_order": b.sort_order,
        "done": done,
    }


def list_blocks(db: Session, start: date, end: date) -> list[dict]:
    blocks = (
        db.query(PlanBlock)
        .join(PlanBlockTemplate)
        .filter(PlanBlock.day >= start, PlanBlock.day <= end)
        .order_by(PlanBlock.day, PlanBlock.sort_order)
        .all()
    )
    # "Done" is deliberately presence-based, not a match against the block's prescribed
    # zone/volume — matching real splits against a plan is fragile and not worth the noise. A
    # running activity on the calendar day is a good-enough signal, checked either way (ignored
    # ones still count: the user still ran, that's what matters here).
    activity_days = {
        a.start_time_local.date()
        for a in db.query(Activity.start_time_local)
        .filter(
            Activity.activity_type == "running",
            Activity.start_time_local >= datetime.combine(start, time.min),
            Activity.start_time_local <= datetime.combine(end, time.max),
        )
        .all()
    }
    return [_block_dict(b, b.day in activity_days) for b in blocks]


def create_block(db: Session, day: date, template_id: int, note: Optional[str] = None) -> Optional[dict]:
    template = db.get(PlanBlockTemplate, template_id)
    if not template:
        return None
    next_order = db.query(PlanBlock).filter(PlanBlock.day == day).count()
    b = PlanBlock(day=day, template_id=template_id, note=note, sort_order=next_order, created_at=datetime.utcnow())
    db.add(b)
    db.commit()
    return _block_dict(b)


def update_block(db: Session, block_id: int, fields: dict) -> Optional[dict]:
    b = db.get(PlanBlock, block_id)
    if not b:
        return None
    for key, value in fields.items():
        setattr(b, key, value)
    db.commit()
    return _block_dict(b)


def delete_block(db: Session, block_id: int) -> bool:
    b = db.get(PlanBlock, block_id)
    if not b:
        return False
    db.delete(b)
    db.commit()
    return True


def copy_week(db: Session, from_monday: date, to_monday: date) -> list[dict]:
    """Copies every block placed on from_monday..+6 onto the matching weekdays of
    to_monday..+6. Appends rather than replaces — existing blocks already in the target week
    are left alone, copies stack after them via sort_order."""
    from_end = from_monday + timedelta(days=6)
    blocks = (
        db.query(PlanBlock)
        .filter(PlanBlock.day >= from_monday, PlanBlock.day <= from_end)
        .order_by(PlanBlock.day, PlanBlock.sort_order)
        .all()
    )
    offset = (to_monday - from_monday).days
    next_order: dict[date, int] = {}
    created = []
    for b in blocks:
        new_day = b.day + timedelta(days=offset)
        if new_day not in next_order:
            next_order[new_day] = db.query(PlanBlock).filter(PlanBlock.day == new_day).count()
        order = next_order[new_day]
        next_order[new_day] = order + 1
        nb = PlanBlock(day=new_day, template_id=b.template_id, note=b.note, sort_order=order, created_at=datetime.utcnow())
        db.add(nb)
        created.append(nb)
    db.commit()
    return [_block_dict(b) for b in created]
