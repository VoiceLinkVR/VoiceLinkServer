from datetime import datetime, timedelta, time
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Depends, Form
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text, func, case, desc
from sqlalchemy.orm import Session

from core.dependencies import get_db, hash_password, verify_password, get_admin_user_from_session
from core.logging_config import logger
from db.models import User, RequestLog

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


@router.get("/login", response_class=HTMLResponse)
async def login_ui_get(request: Request):
    messages = request.session.pop("flash_messages", [])
    return templates.TemplateResponse("login.html", {"request": request, "messages": messages})


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

    users = db.query(User).all()
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
        "manage_users.html",
        {"request": request, "users": users, "messages": messages, "user_stats": user_stats}
    )


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
                func.count().label("total_count"),
            )
            .filter(*base_filters)
            .first()
        )
        if summary_row:
            total_success = summary_row.total_success or 0
            total_fail = summary_row.total_fail or 0
            total_rate_limited = summary_row.total_rate_limited or 0
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
            "total_count": total_count,
            "success_rate": round((total_success / total_count) * 100, 2) if total_count else 0,
            "fail_rate": round((total_fail / total_count) * 100, 2) if total_count else 0,
            "rate_limited_rate": round((total_rate_limited / total_count) * 100, 2) if total_count else 0,
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
    return templates.TemplateResponse("stats.html", {"request": request, "selected_hour": hour})


@router.get("/stats/data", response_class=JSONResponse)
async def stats_data_ui(request: Request, hour: Optional[str] = None, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    payload = _build_stats_payload(db, hour)
    return JSONResponse(content=jsonable_encoder(payload))
