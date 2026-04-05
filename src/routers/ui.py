import json
from datetime import datetime, timedelta, time
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text, func, case, desc, or_
from sqlalchemy.orm import Session

from core.afdian_service import afdian_service
from core.dependencies import get_db, hash_password, verify_password, get_admin_user_from_session
from core.logging_config import logger
from db.models import PaymentOrder, RequestLog, User, normalize_payment_lookup_value, payment_lookup_digest

router = APIRouter(
    tags=["UI Management"],
    responses={404: {"description": "Page not found"}}
)
templates = Jinja2Templates(directory="templates")

LOCAL_TIME_OFFSET_HOURS = 8
MAX_HOUR_OPTIONS = 168
MAX_DETAIL_ROWS = 300
EXCLUDED_ENDPOINTS = [
    "/api/login",
    "/api/latestVersionInfo",
    "/api/updates/check",
    "/api/models/list",
    "/api/translations/profile",
    "/api/translators/runtime",
    "/api/translations/capabilities",
]

AFDIAN_ACTION_LABELS = {
    "renew": "renew",
    "upgrade": "upgrade",
}

AFDIAN_ACTIVATION_SOURCE_LABELS = {
    "manual_activate": "手动激活",
    "admin_manual_activate": "后台手动激活",
    "legacy_manual_activate": "旧版手动激活",
    "webhook_direct": "Webhook 直连激活",
    "webhook_query_confirmed": "Webhook 二次查询激活",
}

PAYMENT_PENDING_EXPIRE_HOURS = 24


def _format_local_timestamp(timestamp_value: Any) -> Optional[str]:
    try:
        ts = int(timestamp_value)
    except (TypeError, ValueError):
        return None

    if ts <= 0:
        return None

    return (datetime.utcfromtimestamp(ts) + timedelta(hours=LOCAL_TIME_OFFSET_HOURS)).strftime("%Y-%m-%d %H:%M:%S")


def _format_money(value: Any) -> Optional[str]:
    try:
        return f"CNY {float(value):.2f}"
    except (TypeError, ValueError):
        return None


def _get_activation_source_label(source: Optional[str]) -> Optional[str]:
    normalized = str(source or "").strip().lower()
    if not normalized:
        return None
    return AFDIAN_ACTIVATION_SOURCE_LABELS.get(normalized, source)


def _build_afdian_token_result(token: str) -> Dict[str, Any]:
    parsed = afdian_service.parse_custom_id(token)

    action = str(parsed.get("action") or "").strip().lower()
    try:
        months = int(parsed.get("months")) if parsed.get("months") is not None else None
    except (TypeError, ValueError):
        months = None

    return {
        "token_length": len(token),
        "expired": afdian_service.is_custom_id_expired(parsed),
        "fields": {
            "version": parsed.get("v"),
            "username": parsed.get("username"),
            "password": parsed.get("password"),
            "action": action or None,
            "action_label": AFDIAN_ACTION_LABELS.get(action, action or None),
            "months": months,
            "plan_price": _format_money(parsed.get("plan_price")),
            "requested_amount": _format_money(parsed.get("requested_amount")),
            "timestamp": parsed.get("ts"),
            "generated_at_local": _format_local_timestamp(parsed.get("ts")),
            "nonce": parsed.get("nonce"),
        },
        "payload": parsed,
    }


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_payment_status(status: Optional[str]) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "pending":
        return "pending"
    if normalized == "activated":
        return "activated"
    if normalized == "unpaid":
        return "unpaid"
    return normalized or "unknown"


def _close_expired_pending_orders(db: Session) -> int:
    expire_before = datetime.utcnow() - timedelta(hours=PAYMENT_PENDING_EXPIRE_HOURS)
    expired_orders = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.status == "pending",
            PaymentOrder.created_at.isnot(None),
            PaymentOrder.created_at <= expire_before,
        )
        .all()
    )

    if not expired_orders:
        return 0

    now_utc = datetime.utcnow()
    for order in expired_orders:
        order.status = "unpaid"
        order.updated_at = now_utc

    db.commit()
    logger.info("[ADMIN] Auto-closed unpaid afdian orders: %s", len(expired_orders))
    return len(expired_orders)


def _parse_json_text(raw_text: Optional[str]) -> Any:
    if not raw_text:
        return None
    try:
        return json.loads(raw_text)
    except (TypeError, ValueError):
        return {"_raw": raw_text}


def _build_user_afdian_summary_map(db: Session) -> Dict[str, Dict[str, Any]]:
    rows = (
        db.query(
            PaymentOrder.username.label("username"),
            func.count(PaymentOrder.id).label("order_count"),
            func.sum(case((PaymentOrder.status == "activated", 1), else_=0)).label("activated_order_count"),
            func.sum(case((PaymentOrder.status == "pending", 1), else_=0)).label("pending_order_count"),
            func.sum(case((PaymentOrder.status == "unpaid", 1), else_=0)).label("unpaid_order_count"),
            func.coalesce(
                func.sum(case((PaymentOrder.status == "activated", PaymentOrder.amount), else_=0.0)),
                0.0,
            ).label("activated_amount_total"),
            func.max(PaymentOrder.created_at).label("latest_order_at"),
        )
        .filter(PaymentOrder.username.isnot(None))
        .group_by(PaymentOrder.username)
        .all()
    )

    summary_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        username = str(row.username or "").strip()
        if not username:
            continue
        summary_map[username] = {
            "order_count": int(row.order_count or 0),
            "activated_order_count": int(row.activated_order_count or 0),
            "pending_order_count": int(row.pending_order_count or 0),
            "unpaid_order_count": int(row.unpaid_order_count or 0),
            "closed_order_count": int(row.unpaid_order_count or 0),
            "activated_amount_total": round(float(row.activated_amount_total or 0.0), 2),
            "latest_order_at": _format_datetime(row.latest_order_at),
        }

    return summary_map


def _build_afdian_order_payload(order: PaymentOrder) -> Dict[str, Any]:
    parsed_custom_id = None
    custom_id_parse_error = None
    status = _normalize_payment_status(order.status)
    if order.custom_id:
        try:
            parsed_custom_id = afdian_service.parse_custom_id(order.custom_id)
        except ValueError as exc:
            custom_id_parse_error = str(exc)

    return {
        "id": order.id,
        "order_no": order.order_no,
        "out_trade_no": order.out_trade_no,
        "custom_id": order.custom_id,
        "remark": order.remark,
        "username": order.username,
        "action": order.action,
        "amount": round(float(order.amount), 2) if order.amount is not None else None,
        "status": status,
        "status_label": {
            "activated": "已激活",
            "pending": "未激活",
            "unpaid": "已关闭",
        }.get(status, status),
        "default_collapsed": status == "unpaid",
        "activated_at": _format_datetime(order.activated_at),
        "activation_source": getattr(order, "activation_source", None),
        "activation_source_label": _get_activation_source_label(getattr(order, "activation_source", None)),
        "activation_error": order.activation_error,
        "activation_error_at": _format_datetime(getattr(order, "activation_error_at", None)),
        "created_at": _format_datetime(order.created_at),
        "updated_at": _format_datetime(order.updated_at),
        "parsed_custom_id": parsed_custom_id,
        "custom_id_parse_error": custom_id_parse_error,
        "raw_payload": _parse_json_text(order.raw_payload),
        "raw_payload_text": order.raw_payload,
    }


def _build_user_detail_payload(db: Session, user: User) -> Dict[str, Any]:
    orders = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.username == user.username)
        .order_by(desc(PaymentOrder.created_at), desc(PaymentOrder.id))
        .all()
    )
    afdian_summary_map = _build_user_afdian_summary_map(db)
    afdian_summary = afdian_summary_map.get(
        user.username,
        {
            "order_count": 0,
            "activated_order_count": 0,
            "pending_order_count": 0,
            "unpaid_order_count": 0,
            "closed_order_count": 0,
            "activated_amount_total": 0.0,
            "latest_order_at": None,
        },
    )

    latest_parsed_custom_id = None
    for order in orders:
        if not order.custom_id:
            continue
        try:
            latest_parsed_custom_id = afdian_service.parse_custom_id(order.custom_id)
            break
        except ValueError:
            continue

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "password_hash": user.password,
            "is_admin": user.is_admin,
            "is_active": user.is_active,
            "limit_rule": user.limit_rule,
            "expiration_date": _format_datetime(user.expiration_date),
            "boost_expiration_date": _format_datetime(user.boost_expiration_date),
            "boost_limit_rule": user.boost_limit_rule,
        },
        "afdian_summary": {
            **afdian_summary,
            "has_orders": bool(orders),
            "latest_parsed_custom_id": latest_parsed_custom_id,
        },
        "orders": [_build_afdian_order_payload(order) for order in orders],
    }


def _build_remote_afdian_order_payload(order: Dict[str, Any]) -> Dict[str, Any]:
    custom_id = afdian_service.get_custom_id(order) or None
    parsed_custom_id = None
    custom_id_parse_error = None
    if custom_id:
        try:
            parsed_custom_id = afdian_service.parse_custom_id(custom_id)
        except ValueError as exc:
            custom_id_parse_error = str(exc)

    status_code = afdian_service.get_order_status(order)
    return {
        "order_no": afdian_service.get_order_no(order) or None,
        "out_trade_no": afdian_service.get_out_trade_no(order) or None,
        "custom_id": custom_id,
        "remark": order.get("remark"),
        "status": status_code,
        "status_label": {
            2: "已支付",
            1: "待支付",
            0: "未知",
        }.get(status_code, str(status_code) if status_code is not None else None),
        "amount": _format_money(order.get("show_amount") or order.get("total_amount") or order.get("amount")),
        "create_time_local": _format_local_timestamp(order.get("create_time")),
        "parsed_custom_id": parsed_custom_id,
        "custom_id_parse_error": custom_id_parse_error,
        "raw_payload": order,
    }


def _build_afdian_order_list_payload(db: Session) -> Dict[str, Any]:
    orders = (
        db.query(PaymentOrder)
        .order_by(desc(PaymentOrder.created_at), desc(PaymentOrder.id))
        .all()
    )
    parsed_orders = [_build_afdian_order_payload(order) for order in orders]
    return {
        "summary": {
            "total_orders": len(parsed_orders),
            "activated_orders": sum(1 for order in parsed_orders if order["status"] == "activated"),
            "pending_orders": sum(1 for order in parsed_orders if order["status"] == "pending"),
            "closed_orders": sum(1 for order in parsed_orders if order["status"] == "unpaid"),
            "activated_amount_total": round(
                sum(float(order["amount"] or 0) for order in parsed_orders if order["status"] == "activated"),
                2,
            ),
        },
        "orders": parsed_orders,
    }


def _find_local_order_by_keyword(db: Session, query_value: str) -> Optional[PaymentOrder]:
    lookup_value = (query_value or "").strip()
    if not lookup_value:
        return None

    lookup_digest = payment_lookup_digest(lookup_value)
    if not lookup_digest:
        return None

    order = (
        db.query(PaymentOrder)
        .filter(
            or_(
                PaymentOrder.out_trade_no == lookup_value,
                PaymentOrder.order_no == lookup_value,
                PaymentOrder.custom_id_digest == lookup_digest,
                PaymentOrder.remark_digest == lookup_digest,
            )
        )
        .order_by(desc(PaymentOrder.id))
        .first()
    )
    if order is None:
        return None

    if order.out_trade_no == lookup_value or order.order_no == lookup_value:
        return order
    if normalize_payment_lookup_value(order.custom_id) == lookup_value:
        return order
    if normalize_payment_lookup_value(order.remark) == lookup_value:
        return order
    return None


async def _lookup_afdian_order_payload(db: Session, query_value: str) -> Dict[str, Any]:
    lookup_value = (query_value or "").strip()
    local_order = _find_local_order_by_keyword(db, lookup_value)

    resolved_out_trade_no = lookup_value
    if local_order and local_order.out_trade_no:
        resolved_out_trade_no = local_order.out_trade_no

    remote_payload = None
    remote_order = None
    remote_error = None
    if resolved_out_trade_no:
        try:
            remote_payload = await afdian_service.query_order(out_trade_no=resolved_out_trade_no)
            remote_orders = afdian_service.extract_orders(remote_payload)
            if remote_orders:
                remote_order = next(
                    (
                        item
                        for item in remote_orders
                        if afdian_service.get_out_trade_no(item) == resolved_out_trade_no
                    ),
                    remote_orders[0],
                )
        except Exception as exc:  # pylint: disable=broad-except
            remote_error = str(exc)

    return {
        "query_value": lookup_value,
        "resolved_out_trade_no": resolved_out_trade_no or None,
        "local_found": local_order is not None,
        "remote_found": remote_order is not None,
        "remote_error": remote_error,
        "local_order": _build_afdian_order_payload(local_order) if local_order else None,
        "remote_order": _build_remote_afdian_order_payload(remote_order) if remote_order else None,
        "remote_payload": remote_payload,
    }


def _resolve_activate_lookup(db: Session, query_value: str) -> tuple[Optional[str], Optional[str]]:
    lookup_value = (query_value or "").strip()
    local_order = _find_local_order_by_keyword(db, lookup_value)
    if local_order is None:
        return lookup_value or None, lookup_value or None

    local_out_trade_no = (local_order.out_trade_no or "").strip() or None
    local_custom_id = (local_order.custom_id or "").strip() or None
    local_remark = (local_order.remark or "").strip() or None

    if lookup_value == local_out_trade_no:
        return local_out_trade_no, local_custom_id or local_remark or lookup_value

    if lookup_value == local_custom_id or lookup_value == local_remark:
        return local_out_trade_no, lookup_value

    return local_out_trade_no, local_custom_id or local_remark or lookup_value


@router.get("/login", response_class=HTMLResponse)
async def login_ui_get(request: Request):
    messages = request.session.pop("flash_messages", [])
    return templates.TemplateResponse(request, "login.html", {"request": request, "messages": messages})


@router.post("/login", response_class=RedirectResponse)
async def login_ui_post(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...)
):
    if db.query(User).filter_by(is_admin=True).count() == 0:
        hashed_password = hash_password(password)
        new_user = User(username=username, password=hashed_password, is_admin=True, is_active=True)
        db.add(new_user)
        db.commit()
        request.session["user_id"] = new_user.id
        logger.info(f"[ADMIN] First admin created: {username}")
        return RedirectResponse(url=request.url_for("manage_users_ui"), status_code=303)

    user = db.query(User).filter_by(username=username).first()
    if user and verify_password(password, user.password) and user.is_admin:
        request.session["user_id"] = user.id
        logger.info(f"[ADMIN] Login success: {username}")
        return RedirectResponse(url=request.url_for("manage_users_ui"), status_code=303)

    request.session["flash_messages"] = ["Invalid username or password"]
    logger.warning(f"[AUTH] Admin login failed: {username}")
    return RedirectResponse(url=request.url_for("login_ui_get"), status_code=303)


@router.get("/logout", response_class=RedirectResponse)
async def logout_ui(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url=request.url_for("login_ui_get"), status_code=303)


@router.get("/manage_users", response_class=HTMLResponse)
async def manage_users_ui(request: Request, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for("login_ui_get"))

    _close_expired_pending_orders(db)
    users = db.query(User).all()
    afdian_summary_map = _build_user_afdian_summary_map(db)
    for user in users:
        setattr(
            user,
            "afdian_summary",
            afdian_summary_map.get(
                user.username,
                {
                    "order_count": 0,
                    "activated_order_count": 0,
                    "pending_order_count": 0,
                    "unpaid_order_count": 0,
                    "closed_order_count": 0,
                    "activated_amount_total": 0.0,
                    "latest_order_at": None,
                },
            ),
        )
    now = datetime.now()
    soon = now + timedelta(days=7)
    user_stats = {
        "total_users": db.query(func.count(User.id)).scalar() or 0,
        "non_permanent_users": db.query(func.count(User.id)).filter(User.expiration_date.isnot(None)).scalar() or 0,
        "active_users": db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0,  # noqa: E712
        "expiring_in_7_days": (
            db.query(func.count(User.id))
            .filter(
                User.expiration_date.isnot(None),
                User.is_active == True,  # noqa: E712
                User.expiration_date >= now,
                User.expiration_date <= soon,
            )
            .scalar()
            or 0
        ),
    }
    messages = request.session.pop("flash_messages", [])
    return templates.TemplateResponse(
        request,
        "manage_users.html",
        {"request": request, "users": users, "messages": messages, "user_stats": user_stats}
    )


@router.get("/afdian", response_class=HTMLResponse)
async def afdian_management_ui(request: Request, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for("login_ui_get"))

    _close_expired_pending_orders(db)
    payload = _build_afdian_order_list_payload(db)
    return templates.TemplateResponse(
        request,
        "afdian_management.html",
        {"request": request, "orders": payload["orders"], "summary": payload["summary"]},
    )


@router.get("/afdian/orders/{order_id}", response_class=JSONResponse)
async def afdian_order_detail_ui(request: Request, order_id: int, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    order = db.query(PaymentOrder).filter(PaymentOrder.id == order_id).first()
    if not order:
        return JSONResponse(status_code=404, content={"detail": "Order not found"})

    return JSONResponse(content=jsonable_encoder(_build_afdian_order_payload(order)))


@router.get("/manage_users/{user_id}/detail", response_class=JSONResponse)
async def manage_user_detail_ui(request: Request, user_id: int, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    _close_expired_pending_orders(db)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse(status_code=404, content={"detail": "User not found"})

    payload = _build_user_detail_payload(db, user)
    return JSONResponse(content=jsonable_encoder(payload))


@router.post("/manage_users/parse_afdian_token", response_class=JSONResponse)
async def parse_afdian_token_ui(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(...),
):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    token = (token or "").strip()
    if not token:
        return JSONResponse(status_code=400, content={"detail": "token is required"})

    try:
        payload = _build_afdian_token_result(token)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return JSONResponse(content=jsonable_encoder(payload))


@router.post("/manage_users/query_afdian_order", response_class=JSONResponse)
async def query_afdian_order_ui(
    request: Request,
    db: Session = Depends(get_db),
    order_keyword: str = Form(...),
):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    lookup_value = (order_keyword or "").strip()
    if not lookup_value:
        return JSONResponse(status_code=400, content={"detail": "order_keyword is required"})

    payload = await _lookup_afdian_order_payload(db, lookup_value)
    return JSONResponse(content=jsonable_encoder(payload))


@router.post("/manage_users/activate_afdian_order", response_class=JSONResponse)
async def activate_afdian_order_ui(
    request: Request,
    db: Session = Depends(get_db),
    order_keyword: str = Form(...),
):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    lookup_value = (order_keyword or "").strip()
    if not lookup_value:
        return JSONResponse(status_code=400, content={"detail": "order_keyword is required"})

    from routers.user_center import activate_afdian_order_by_lookup

    out_trade_no, remark_or_custom_id = _resolve_activate_lookup(db, lookup_value)

    try:
        result = await activate_afdian_order_by_lookup(
            db=db,
            out_trade_no=out_trade_no,
            remark_or_custom_id=remark_or_custom_id,
            source="admin_manual_activate",
        )
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    payload = {
        "lookup_value": lookup_value,
        "resolved_out_trade_no": out_trade_no,
        "resolved_custom_id": remark_or_custom_id,
        **result,
    }
    payload["activation_source_label"] = _get_activation_source_label(payload.get("activation_source"))
    return JSONResponse(content=jsonable_encoder(payload))


@router.post("/manage_users", response_class=RedirectResponse)
async def manage_users_post(request: Request, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for("login_ui_get"))

    form = await request.form()
    username = form.get("new_username")
    password = form.get("new_password")
    is_admin = form.get("new_is_admin") == "on"
    is_update = form.get("is_update") == "on"
    limit_rule = form.get("new_limit_rule")
    exp_date_str = form.get("expiration_date")
    expiration_date = datetime.strptime(exp_date_str, "%Y-%m-%d") if exp_date_str else None
    is_active = form.get("is_active") == "on"

    if is_update:
        user = db.query(User).filter_by(username=username).first()
        if user:
            user.is_admin = is_admin
            if expiration_date:
                user.expiration_date = expiration_date
            user.is_active = is_active
            if password:
                user.password = hash_password(password)
            if limit_rule:
                user.limit_rule = limit_rule
            logger.info(f"[ADMIN] Updated user: {username}")
        else:
            request.session["flash_messages"] = [f"User '{username}' not found for update."]
            return RedirectResponse(url=request.url_for("manage_users_ui"), status_code=303)
    else:
        if not password:
            request.session["flash_messages"] = ["Password is required for new user."]
            return RedirectResponse(url=request.url_for("manage_users_ui"), status_code=303)

        hashed_password = hash_password(password)
        new_user = User(
            username=username,
            password=hashed_password,
            is_admin=is_admin,
            limit_rule=limit_rule or "10000/day;1000/hour",
            expiration_date=expiration_date,
            is_active=is_active,
        )
        db.add(new_user)
        logger.info(f"[ADMIN] Added user: {username}")

    db.commit()
    request.session["flash_messages"] = ["User added/updated successfully."]
    return RedirectResponse(url=request.url_for("manage_users_ui"), status_code=303)


@router.post("/deleteUser", response_class=RedirectResponse)
async def delete_user_ui(request: Request, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for("login_ui_get"))

    form = await request.form()
    user_id = form.get("id")
    user = db.query(User).filter_by(id=int(user_id)).first()
    if user:
        db.delete(user)
        db.commit()
        request.session["flash_messages"] = [f"User {user.username} deleted."]
    return RedirectResponse(url=request.url_for("manage_users_ui"), status_code=303)


def _get_local_hour_expr(db: Session):
    if db.bind.dialect.name == "sqlite":
        return func.strftime(
            "%Y-%m-%d %H:00",
            func.datetime(RequestLog.timestamp, f"+{LOCAL_TIME_OFFSET_HOURS} hours"),
        )
    return func.date_format(
        func.date_add(RequestLog.timestamp, text(f"INTERVAL {LOCAL_TIME_OFFSET_HOURS} HOUR")),
        "%Y-%m-%d %H:00",
    )


def _get_local_date_expr(db: Session):
    if db.bind.dialect.name == "sqlite":
        return func.date(func.datetime(RequestLog.timestamp, f"+{LOCAL_TIME_OFFSET_HOURS} hours"))
    return func.date(func.date_add(RequestLog.timestamp, text(f"INTERVAL {LOCAL_TIME_OFFSET_HOURS} HOUR")))


def _get_recent_hours(db: Session, hour_expr) -> List[str]:
    latest_timestamp = db.query(func.max(RequestLog.timestamp)).scalar()
    if not latest_timestamp:
        return []

    recent_start = latest_timestamp - timedelta(days=14)
    rows = (
        db.query(hour_expr.label("hour"))
        .filter(RequestLog.timestamp >= recent_start)
        .group_by(hour_expr)
        .order_by(desc(hour_expr))
        .limit(MAX_HOUR_OPTIONS)
        .all()
    )
    return [row[0] for row in rows if row[0]]


def _normalize_hour(selected_hour: Optional[str], hours: List[str]) -> Optional[str]:
    if selected_hour and selected_hour in hours:
        return selected_hour

    if selected_hour:
        try:
            datetime.strptime(selected_hour, "%Y-%m-%d %H:00")
            return selected_hour
        except ValueError:
            logger.warning(f"[STATS] Invalid hour format: {selected_hour}")

    return hours[0] if hours else None


def _get_utc_hour_range(selected_hour: str):
    local_start = datetime.strptime(selected_hour, "%Y-%m-%d %H:00")
    utc_start = local_start - timedelta(hours=LOCAL_TIME_OFFSET_HOURS)
    return utc_start, utc_start + timedelta(hours=1)


def _build_stats_payload(db: Session, selected_hour_param: Optional[str]) -> Dict[str, Any]:
    hour_expr = _get_local_hour_expr(db)
    date_expr = _get_local_date_expr(db)
    hours = _get_recent_hours(db, hour_expr)
    selected_hour = _normalize_hour(selected_hour_param, hours)

    if selected_hour and selected_hour not in hours:
        hours = [selected_hour, *hours[: MAX_HOUR_OPTIONS - 1]]

    total_success = 0
    total_fail = 0
    total_rate_limited = 0
    total_resource_cost = 0
    total_count = 0
    hourly_stats = []
    duration_stats = []
    top_endpoints = []

    if selected_hour:
        hour_start_utc, hour_end_utc = _get_utc_hour_range(selected_hour)
        base_filters = [
            RequestLog.timestamp >= hour_start_utc,
            RequestLog.timestamp < hour_end_utc,
            RequestLog.endpoint.notin_(EXCLUDED_ENDPOINTS),
        ]

        summary_row = (
            db.query(
                func.sum(case((RequestLog.status == "success", 1), else_=0)).label("total_success"),
                func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("total_fail"),
                func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("total_rate_limited"),
                func.sum(RequestLog.resource_cost).label("total_resource_cost"),
                func.count().label("total_count"),
            )
            .filter(*base_filters)
            .first()
        )
        if summary_row:
            total_success = summary_row.total_success or 0
            total_fail = summary_row.total_fail or 0
            total_rate_limited = summary_row.total_rate_limited or 0
            total_resource_cost = summary_row.total_resource_cost or 0
            total_count = summary_row.total_count or 0

        total_count_col = func.count().label("total_count")
        hourly_stats = (
            db.query(
                RequestLog.username,
                RequestLog.ip,
                RequestLog.endpoint,
                func.sum(case((RequestLog.status == "success", 1), else_=0)).label("success_count"),
                func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("fail_count"),
                func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("rate_limited_count"),
                func.sum(RequestLog.resource_cost).label("resource_cost"),
                total_count_col,
            )
            .filter(*base_filters)
            .group_by(RequestLog.username, RequestLog.ip, RequestLog.endpoint)
            .order_by(desc(total_count_col))
            .limit(MAX_DETAIL_ROWS)
            .all()
        )

        endpoint_total_col = func.count().label("endpoint_total")
        top_endpoints = (
            db.query(
                RequestLog.endpoint,
                func.sum(case((RequestLog.status == "success", 1), else_=0)).label("success_count"),
                func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("fail_count"),
                func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("rate_limited_count"),
                func.sum(RequestLog.resource_cost).label("resource_cost"),
                endpoint_total_col,
            )
            .filter(*base_filters)
            .group_by(RequestLog.endpoint)
            .order_by(desc(endpoint_total_col))
            .limit(8)
            .all()
        )

        duration_range = case(
            (RequestLog.duration < 0.5, "0-0.5s"),
            (RequestLog.duration < 1, "0.5-1s"),
            (RequestLog.duration < 2, "1-2s"),
            (RequestLog.duration < 3, "2-3s"),
            (RequestLog.duration < 4, "3-4s"),
            (RequestLog.duration < 6, "4-6s"),
            (RequestLog.duration < 10, "5-10s"),
            (RequestLog.duration < 20, "10-20s"),
            (RequestLog.duration < 30, "20-30s"),
            (RequestLog.duration < 50, "30-50s"),
            else_="50+s",
        ).label("duration_range")
        bucket_order = case(
            (RequestLog.duration < 0.5, 1),
            (RequestLog.duration < 1, 2),
            (RequestLog.duration < 2, 3),
            (RequestLog.duration < 3, 4),
            (RequestLog.duration < 4, 5),
            (RequestLog.duration < 6, 6),
            (RequestLog.duration < 10, 7),
            (RequestLog.duration < 20, 8),
            (RequestLog.duration < 30, 9),
            (RequestLog.duration < 50, 10),
            else_=11,
        ).label("bucket_order")

        duration_stats = (
            db.query(
                duration_range,
                func.count().label("count"),
                bucket_order,
            )
            .filter(*base_filters)
            .group_by(duration_range, bucket_order)
            .order_by(bucket_order)
            .all()
        )

    if selected_hour:
        local_end_day = datetime.strptime(selected_hour.split()[0], "%Y-%m-%d").date()
    else:
        local_end_day = (datetime.utcnow() + timedelta(hours=LOCAL_TIME_OFFSET_HOURS)).date()
    local_start_day = local_end_day - timedelta(days=6)

    local_start_dt = datetime.combine(local_start_day, time.min)
    local_end_exclusive = datetime.combine(local_end_day + timedelta(days=1), time.min)
    utc_start_dt = local_start_dt - timedelta(hours=LOCAL_TIME_OFFSET_HOURS)
    utc_end_dt = local_end_exclusive - timedelta(hours=LOCAL_TIME_OFFSET_HOURS)

    daily_rows = (
        db.query(
            date_expr.label("day"),
            func.sum(case((RequestLog.status == "success", 1), else_=0)).label("daily_success"),
            func.sum(case((RequestLog.status == "failed", 1), else_=0)).label("daily_fail"),
            func.sum(case((RequestLog.status == "rate_limited", 1), else_=0)).label("daily_rate_limited"),
            func.sum(RequestLog.resource_cost).label("daily_resource_cost"),
            func.count().label("daily_total"),
        )
        .filter(RequestLog.timestamp >= utc_start_dt, RequestLog.timestamp < utc_end_dt)
        .group_by(date_expr)
        .order_by(desc(date_expr))
        .all()
    )

    daily_map: Dict[str, Dict[str, Any]] = {}
    for row in daily_rows:
        if hasattr(row.day, "strftime"):
            day_key = row.day.strftime("%Y-%m-%d")
        else:
            day_key = str(row.day)
        daily_map[day_key] = {
            "day": day_key,
            "daily_success": row.daily_success or 0,
            "daily_fail": row.daily_fail or 0,
            "daily_rate_limited": row.daily_rate_limited or 0,
            "daily_resource_cost": row.daily_resource_cost or 0,
            "daily_total": row.daily_total or 0,
        }

    daily_stats = []
    for day_offset in range(7):
        day_text = (local_start_day + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        daily_stats.append(
            daily_map.get(
                day_text,
                {
                    "day": day_text,
                    "daily_success": 0,
                    "daily_fail": 0,
                    "daily_rate_limited": 0,
                    "daily_resource_cost": 0,
                    "daily_total": 0,
                },
            )
        )

    return {
        "selected_hour": selected_hour,
        "hours": hours,
        "summary": {
            "total_success": total_success,
            "total_fail": total_fail,
            "total_rate_limited": total_rate_limited,
            "total_resource_cost": total_resource_cost,
            "total_count": total_count,
            "success_rate": round((total_success / total_count) * 100, 2) if total_count else 0,
            "fail_rate": round((total_fail / total_count) * 100, 2) if total_count else 0,
            "rate_limited_rate": round((total_rate_limited / total_count) * 100, 2) if total_count else 0,
            "resource_per_request": round((total_resource_cost / total_count), 2) if total_count else 0,
        },
        "date_filter_info": {
            "start_date": local_start_day.strftime("%Y-%m-%d"),
            "end_date": local_end_day.strftime("%Y-%m-%d"),
        },
        "daily_stats": daily_stats,
        "duration_stats": [
            {"duration_range": row.duration_range, "count": row.count}
            for row in duration_stats
        ],
        "top_endpoints": [
            {
                "endpoint": row.endpoint,
                "success_count": row.success_count or 0,
                "fail_count": row.fail_count or 0,
                "rate_limited_count": row.rate_limited_count or 0,
                "resource_cost": row.resource_cost or 0,
                "total_count": row.endpoint_total or 0,
            }
            for row in top_endpoints
        ],
        "hourly_stats": [
            {
                "username": row.username or "anonymous",
                "ip": row.ip,
                "endpoint": row.endpoint,
                "success_count": row.success_count or 0,
                "fail_count": row.fail_count or 0,
                "rate_limited_count": row.rate_limited_count or 0,
                "resource_cost": row.resource_cost or 0,
                "total_count": row.total_count or 0,
            }
            for row in hourly_stats
        ],
        "meta": {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "max_detail_rows": MAX_DETAIL_ROWS,
        },
    }


@router.get("/stats", response_class=HTMLResponse)
async def stats_ui(request: Request, hour: Optional[str] = None, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for("login_ui_get"))
    return templates.TemplateResponse(request, "stats.html", {"request": request, "selected_hour": hour})


@router.get("/stats/data", response_class=JSONResponse)
async def stats_data_ui(request: Request, hour: Optional[str] = None, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    payload = _build_stats_payload(db, hour)
    return JSONResponse(content=jsonable_encoder(payload))
