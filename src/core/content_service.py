from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from db.models import AnnouncementEntry, FaqEntry, V2User


def _utc_now_naive() -> datetime:
    return datetime.utcnow()


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() + "Z"


def _serialize_announcement(item: AnnouncementEntry) -> dict:
    return {
        "id": str(item.id),
        "title": item.title,
        "content": item.content,
        "display_mode": item.display_mode,
        "show_in_overview": bool(item.show_in_overview),
        "sort_order": int(item.sort_order or 0),
        "is_published": bool(item.is_published),
        "published_at": _serialize_datetime(item.published_at),
        "created_at": _serialize_datetime(item.created_at),
        "updated_at": _serialize_datetime(item.updated_at),
        "updated_by_username": item.updated_by_username,
    }


def _serialize_faq(item: FaqEntry) -> dict:
    return {
        "id": str(item.id),
        "title": item.title,
        "content": item.content,
        "sort_order": int(item.sort_order or 0),
        "is_published": bool(item.is_published),
        "published_at": _serialize_datetime(item.published_at),
        "created_at": _serialize_datetime(item.created_at),
        "updated_at": _serialize_datetime(item.updated_at),
        "updated_by_username": item.updated_by_username,
    }


def list_admin_announcements_payload(db: Session) -> dict:
    items = (
        db.query(AnnouncementEntry)
        .order_by(AnnouncementEntry.sort_order.asc(), AnnouncementEntry.updated_at.desc(), AnnouncementEntry.id.desc())
        .all()
    )
    return {"items": [_serialize_announcement(item) for item in items]}


def list_admin_faqs_payload(db: Session) -> dict:
    items = (
        db.query(FaqEntry)
        .order_by(FaqEntry.sort_order.asc(), FaqEntry.updated_at.desc(), FaqEntry.id.desc())
        .all()
    )
    return {"items": [_serialize_faq(item) for item in items]}


def list_published_announcements_payload(db: Session) -> list[dict]:
    items = (
        db.query(AnnouncementEntry)
        .filter(AnnouncementEntry.is_published == True)  # noqa: E712
        .order_by(AnnouncementEntry.published_at.desc(), AnnouncementEntry.sort_order.asc(), AnnouncementEntry.id.desc())
        .all()
    )
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "content": item.content,
            "displayMode": item.display_mode,
            "showInOverview": bool(item.show_in_overview),
            "sortOrder": int(item.sort_order or 0),
            "publishedAt": _serialize_datetime(item.published_at),
            "updatedAt": _serialize_datetime(item.updated_at),
        }
        for item in items
    ]


def list_published_faqs_payload(db: Session) -> list[dict]:
    items = (
        db.query(FaqEntry)
        .filter(FaqEntry.is_published == True)  # noqa: E712
        .order_by(FaqEntry.sort_order.asc(), FaqEntry.published_at.desc(), FaqEntry.id.desc())
        .all()
    )
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "content": item.content,
            "sortOrder": int(item.sort_order or 0),
            "publishedAt": _serialize_datetime(item.published_at),
            "updatedAt": _serialize_datetime(item.updated_at),
        }
        for item in items
    ]


def create_announcement(db: Session, actor: V2User, payload) -> dict:
    now = _utc_now_naive()
    item = AnnouncementEntry(
        title=payload.title.strip(),
        content=payload.content.strip(),
        display_mode=payload.display_mode,
        show_in_overview=bool(payload.show_in_overview),
        sort_order=int(payload.sort_order or 0),
        is_published=bool(payload.is_published),
        published_at=now if payload.is_published else None,
        created_at=now,
        updated_at=now,
        updated_by_user_id=actor.id,
        updated_by_username=actor.username,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize_announcement(item)


def update_announcement(db: Session, item_id: int, actor: V2User, payload) -> dict:
    item = db.query(AnnouncementEntry).filter(AnnouncementEntry.id == item_id).first()
    if item is None:
        raise ValueError("Announcement not found")

    if payload.title is not None:
        item.title = payload.title.strip()
    if payload.content is not None:
        item.content = payload.content.strip()
    if payload.display_mode is not None:
        item.display_mode = payload.display_mode
    if payload.show_in_overview is not None:
        item.show_in_overview = bool(payload.show_in_overview)
    if payload.sort_order is not None:
        item.sort_order = int(payload.sort_order)
    if payload.is_published is not None:
        item.is_published = bool(payload.is_published)
        item.published_at = _utc_now_naive() if item.is_published else None

    item.updated_at = _utc_now_naive()
    item.updated_by_user_id = actor.id
    item.updated_by_username = actor.username
    db.commit()
    db.refresh(item)
    return _serialize_announcement(item)


def delete_announcement(db: Session, item_id: int) -> dict:
    item = db.query(AnnouncementEntry).filter(AnnouncementEntry.id == item_id).first()
    if item is None:
        raise ValueError("Announcement not found")
    db.delete(item)
    db.commit()
    return {"message": "Announcement deleted"}


def create_faq(db: Session, actor: V2User, payload) -> dict:
    now = _utc_now_naive()
    item = FaqEntry(
        title=payload.title.strip(),
        content=payload.content.strip(),
        sort_order=int(payload.sort_order or 0),
        is_published=bool(payload.is_published),
        published_at=now if payload.is_published else None,
        created_at=now,
        updated_at=now,
        updated_by_user_id=actor.id,
        updated_by_username=actor.username,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize_faq(item)


def update_faq(db: Session, item_id: int, actor: V2User, payload) -> dict:
    item = db.query(FaqEntry).filter(FaqEntry.id == item_id).first()
    if item is None:
        raise ValueError("FAQ not found")

    if payload.title is not None:
        item.title = payload.title.strip()
    if payload.content is not None:
        item.content = payload.content.strip()
    if payload.sort_order is not None:
        item.sort_order = int(payload.sort_order)
    if payload.is_published is not None:
        item.is_published = bool(payload.is_published)
        item.published_at = _utc_now_naive() if item.is_published else None

    item.updated_at = _utc_now_naive()
    item.updated_by_user_id = actor.id
    item.updated_by_username = actor.username
    db.commit()
    db.refresh(item)
    return _serialize_faq(item)


def delete_faq(db: Session, item_id: int) -> dict:
    item = db.query(FaqEntry).filter(FaqEntry.id == item_id).first()
    if item is None:
        raise ValueError("FAQ not found")
    db.delete(item)
    db.commit()
    return {"message": "FAQ deleted"}
