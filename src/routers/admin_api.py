from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from core import admin_service
from core import support_ticket_service
import core.content_service as content_service
import core.site_config_service as site_config_service
from core.config import settings
from core.dependencies import (
    get_db,
    hash_password,
    normalize_email,
    utcnow_naive,
)
from core.rate_limiter import enforce_auth_attempt_rate_limit
from core.v2_dependencies import (
    authenticate_v2_user,
    get_current_admin_v2_user,
    issue_v2_login_artifacts,
    resolve_authenticated_v2_user,
    revoke_v2_user_session,
)
from db.models import V2User
from routers.user_center import activate_afdian_order_by_lookup
from schemas.admin import (
    AdminAnnouncementCreateRequest,
    AdminAnnouncementListResponse,
    AdminAnnouncementUpdateRequest,
    AdminAuthBootstrapRequest,
    AdminAuthLoginRequest,
    AdminAuthStateResponse,
    AdminSiteConfigAuditResponse,
    AdminSiteConfigListResponse,
    AdminSiteConfigUpdateRequest,
    AdminSiteConfigUpdateResponse,
    AdminSupportTicketUpdateRequest,
    AdminAuthTokenResponse,
    AdminOrderActivateRequest,
    AdminOrderLookupRequest,
    AdminFaqCreateRequest,
    AdminFaqListResponse,
    AdminFaqUpdateRequest,
    AdminUserCreateRequest,
    AdminUserEntitlementCorrectionRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
)

router = APIRouter()


def _serialize_admin_user(user: V2User) -> AdminUserResponse:
    return AdminUserResponse(**admin_service.serialize_admin_user(user))


def _current_admin_if_any(request: Request, db: Session) -> V2User | None:
    try:
        session_user = resolve_authenticated_v2_user(request, db)
    except HTTPException:
        return None
    if session_user is None or not session_user.is_admin:
        return None
    return session_user


def _resolve_request_ip(request: Request) -> str:
    for header_name in ("x-real-ip", "x-forwarded-for"):
        header_value = request.headers.get(header_name, "")
        candidate = header_value.split(",")[0].strip()
        if candidate:
            return candidate[:64]
    if request.client and request.client.host:
        return str(request.client.host).strip()[:64]
    return "unknown"


@router.get("/auth/state", response_model=AdminAuthStateResponse)
async def auth_state(request: Request, db: Session = Depends(get_db)):
    current_admin = _current_admin_if_any(request, db)
    return AdminAuthStateResponse(
        has_admin=db.query(V2User).filter(V2User.is_admin == True).count() > 0,  # noqa: E712
        user=_serialize_admin_user(current_admin) if current_admin else None,
    )


@router.post("/auth/bootstrap", response_model=AdminAuthTokenResponse)
async def bootstrap_admin(
    payload: AdminAuthBootstrapRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if db.query(V2User).filter(V2User.is_admin == True).count() > 0:  # noqa: E712
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Admin already exists")

    username = payload.username.strip()
    email = normalize_email(payload.email) if payload.email else None
    if db.query(V2User).filter(V2User.username == username).first() is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists")
    if email and db.query(V2User).filter(V2User.email == email).first() is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")

    user = V2User(
        username=username,
        password=hash_password(payload.password),
        email=email or "",
        email_verified=bool(email),
        is_admin=True,
        is_active=True,
        identity_version=2,
        created_at=utcnow_naive(),
        last_login_at=utcnow_naive(),
    )
    db.add(user)
    db.flush()

    access_token, refresh_token, _ = issue_v2_login_artifacts(db, user, request)
    db.commit()
    db.refresh(user)

    return AdminAuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_serialize_admin_user(user),
    )


@router.post("/auth/login", response_model=AdminAuthTokenResponse)
async def login_admin(
    payload: AdminAuthLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    enforce_auth_attempt_rate_limit(
        request,
        scope_prefix="admin_login",
        ip_limit=settings.ADMIN_LOGIN_IP_LIMIT,
        identity=payload.login,
        identity_limit=settings.ADMIN_LOGIN_IDENTITY_LIMIT,
    )
    user = authenticate_v2_user(db, payload.login.strip(), payload.password)
    if user is None or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    user.last_login_at = utcnow_naive()
    access_token, refresh_token, _ = issue_v2_login_artifacts(db, user, request)
    db.commit()
    db.refresh(user)

    return AdminAuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_serialize_admin_user(user),
    )


@router.post("/auth/logout")
async def logout_admin(
    request: Request,
    db: Session = Depends(get_db),
    _: V2User = Depends(get_current_admin_v2_user),
):
    revoke_v2_user_session(db, request)
    db.commit()
    return {"message": "Logged out"}


@router.get("/auth/me", response_model=AdminUserResponse)
async def me_admin(current_user: V2User = Depends(get_current_admin_v2_user)):
    return _serialize_admin_user(current_user)


@router.get("/dashboard/summary")
async def dashboard_summary(_: V2User = Depends(get_current_admin_v2_user), db: Session = Depends(get_db)):
    return admin_service.build_dashboard_summary(db)


@router.get("/subscriptions/summary")
async def subscriptions_summary(_: V2User = Depends(get_current_admin_v2_user), db: Session = Depends(get_db)):
    return admin_service.build_subscription_stats_payload(db)


@router.get("/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None, max_length=255),
    role: str | None = Query(default=None, max_length=32),
    user_status: str | None = Query(default=None, alias="status", max_length=32),
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    return admin_service.list_users_payload(
        db,
        page=page,
        page_size=page_size,
        search=search,
        role=role,
        status_filter=user_status,
    )


@router.get("/users/{user_id}")
async def get_user(user_id: int, _: V2User = Depends(get_current_admin_v2_user), db: Session = Depends(get_db)):
    try:
        return admin_service.get_user_detail_payload(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/users/{user_id}/stats")
async def get_user_stats(
    user_id: int,
    days: int = Query(default=14, ge=1, le=90),
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return admin_service.build_user_stats_payload(db, user_id, days=days)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/users")
async def create_user(
    payload: AdminUserCreateRequest,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return admin_service.create_user(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: AdminUserUpdateRequest,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return admin_service.update_user(db, user_id, payload)
    except ValueError as exc:
        status_code = status.HTTP_404_NOT_FOUND if str(exc) == "User not found" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.patch("/users/{user_id}/entitlements")
async def update_user_entitlements(
    user_id: int,
    payload: AdminUserEntitlementCorrectionRequest,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return admin_service.update_user_manual_entitlement(db, user_id, payload)
    except ValueError as exc:
        status_code = status.HTTP_404_NOT_FOUND if str(exc) == "User not found" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    target = db.query(V2User).filter(V2User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete current admin session user")
    return admin_service.delete_user(db, user_id)


@router.get("/orders")
async def list_orders(
    search: str | None = Query(default=None),
    order_status: str | None = Query(default="all", alias="status"),
    action: str | None = Query(default="all"),
    created_from: str | None = Query(default=None),
    created_to: str | None = Query(default=None),
    min_amount: float | None = Query(default=None),
    max_amount: float | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return admin_service.list_orders_payload(
            db,
            search=search,
            status_filter=order_status,
            action_filter=action,
            created_from=created_from,
            created_to=created_to,
            min_amount=min_amount,
            max_amount=max_amount,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/orders/lookup")
async def lookup_order(
    payload: AdminOrderLookupRequest,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    return await admin_service.lookup_afdian_order_payload(db, payload.keyword)


@router.post("/orders/activate")
async def activate_order(
    payload: AdminOrderActivateRequest,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    out_trade_no, remark_or_custom_id = admin_service.resolve_activate_lookup(db, payload.keyword)
    result = await activate_afdian_order_by_lookup(
        db=db,
        out_trade_no=out_trade_no,
        remark_or_custom_id=remark_or_custom_id,
        source="admin_manual_activate",
    )
    return {
        "lookup_value": payload.keyword.strip(),
        "resolved_out_trade_no": out_trade_no,
        "resolved_custom_id": remark_or_custom_id,
        **result,
    }


@router.post("/orders/{order_id}/mark-handled")
async def mark_order_handled(
    order_id: int,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return admin_service.mark_order_manually_handled(db, order_id)
    except ValueError as exc:
        status_code = status.HTTP_404_NOT_FOUND if str(exc) == "Order not found" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/orders/{order_id}")
async def get_order(order_id: int, _: V2User = Depends(get_current_admin_v2_user), db: Session = Depends(get_db)):
    try:
        return admin_service.get_order_detail_payload(db, order_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/tickets")
async def list_support_tickets(
    search: str | None = Query(default=None, max_length=255),
    ticket_status: str | None = Query(default="all", alias="status", max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return support_ticket_service.list_admin_tickets_payload(
            db,
            search=search,
            status_filter=ticket_status,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/tickets/{ticket_id}")
async def get_support_ticket(
    ticket_id: int,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return support_ticket_service.get_admin_ticket(db, ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/tickets/{ticket_id}/attachments/{attachment_id}")
async def get_support_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        payload = support_ticket_service.get_admin_ticket_attachment_file(
            db,
            ticket_id=ticket_id,
            attachment_id=attachment_id,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FileResponse(
        payload["path"],
        media_type=payload["content_type"],
        filename=payload["filename"],
        headers={"Cache-Control": "private, no-store"},
    )


@router.patch("/tickets/{ticket_id}")
async def update_support_ticket(
    ticket_id: int,
    payload: AdminSupportTicketUpdateRequest,
    current_user: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return support_ticket_service.update_admin_ticket(
            db,
            ticket_id=ticket_id,
            actor=current_user,
            status_value=payload.status,
            public_reply=payload.publicReply,
            internal_note=payload.internalNote,
            mark_order_handled=payload.markOrderHandled,
        )
    except ValueError as exc:
        status_code = status.HTTP_404_NOT_FOUND if str(exc) == "Ticket not found" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/stats")
async def stats(hour: str | None = None, _: V2User = Depends(get_current_admin_v2_user), db: Session = Depends(get_db)):
    return admin_service.build_stats_payload(db, hour)


@router.get("/content/announcements", response_model=AdminAnnouncementListResponse)
async def list_announcements(_: V2User = Depends(get_current_admin_v2_user), db: Session = Depends(get_db)):
    return content_service.list_admin_announcements_payload(db)


@router.post("/content/announcements")
async def create_announcement(
    payload: AdminAnnouncementCreateRequest,
    current_user: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    return content_service.create_announcement(db, current_user, payload)


@router.patch("/content/announcements/{item_id}")
async def update_announcement(
    item_id: int,
    payload: AdminAnnouncementUpdateRequest,
    current_user: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return content_service.update_announcement(db, item_id, current_user, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/content/announcements/{item_id}")
async def delete_announcement(
    item_id: int,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return content_service.delete_announcement(db, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/content/faqs", response_model=AdminFaqListResponse)
async def list_faqs(_: V2User = Depends(get_current_admin_v2_user), db: Session = Depends(get_db)):
    return content_service.list_admin_faqs_payload(db)


@router.post("/content/faqs")
async def create_faq(
    payload: AdminFaqCreateRequest,
    current_user: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    return content_service.create_faq(db, current_user, payload)


@router.patch("/content/faqs/{item_id}")
async def update_faq(
    item_id: int,
    payload: AdminFaqUpdateRequest,
    current_user: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return content_service.update_faq(db, item_id, current_user, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/content/faqs/{item_id}")
async def delete_faq(
    item_id: int,
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return content_service.delete_faq(db, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/configs", response_model=AdminSiteConfigListResponse)
async def list_site_configs(
    group: str | None = Query(default=None, max_length=64),
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return site_config_service.list_site_configs(db, group=group)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/configs", response_model=AdminSiteConfigUpdateResponse)
async def update_site_configs(
    payload: AdminSiteConfigUpdateRequest,
    request: Request,
    current_user: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return site_config_service.update_site_configs(
            db,
            actor=current_user,
            request_ip=_resolve_request_ip(request),
            updates=[item.model_dump() for item in payload.items],
            change_note=payload.change_note,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/configs/audit", response_model=AdminSiteConfigAuditResponse)
async def list_site_config_audit_logs(
    config_key: str | None = Query(default=None, max_length=100),
    group: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    _: V2User = Depends(get_current_admin_v2_user),
    db: Session = Depends(get_db),
):
    try:
        return site_config_service.list_site_config_audit_logs(
            db,
            config_key=config_key,
            group=group,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/afdian/parse-token")
async def parse_afdian_token(
    payload: AdminOrderLookupRequest,
    _: V2User = Depends(get_current_admin_v2_user),
):
    try:
        return admin_service.parse_afdian_token_payload(payload.keyword)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
