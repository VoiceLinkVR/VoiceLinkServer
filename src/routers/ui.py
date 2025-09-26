from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, func, case, desc
from datetime import datetime, timedelta
from typing import Optional

from core.dependencies import get_db, hash_password, verify_password, get_admin_user_from_session
from db.models import User, RequestLog
from core.logging_config import logger

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/login", response_class=HTMLResponse)
async def login_ui_get(request: Request):
    messages = request.session.pop("flash_messages", [])
    return templates.TemplateResponse("login.html", {"request": request, "messages": messages})

@router.post("/login", response_class=RedirectResponse)
async def login_ui_post(request: Request, db: Session = Depends(get_db), username: str = Form(...), password: str = Form(...)):
    if db.query(User).filter_by(is_admin=True).count() == 0:
        hashed_password = hash_password(password)
        new_user = User(username=username, password=hashed_password, is_admin=True, is_active=True)
        db.add(new_user)
        db.commit()
        request.session["user_id"] = new_user.id
        logger.info(f"[ADMIN] 创建首个管理员用户: {username}")
        return RedirectResponse(url=request.url_for('manage_users_ui'), status_code=303)

    user = db.query(User).filter_by(username=username).first()
    if user and verify_password(password, user.password) and user.is_admin:
        request.session["user_id"] = user.id
        logger.info(f"[ADMIN] 管理员登录: {username}")
        return RedirectResponse(url=request.url_for('manage_users_ui'), status_code=303)
    else:
        request.session["flash_messages"] = ["Invalid username or password"]
        logger.warning(f"[AUTH] 管理员登录失败: {username}")
        return RedirectResponse(url=request.url_for('login_ui_get'), status_code=303)

@router.get("/logout", response_class=RedirectResponse)
async def logout_ui(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url=request.url_for('login_ui_get'), status_code=303)

@router.get("/manage_users", response_class=HTMLResponse)
async def manage_users_ui(request: Request, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for('login_ui_get'))

    users = db.query(User).all()
    messages = request.session.pop("flash_messages", [])
    return templates.TemplateResponse("manage_users.html", {"request": request, "users": users, "messages": messages})

@router.post("/manage_users", response_class=RedirectResponse)
async def manage_users_post(request: Request, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for('login_ui_get'))

    form = await request.form()
    username = form.get("new_username")
    password = form.get("new_password")
    is_admin = form.get("new_is_admin") == "on"
    is_update = form.get("is_update") == "on"
    limit_rule = form.get("new_limit_rule")
    exp_date_str = form.get("expiration_date")
    expiration_date = datetime.strptime(exp_date_str, '%Y-%m-%d') if exp_date_str else None
    is_active = form.get("is_active") == "on"

    if is_update:
        user = db.query(User).filter_by(username=username).first()
        if user:
            user.is_admin = is_admin
            if expiration_date: user.expiration_date = expiration_date
            user.is_active = is_active
            if password: user.password = hash_password(password)
            if limit_rule: user.limit_rule = limit_rule
            logger.info(f"更新用户: {username}")
        else:
            request.session["flash_messages"] = [f"User '{username}' not found for update."]
            return RedirectResponse(url=request.url_for('manage_users_ui'), status_code=303)
    else:
        if not password:
            request.session["flash_messages"] = ["Password is required for new user."]
            return RedirectResponse(url=request.url_for('manage_users_ui'), status_code=303)
        hashed_password = hash_password(password)
        new_user = User(
            username=username, password=hashed_password, is_admin=is_admin,
            limit_rule=limit_rule or "10000/day;1000/hour",
            expiration_date=expiration_date, is_active=is_active
        )
        db.add(new_user)
        logger.info(f"新增用户: {username}")

    db.commit()
    request.session["flash_messages"] = ["User added/updated successfully."]
    return RedirectResponse(url=request.url_for('manage_users_ui'), status_code=303)

@router.post("/deleteUser", response_class=RedirectResponse)
async def delete_user_ui(request: Request, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user:
        return RedirectResponse(url=request.url_for('login_ui_get'))

    form = await request.form()
    user_id = form.get("id")
    user = db.query(User).filter_by(id=int(user_id)).first()
    if user:
        db.delete(user)
        db.commit()
        request.session["flash_messages"] = [f"User {user.username} deleted."]
    return RedirectResponse(url=request.url_for('manage_users_ui'), status_code=303)

@router.get('/stats', response_class=HTMLResponse)
async def stats_ui(request: Request, hour: Optional[str] = None, db: Session = Depends(get_db)):
    admin_user = get_admin_user_from_session(request, db)
    if not admin_user: return RedirectResponse(url=request.url_for('login_ui_get'))

    # 数据库方言兼容
    dialect_name = db.bind.dialect.name
    if dialect_name == 'sqlite':
        hour_expr = func.strftime('%Y-%m-%d %H:00', func.datetime(RequestLog.timestamp, '+8 hours'))
        date_expr = func.date(func.datetime(RequestLog.timestamp, '+8 hours'))
    else: # mysql
        hour_expr = func.date_format(func.date_add(RequestLog.timestamp, text('INTERVAL 8 HOUR')), '%Y-%m-%d %H:00')
        date_expr = func.date(func.date_add(RequestLog.timestamp, text('INTERVAL 8 HOUR')))

    hours = [r[0] for r in db.query(hour_expr.label('hour')).distinct().order_by(desc('hour')).all()]
    selected_hour = hour or (hours[0] if hours else None)

    # 小时统计
    hourly_stats, total_success, total_fail, total_rate_limited, total_count = [], 0, 0, 0, 0
    if selected_hour:
        hourly_query = db.query(
            RequestLog.username, RequestLog.ip, RequestLog.endpoint,
            func.sum(case((RequestLog.status == 'success', 1), else_=0)).label('success_count'),
            func.sum(case((RequestLog.status == 'failed', 1), else_=0)).label('fail_count'),
            func.sum(case((RequestLog.status == 'rate_limited', 1), else_=0)).label('rate_limited_count'),
            func.count().label('total_count')
        ).filter(hour_expr == selected_hour).group_by(RequestLog.username, RequestLog.ip, RequestLog.endpoint)
        hourly_stats = hourly_query.all()
        total_success = sum(s.success_count for s in hourly_stats)
        total_fail = sum(s.fail_count for s in hourly_stats)
        total_rate_limited = sum(s.rate_limited_count for s in hourly_stats)
        total_count = sum(s.total_count for s in hourly_stats)

    # 耗时分布
    duration_stats = []
    if selected_hour:
        duration_query = db.query(
            case(
                (RequestLog.duration < 3, '0-3s'), (RequestLog.duration < 10, '3-10s'),
                (RequestLog.duration < 20, '10-20s'), (RequestLog.duration < 30, '20-30s'),
                (RequestLog.duration < 60, '30-60s'), (RequestLog.duration < 90, '60-90s'),
                else_='90s+'
            ).label('duration_range'),
            func.count().label('count')
        ).filter(hour_expr == selected_hour).group_by('duration_range')
        duration_stats = sorted(duration_query.all(), key=lambda x: (int(x.duration_range.split('-')[0].rstrip('s')) if x.duration_range != '90s+' else 90))

    # 每日统计
    date_filter_info = {'start_date': None, 'end_date': None, 'error': False}
    try:
        end_date = datetime.strptime(selected_hour.split()[0], "%Y-%m-%d") if selected_hour else datetime.now()
        start_date = end_date - timedelta(days=6)
        date_filter_info['start_date'] = start_date.strftime("%Y-%m-%d")
        date_filter_info['end_date'] = end_date.strftime("%Y-%m-%d")
    except:
        date_filter_info['error'] = True

    daily_query = db.query(
        date_expr.label('day'),
        func.sum(case((RequestLog.status == 'success', 1), else_=0)).label('daily_success'),
        func.sum(case((RequestLog.status == 'failed', 1), else_=0)).label('daily_fail'),
        func.sum(case((RequestLog.status == 'rate_limited', 1), else_=0)).label('daily_rate_limited'),
        func.count().label('daily_total')
    ).filter(date_expr.between(date_filter_info['start_date'], date_filter_info['end_date'])).group_by('day').order_by(desc('day'))
    daily_stats = daily_query.all()

    return templates.TemplateResponse('stats.html', {
        "request": request, "date_filter_info": date_filter_info, "daily_stats": daily_stats,
        "total_success": total_success, "total_fail": total_fail, "total_rate_limited": total_rate_limited,
        "hours": hours, "selected_hour": selected_hour, "hourly_stats": hourly_stats,
        "duration_stats": duration_stats, "total_count": total_count
    })