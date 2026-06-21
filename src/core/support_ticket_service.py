import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from core.config import settings
from core.dependencies import normalize_email, utcnow_naive
from core.email_service import send_email_message
from core.logging_config import logger
from db.models import PaymentOrder, SupportTicket, SupportTicketAttachment, SupportTicketEvent, V2User

TICKET_STATUS_OPEN = "open"
TICKET_STATUS_IN_PROGRESS = "in_progress"
TICKET_STATUS_RESOLVED = "resolved"
TICKET_STATUS_CLOSED = "closed"
TICKET_ACTIVE_STATUSES = {TICKET_STATUS_OPEN, TICKET_STATUS_IN_PROGRESS}
TICKET_FINAL_STATUSES = {TICKET_STATUS_RESOLVED, TICKET_STATUS_CLOSED}
TICKET_STATUSES = TICKET_ACTIVE_STATUSES | TICKET_FINAL_STATUSES

ORDER_ACTIVATION_FAILED_STATUS = "activation_failed"
ORDER_MANUAL_HANDLED_STATUS = "manual_handled"
GENERAL_TICKET_CATEGORY = "general"
GENERAL_TICKET_CATEGORIES = {
    GENERAL_TICKET_CATEGORY,
    "account",
    "billing",
    "usage",
    "technical",
    "other",
}

ATTACHMENT_FIELD_NAME = "attachments"
ATTACHMENT_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/x-log": ".log",
}
ATTACHMENT_SUFFIX_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".log": "text/plain",
}

TICKET_STATUS_LABELS = {
    TICKET_STATUS_OPEN: "待处理",
    TICKET_STATUS_IN_PROGRESS: "处理中",
    TICKET_STATUS_RESOLVED: "已处理",
    TICKET_STATUS_CLOSED: "已关闭",
}


def _to_utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _normalize_status(value: Optional[str], fallback: str = TICKET_STATUS_OPEN) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "pending": TICKET_STATUS_OPEN,
        "processing": TICKET_STATUS_IN_PROGRESS,
        "handled": TICKET_STATUS_RESOLVED,
        "done": TICKET_STATUS_RESOLVED,
    }
    normalized = aliases.get(normalized, normalized)
    if not normalized:
        return fallback
    if normalized not in TICKET_STATUSES:
        raise ValueError("Invalid ticket status")
    return normalized


def _is_activation_failed_order(order: PaymentOrder) -> bool:
    status_text = str(getattr(order, "status", "") or "").strip().lower()
    if status_text == ORDER_ACTIVATION_FAILED_STATUS:
        return True
    return bool(str(getattr(order, "activation_error", "") or "").strip()) and status_text != ORDER_MANUAL_HANDLED_STATUS


def _ticket_subject_for_order(order: PaymentOrder) -> str:
    order_ref = (order.out_trade_no or order.order_no or f"#{order.id}").strip()
    return f"异常订单人工处理：{order_ref}"


def _build_ticket_no(db: Session) -> str:
    prefix = datetime.utcnow().strftime("T%Y%m%d")
    for _ in range(10):
        candidate = f"{prefix}{secrets.token_hex(3).upper()}"
        if db.query(SupportTicket.id).filter(SupportTicket.ticket_no == candidate).first() is None:
            return candidate
    return f"{prefix}{secrets.token_hex(6).upper()}"


def _attachment_root() -> Path:
    root = Path(settings.SUPPORT_TICKET_ATTACHMENT_ROOT).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _max_attachment_count() -> int:
    return max(1, int(settings.SUPPORT_TICKET_ATTACHMENT_MAX_FILES or 5))


def _max_attachment_size_bytes() -> int:
    return max(1, int(settings.SUPPORT_TICKET_ATTACHMENT_MAX_MB or 10)) * 1024 * 1024


def _normalize_content_type(value: Optional[str]) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _safe_original_filename(value: Optional[str], fallback: str) -> str:
    raw = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not raw:
        raw = fallback
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    return cleaned[:255]


def _attachment_content_type_and_suffix(upload: UploadFile, original_filename: str) -> tuple[str, str]:
    suffix = Path(original_filename).suffix.lower()
    content_type = _normalize_content_type(getattr(upload, "content_type", None))

    if content_type in ATTACHMENT_CONTENT_TYPES:
        return content_type, ATTACHMENT_CONTENT_TYPES[content_type]
    if suffix in ATTACHMENT_SUFFIX_CONTENT_TYPES:
        return ATTACHMENT_SUFFIX_CONTENT_TYPES[suffix], suffix

    raise ValueError("附件仅支持 png/jpg/webp/gif 图片、PDF、txt 或 log 文件")


def _attachment_download_url(ticket_id: int, attachment_id: int, *, admin: bool) -> str:
    prefix = "/v2/adminapi/tickets" if admin else "/v2/api/account/tickets"
    return f"{prefix}/{ticket_id}/attachments/{attachment_id}"


def _serialize_attachment(attachment: SupportTicketAttachment, *, admin: bool) -> dict[str, Any]:
    return {
        "id": str(attachment.id),
        "eventId": str(attachment.event_id) if getattr(attachment, "event_id", None) else "",
        "filename": attachment.original_filename,
        "contentType": attachment.content_type,
        "fileSize": int(attachment.file_size or 0),
        "url": _attachment_download_url(int(attachment.ticket_id), int(attachment.id), admin=admin),
        "createdAt": _to_utc_iso(attachment.created_at),
    }


async def _save_ticket_attachments(
    db: Session,
    ticket: SupportTicket,
    attachments: Optional[list[UploadFile]],
    current_user: V2User,
    event: Optional[SupportTicketEvent] = None,
) -> list[Path]:
    upload_files = [item for item in (attachments or []) if item is not None]
    if not upload_files:
        return []

    max_count = _max_attachment_count()
    if len(upload_files) > max_count:
        raise ValueError(f"最多只能添加 {max_count} 个附件")

    root = _attachment_root()
    ticket_dir = (root / str(ticket.id)).resolve()
    if root not in ticket_dir.parents and ticket_dir != root:
        raise ValueError("Invalid ticket attachment path")
    ticket_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = _max_attachment_size_bytes()
    saved_paths: list[Path] = []
    try:
        for index, upload in enumerate(upload_files, start=1):
            original_filename = _safe_original_filename(
                getattr(upload, "filename", None),
                f"attachment-{index}",
            )
            content_type, suffix = _attachment_content_type_and_suffix(upload, original_filename)
            storage_name = f"{ticket.ticket_no}-{secrets.token_hex(8)}{suffix}"
            destination = (ticket_dir / storage_name).resolve()
            if root not in destination.parents:
                raise ValueError("Invalid ticket attachment path")

            total = 0
            try:
                with destination.open("wb") as file:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"单个附件不能超过 {settings.SUPPORT_TICKET_ATTACHMENT_MAX_MB} MB")
                        file.write(chunk)

                if total <= 0:
                    raise ValueError("附件不能为空")

                relative_path = destination.relative_to(root).as_posix()
                db.add(
                    SupportTicketAttachment(
                        ticket_id=ticket.id,
                        event_id=event.id if event is not None else None,
                        storage_path=relative_path,
                        original_filename=original_filename,
                        content_type=content_type,
                        file_size=total,
                        uploaded_by_user_uuid=(current_user.user_uuid or "").strip() or None,
                        uploaded_by_username=(current_user.username or "").strip() or None,
                        created_at=utcnow_naive(),
                    )
                )
                saved_paths.append(destination)
            except Exception:
                if destination.exists():
                    destination.unlink(missing_ok=True)
                raise
            finally:
                await upload.close()
    except Exception:
        _remove_saved_files(saved_paths)
        raise

    return saved_paths


def _remove_saved_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[SUPPORT-TICKET] Failed to remove attachment %s: %s", path, exc)


def _send_ticket_email(ticket: SupportTicket, *, subject: str, body: str) -> tuple[bool, Optional[str]]:
    recipient = str(ticket.email or "").strip()
    if not recipient:
        return False, "No recipient email"
    try:
        send_email_message(recipient=normalize_email(recipient), subject=subject, text_body=body)
        ticket.last_notified_at = utcnow_naive()
        return True, None
    except HTTPException as exc:
        detail = str(exc.detail or exc)
    except Exception as exc:  # pylint: disable=broad-except
        detail = str(exc)
    logger.warning("[SUPPORT-TICKET] Failed to notify ticket %s: %s", ticket.ticket_no, detail)
    return False, detail[:1000]


def _order_ref(ticket: SupportTicket) -> str:
    return str(ticket.out_trade_no or ticket.order_no or (f"#{ticket.order_id}" if ticket.order_id else "-"))


def _ticket_context_text(ticket: SupportTicket) -> str:
    lines = [f"反馈主题：{ticket.subject}"]
    order_ref = _order_ref(ticket)
    if order_ref != "-":
        lines.append(f"关联订单：{order_ref}")
    return "\n".join(lines)


def _create_received_email(ticket: SupportTicket) -> tuple[bool, Optional[str]]:
    body = (
        "你提交的 VoiceLinkVR 反馈中心请求已收到。\n\n"
        f"反馈编号：{ticket.ticket_no}\n"
        f"{_ticket_context_text(ticket)}\n"
        f"当前状态：{TICKET_STATUS_LABELS.get(ticket.status, ticket.status)}\n\n"
        "处理进度会同步到控制台的「反馈中心」页面；每一次处理更新也会通过邮件通知你。"
    )
    return _send_ticket_email(ticket, subject=f"VoiceLinkVR 已收到反馈 {ticket.ticket_no}", body=body)


def _create_update_email(ticket: SupportTicket, event: SupportTicketEvent) -> tuple[bool, Optional[str]]:
    message = str(event.message or "").strip()
    body = (
        "你的 VoiceLinkVR 反馈中心请求有新的处理进度。\n\n"
        f"反馈编号：{ticket.ticket_no}\n"
        f"{_ticket_context_text(ticket)}\n"
        f"当前状态：{TICKET_STATUS_LABELS.get(ticket.status, ticket.status)}\n"
        f"处理人：{event.actor_username or '客服'}\n"
    )
    if message:
        body += f"\n处理说明：\n{message}\n"
    body += "\n你可以在控制台的「反馈中心」页面查看完整进度。"
    return _send_ticket_email(ticket, subject=f"VoiceLinkVR 反馈进度更新 {ticket.ticket_no}", body=body)


def _create_event(
    db: Session,
    ticket: SupportTicket,
    *,
    actor_type: str,
    actor_username: Optional[str],
    action: str,
    old_status: Optional[str],
    new_status: Optional[str],
    message: Optional[str],
) -> SupportTicketEvent:
    now = utcnow_naive()
    event = SupportTicketEvent(
        ticket_id=ticket.id,
        actor_type=actor_type,
        actor_username=(actor_username or "").strip() or None,
        action=action,
        old_status=old_status,
        new_status=new_status,
        message=(message or "").strip() or None,
        created_at=now,
    )
    db.add(event)
    ticket.last_event_at = now
    ticket.updated_at = now
    return event


def _serialize_order(order: Optional[PaymentOrder]) -> Optional[dict[str, Any]]:
    if order is None:
        return None
    return {
        "id": str(order.id),
        "orderNo": order.order_no,
        "outTradeNo": order.out_trade_no,
        "action": order.action,
        "amount": round(float(order.amount or 0.0), 2),
        "status": order.status,
        "activationError": str(order.activation_error or "").strip(),
        "activationErrorAt": _to_utc_iso(order.activation_error_at),
        "createdAt": _to_utc_iso(order.created_at),
    }


def serialize_ticket(
    ticket: SupportTicket,
    events: Optional[list[SupportTicketEvent]] = None,
    order: Optional[PaymentOrder] = None,
    attachments: Optional[list[SupportTicketAttachment]] = None,
    *,
    include_internal_note: bool = False,
) -> dict[str, Any]:
    ticket_events = events
    if ticket_events is None:
        ticket_events = []
    ticket_attachments = attachments
    if ticket_attachments is None:
        ticket_attachments = []
    attachments_by_event: dict[int, list[SupportTicketAttachment]] = {}
    loose_attachments: list[SupportTicketAttachment] = []
    for attachment in ticket_attachments:
        event_id = int(getattr(attachment, "event_id", 0) or 0)
        if event_id:
            attachments_by_event.setdefault(event_id, []).append(attachment)
        else:
            loose_attachments.append(attachment)
    if loose_attachments and ticket_events:
        first_event_id = int(ticket_events[0].id)
        attachments_by_event.setdefault(first_event_id, []).extend(loose_attachments)

    return {
        "id": str(ticket.id),
        "ticketNo": ticket.ticket_no,
        "orderId": str(ticket.order_id) if ticket.order_id else "",
        "orderNo": ticket.order_no or "",
        "outTradeNo": ticket.out_trade_no or "",
        "username": ticket.username or "",
        "email": ticket.email or "",
        "category": ticket.category,
        "subject": ticket.subject,
        "description": ticket.description or "",
        "status": ticket.status,
        "statusLabel": TICKET_STATUS_LABELS.get(ticket.status, ticket.status),
        "priority": ticket.priority,
        "publicReply": ticket.public_reply or "",
        "internalNote": (ticket.internal_note or "") if include_internal_note else "",
        "lastEventAt": _to_utc_iso(ticket.last_event_at),
        "lastNotifiedAt": _to_utc_iso(ticket.last_notified_at),
        "resolvedAt": _to_utc_iso(ticket.resolved_at),
        "createdAt": _to_utc_iso(ticket.created_at),
        "updatedAt": _to_utc_iso(ticket.updated_at),
        "order": _serialize_order(order),
        "attachments": [
            _serialize_attachment(attachment, admin=include_internal_note)
            for attachment in ticket_attachments
        ],
        "events": [
            {
                "id": str(event.id),
                "actorType": event.actor_type,
                "actorUsername": event.actor_username or "",
                "action": event.action,
                "oldStatus": event.old_status or "",
                "newStatus": event.new_status or "",
                "newStatusLabel": TICKET_STATUS_LABELS.get(event.new_status or "", event.new_status or ""),
                "message": event.message or "",
                "emailSent": bool(event.email_sent),
                "emailError": event.email_error or "",
                "createdAt": _to_utc_iso(event.created_at),
                "attachments": [
                    _serialize_attachment(attachment, admin=include_internal_note)
                    for attachment in attachments_by_event.get(int(event.id), [])
                ],
            }
            for event in ticket_events
        ],
    }


def _events_for_tickets(db: Session, ticket_ids: list[int]) -> dict[int, list[SupportTicketEvent]]:
    if not ticket_ids:
        return {}
    rows = (
        db.query(SupportTicketEvent)
        .filter(SupportTicketEvent.ticket_id.in_(ticket_ids))
        .order_by(SupportTicketEvent.created_at.asc(), SupportTicketEvent.id.asc())
        .all()
    )
    result: dict[int, list[SupportTicketEvent]] = {}
    for row in rows:
        result.setdefault(int(row.ticket_id), []).append(row)
    return result


def _attachments_for_tickets(db: Session, ticket_ids: list[int]) -> dict[int, list[SupportTicketAttachment]]:
    if not ticket_ids:
        return {}
    rows = (
        db.query(SupportTicketAttachment)
        .filter(SupportTicketAttachment.ticket_id.in_(ticket_ids))
        .order_by(SupportTicketAttachment.created_at.asc(), SupportTicketAttachment.id.asc())
        .all()
    )
    result: dict[int, list[SupportTicketAttachment]] = {}
    for row in rows:
        result.setdefault(int(row.ticket_id), []).append(row)
    return result


def _orders_for_tickets(db: Session, tickets: list[SupportTicket]) -> dict[int, PaymentOrder]:
    order_ids = [int(ticket.order_id) for ticket in tickets if ticket.order_id]
    if not order_ids:
        return {}
    return {int(order.id): order for order in db.query(PaymentOrder).filter(PaymentOrder.id.in_(order_ids)).all()}


def _user_order_filter(current_user: V2User):
    return or_(PaymentOrder.user_uuid == current_user.user_uuid, PaymentOrder.username == current_user.username)


def _user_ticket_filter(current_user: V2User):
    return or_(SupportTicket.user_uuid == current_user.user_uuid, SupportTicket.username == current_user.username)


def find_active_ticket_for_order(db: Session, order_id: int, current_user: V2User) -> Optional[SupportTicket]:
    return (
        db.query(SupportTicket)
        .filter(
            SupportTicket.order_id == order_id,
            _user_ticket_filter(current_user),
            SupportTicket.status.in_(list(TICKET_ACTIVE_STATUSES)),
        )
        .order_by(desc(SupportTicket.updated_at), desc(SupportTicket.id))
        .first()
    )


def get_ticket_summary_for_orders(db: Session, order_ids: list[int], current_user: V2User) -> dict[int, dict[str, Any]]:
    if not order_ids:
        return {}
    tickets = (
        db.query(SupportTicket)
        .filter(SupportTicket.order_id.in_(order_ids), _user_ticket_filter(current_user))
        .order_by(desc(SupportTicket.updated_at), desc(SupportTicket.id))
        .all()
    )
    result: dict[int, dict[str, Any]] = {}
    for ticket in tickets:
        if ticket.order_id in result:
            continue
        result[int(ticket.order_id)] = {
            "id": str(ticket.id),
            "ticketNo": ticket.ticket_no,
            "status": ticket.status,
            "statusLabel": TICKET_STATUS_LABELS.get(ticket.status, ticket.status),
            "active": ticket.status in TICKET_ACTIVE_STATUSES,
            "updatedAt": _to_utc_iso(ticket.updated_at),
        }
    return result


async def create_order_support_ticket(
    db: Session,
    *,
    order_id: int,
    current_user: V2User,
    message: Optional[str] = None,
    attachments: Optional[list[UploadFile]] = None,
) -> dict[str, Any]:
    order = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.id == order_id, _user_order_filter(current_user))
        .first()
    )
    if order is None:
        raise ValueError("Order not found")
    if not _is_activation_failed_order(order):
        raise ValueError("Only activation failed orders can create a support ticket")

    existing_ticket = find_active_ticket_for_order(db, order.id, current_user)
    if existing_ticket is not None:
        normalized_message = str(message or "").strip()
        if attachments or normalized_message:
            saved_paths: list[Path] = []
            try:
                event = _create_event(
                    db,
                    existing_ticket,
                    actor_type="user",
                    actor_username=current_user.username,
                    action="message_added",
                    old_status=existing_ticket.status,
                    new_status=existing_ticket.status,
                    message=normalized_message or "用户补充了反馈附件。",
                )
                db.flush()
                saved_paths = await _save_ticket_attachments(db, existing_ticket, attachments, current_user, event=event)
                db.commit()
                db.refresh(existing_ticket)
            except Exception:
                db.rollback()
                _remove_saved_files(saved_paths)
                raise
        events = _events_for_tickets(db, [existing_ticket.id]).get(existing_ticket.id, [])
        ticket_attachments = _attachments_for_tickets(db, [existing_ticket.id]).get(existing_ticket.id, [])
        return serialize_ticket(existing_ticket, events, order, ticket_attachments)

    now = utcnow_naive()
    normalized_email = normalize_email(current_user.email) if (current_user.email or "").strip() else None
    ticket = SupportTicket(
        ticket_no=_build_ticket_no(db),
        order_id=order.id,
        order_no=order.order_no,
        out_trade_no=order.out_trade_no,
        user_uuid=(current_user.user_uuid or "").strip() or getattr(order, "user_uuid", None),
        username=current_user.username or order.username,
        email=normalized_email or getattr(order, "email", None),
        category="order_activation",
        subject=_ticket_subject_for_order(order),
        description=(message or "").strip() or str(order.activation_error or "").strip() or None,
        status=TICKET_STATUS_OPEN,
        priority="normal",
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    db.flush()

    saved_paths: list[Path] = []
    try:
        event = _create_event(
            db,
            ticket,
            actor_type="user",
            actor_username=current_user.username,
            action="created",
            old_status=None,
            new_status=ticket.status,
            message=ticket.description,
        )
        db.flush()
        saved_paths = await _save_ticket_attachments(db, ticket, attachments, current_user, event=event)
        event.email_sent, event.email_error = _create_received_email(ticket)
        db.commit()
    except Exception:
        db.rollback()
        _remove_saved_files(saved_paths)
        raise
    db.refresh(ticket)
    events = _events_for_tickets(db, [ticket.id]).get(ticket.id, [])
    ticket_attachments = _attachments_for_tickets(db, [ticket.id]).get(ticket.id, [])
    return serialize_ticket(ticket, events, order, ticket_attachments)


def _normalize_general_category(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return GENERAL_TICKET_CATEGORY
    if normalized not in GENERAL_TICKET_CATEGORIES:
        return GENERAL_TICKET_CATEGORY
    return normalized


async def create_general_support_ticket(
    db: Session,
    *,
    current_user: V2User,
    subject: Optional[str],
    message: Optional[str],
    category: Optional[str] = None,
    attachments: Optional[list[UploadFile]] = None,
) -> dict[str, Any]:
    normalized_email = normalize_email(current_user.email) if (current_user.email or "").strip() else None
    if not normalized_email:
        raise ValueError("Bind an email before creating a support ticket")

    normalized_subject = str(subject or "").strip()
    normalized_message = str(message or "").strip()
    if not normalized_subject:
        raise ValueError("Ticket subject is required")
    if not normalized_message:
        raise ValueError("Ticket description is required")

    now = utcnow_naive()
    ticket = SupportTicket(
        ticket_no=_build_ticket_no(db),
        user_uuid=(current_user.user_uuid or "").strip() or None,
        username=current_user.username,
        email=normalized_email,
        category=_normalize_general_category(category),
        subject=normalized_subject,
        description=normalized_message,
        status=TICKET_STATUS_OPEN,
        priority="normal",
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    db.flush()

    saved_paths: list[Path] = []
    try:
        event = _create_event(
            db,
            ticket,
            actor_type="user",
            actor_username=current_user.username,
            action="created",
            old_status=None,
            new_status=ticket.status,
            message=ticket.description,
        )
        db.flush()
        saved_paths = await _save_ticket_attachments(db, ticket, attachments, current_user, event=event)
        event.email_sent, event.email_error = _create_received_email(ticket)
        db.commit()
    except Exception:
        db.rollback()
        _remove_saved_files(saved_paths)
        raise
    db.refresh(ticket)
    events = _events_for_tickets(db, [ticket.id]).get(ticket.id, [])
    ticket_attachments = _attachments_for_tickets(db, [ticket.id]).get(ticket.id, [])
    return serialize_ticket(ticket, events, None, ticket_attachments)


async def add_user_ticket_message(
    db: Session,
    *,
    ticket_id: int,
    current_user: V2User,
    message: Optional[str] = None,
    attachments: Optional[list[UploadFile]] = None,
) -> dict[str, Any]:
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id, _user_ticket_filter(current_user)).first()
    if ticket is None:
        raise ValueError("Ticket not found")
    if ticket.status == TICKET_STATUS_CLOSED:
        raise ValueError("Ticket is closed")

    normalized_message = str(message or "").strip()
    upload_files = [item for item in (attachments or []) if item is not None]
    if not normalized_message and not upload_files:
        raise ValueError("Message or attachment is required")

    old_status = ticket.status
    new_status = TICKET_STATUS_IN_PROGRESS if old_status == TICKET_STATUS_RESOLVED else old_status
    ticket.status = new_status
    if old_status == TICKET_STATUS_RESOLVED:
        ticket.resolved_at = None

    saved_paths: list[Path] = []
    try:
        event = _create_event(
            db,
            ticket,
            actor_type="user",
            actor_username=current_user.username,
            action="message_added",
            old_status=old_status,
            new_status=new_status,
            message=normalized_message or "用户补充了反馈附件。",
        )
        db.flush()
        saved_paths = await _save_ticket_attachments(db, ticket, upload_files, current_user, event=event)
        db.commit()
    except Exception:
        db.rollback()
        _remove_saved_files(saved_paths)
        raise

    db.refresh(ticket)
    events = _events_for_tickets(db, [ticket.id]).get(ticket.id, [])
    ticket_attachments = _attachments_for_tickets(db, [ticket.id]).get(ticket.id, [])
    order = db.query(PaymentOrder).filter(PaymentOrder.id == ticket.order_id).first() if ticket.order_id else None
    return serialize_ticket(ticket, events, order, ticket_attachments)


def list_user_tickets(db: Session, current_user: V2User, *, include_closed: bool = True) -> list[dict[str, Any]]:
    query = db.query(SupportTicket).filter(_user_ticket_filter(current_user))
    if not include_closed:
        query = query.filter(SupportTicket.status != TICKET_STATUS_CLOSED)
    tickets = query.order_by(desc(SupportTicket.updated_at), desc(SupportTicket.id)).all()
    events_by_ticket = _events_for_tickets(db, [ticket.id for ticket in tickets])
    attachments_by_ticket = _attachments_for_tickets(db, [ticket.id for ticket in tickets])
    orders_by_id = _orders_for_tickets(db, tickets)
    return [
        serialize_ticket(
            ticket,
            events_by_ticket.get(ticket.id, []),
            orders_by_id.get(int(ticket.order_id or 0)),
            attachments_by_ticket.get(ticket.id, []),
        )
        for ticket in tickets
    ]


def get_user_ticket(db: Session, ticket_id: int, current_user: V2User) -> dict[str, Any]:
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id, _user_ticket_filter(current_user)).first()
    if ticket is None:
        raise ValueError("Ticket not found")
    events = _events_for_tickets(db, [ticket.id]).get(ticket.id, [])
    attachments = _attachments_for_tickets(db, [ticket.id]).get(ticket.id, [])
    order = db.query(PaymentOrder).filter(PaymentOrder.id == ticket.order_id).first() if ticket.order_id else None
    return serialize_ticket(ticket, events, order, attachments)


def _attachment_file_payload(attachment: SupportTicketAttachment) -> dict[str, Any]:
    root = _attachment_root()
    path = (root / attachment.storage_path).resolve()
    if root not in path.parents or not path.exists() or not path.is_file():
        raise FileNotFoundError("Attachment file not found")
    return {
        "path": path,
        "filename": attachment.original_filename,
        "content_type": attachment.content_type or "application/octet-stream",
    }


def get_user_ticket_attachment_file(
    db: Session,
    *,
    ticket_id: int,
    attachment_id: int,
    current_user: V2User,
) -> dict[str, Any]:
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id, _user_ticket_filter(current_user)).first()
    if ticket is None:
        raise ValueError("Attachment not found")
    attachment = (
        db.query(SupportTicketAttachment)
        .filter(SupportTicketAttachment.id == attachment_id, SupportTicketAttachment.ticket_id == ticket.id)
        .first()
    )
    if attachment is None:
        raise ValueError("Attachment not found")
    return _attachment_file_payload(attachment)


def get_admin_ticket_attachment_file(db: Session, *, ticket_id: int, attachment_id: int) -> dict[str, Any]:
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if ticket is None:
        raise ValueError("Attachment not found")
    attachment = (
        db.query(SupportTicketAttachment)
        .filter(SupportTicketAttachment.id == attachment_id, SupportTicketAttachment.ticket_id == ticket.id)
        .first()
    )
    if attachment is None:
        raise ValueError("Attachment not found")
    return _attachment_file_payload(attachment)


def list_admin_tickets_payload(
    db: Session,
    *,
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    filters = []

    normalized_status = str(status_filter or "active").strip().lower()
    if normalized_status in {"", "active", "not_closed", "open_active"}:
        normalized_status = "active"
        filters.append(SupportTicket.status != TICKET_STATUS_CLOSED)
    elif normalized_status != "all":
        filters.append(SupportTicket.status == _normalize_status(normalized_status))

    search_value = str(search or "").strip()
    if search_value:
        search_like = f"%{search_value}%"
        filters.append(
            or_(
                SupportTicket.ticket_no.ilike(search_like),
                SupportTicket.order_no.ilike(search_like),
                SupportTicket.out_trade_no.ilike(search_like),
                SupportTicket.username.ilike(search_like),
                SupportTicket.email.ilike(search_like),
                SupportTicket.subject.ilike(search_like),
            )
        )

    filtered_query = db.query(SupportTicket).filter(*filters)
    total_items = filtered_query.count()
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = min(page, total_pages)
    tickets = (
        filtered_query.order_by(desc(SupportTicket.updated_at), desc(SupportTicket.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    events_by_ticket = _events_for_tickets(db, [ticket.id for ticket in tickets])
    attachments_by_ticket = _attachments_for_tickets(db, [ticket.id for ticket in tickets])
    orders_by_id = _orders_for_tickets(db, tickets)

    status_rows = (
        db.query(SupportTicket.status, func.count(SupportTicket.id).label("count"))
        .group_by(SupportTicket.status)
        .all()
    )
    summary = {f"{str(row.status or 'unknown')}_tickets": int(row.count or 0) for row in status_rows}
    summary["total_tickets"] = db.query(func.count(SupportTicket.id)).scalar() or 0
    summary["filtered_tickets"] = total_items

    return {
        "summary": summary,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
        },
        "filters": {
            "search": search_value,
            "status": normalized_status or "all",
        },
        "items": [
            serialize_ticket(
                ticket,
                events_by_ticket.get(ticket.id, []),
                orders_by_id.get(int(ticket.order_id or 0)),
                attachments_by_ticket.get(ticket.id, []),
                include_internal_note=True,
            )
            for ticket in tickets
        ],
    }


def get_admin_ticket(db: Session, ticket_id: int) -> dict[str, Any]:
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if ticket is None:
        raise ValueError("Ticket not found")
    events = _events_for_tickets(db, [ticket.id]).get(ticket.id, [])
    attachments = _attachments_for_tickets(db, [ticket.id]).get(ticket.id, [])
    order = db.query(PaymentOrder).filter(PaymentOrder.id == ticket.order_id).first() if ticket.order_id else None
    return serialize_ticket(ticket, events, order, attachments, include_internal_note=True)


def update_admin_ticket(
    db: Session,
    *,
    ticket_id: int,
    actor: V2User,
    status_value: Optional[str],
    public_reply: Optional[str],
    internal_note: Optional[str],
    mark_order_handled: bool = False,
) -> dict[str, Any]:
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if ticket is None:
        raise ValueError("Ticket not found")

    old_status = ticket.status
    new_status = _normalize_status(status_value, fallback=old_status)
    now = utcnow_naive()
    public_message = (public_reply or "").strip()
    internal_message = (internal_note or "").strip()

    ticket.status = new_status
    if public_message:
        ticket.public_reply = public_message
    if internal_note is not None:
        ticket.internal_note = internal_message or None
    ticket.resolved_at = now if new_status in TICKET_FINAL_STATUSES else None
    ticket.updated_at = now

    order = db.query(PaymentOrder).filter(PaymentOrder.id == ticket.order_id).first() if ticket.order_id else None
    if mark_order_handled and order is not None and _is_activation_failed_order(order):
        order.status = ORDER_MANUAL_HANDLED_STATUS
        order.activation_source = "admin_ticket_handled"
        order.updated_at = now

    event_message = public_message or (
        f"反馈状态更新为 {TICKET_STATUS_LABELS.get(new_status, new_status)}"
    )
    if mark_order_handled and order is not None:
        event_message = f"{event_message}\n\n关联订单已标记为手工处理。"

    event = _create_event(
        db,
        ticket,
        actor_type="admin",
        actor_username=actor.username,
        action="updated",
        old_status=old_status,
        new_status=new_status,
        message=event_message,
    )
    event.email_sent, event.email_error = _create_update_email(ticket, event)
    db.commit()
    db.refresh(ticket)
    events = _events_for_tickets(db, [ticket.id]).get(ticket.id, [])
    attachments = _attachments_for_tickets(db, [ticket.id]).get(ticket.id, [])
    if order is not None:
        db.refresh(order)
    return serialize_ticket(ticket, events, order, attachments, include_internal_note=True)
