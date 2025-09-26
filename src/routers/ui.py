from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from core.dependencies import get_db, pwd_context
from db.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# 辅助函数，用于检查UI会话
def get_current_admin_from_session(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_admin == True).first()
    return user

@router.get("/login", response_class=HTMLResponse)
async def login_ui_get(request: Request):
    # 读取闪现消息
    message = request.session.pop("flash_message", None)
    return templates.TemplateResponse("login.html", {"request": request, "messages": [message] if message else []})

@router.post("/login", response_class=HTMLResponse)
async def login_ui_post(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...)
):
    # 验证用户
    user = db.query(User).filter(User.username == username).first()
    if user and pwd_context.verify(password, user.password) and user.is_active:
        request.session["user_id"] = user.id
        return RedirectResponse(url="/ui/manage_users", status_code=302)
    else:
        # 设置闪现消息
        request.session["flash_message"] = "Invalid username or password"
        return RedirectResponse(url="/ui/login", status_code=302)

@router.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/ui/login")

@router.get("/manage_users", response_class=HTMLResponse)
async def manage_users_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_admin_from_session(request, db)
    if not user:
        return RedirectResponse(url="/ui/login")

    users = db.query(User).all()
    return templates.TemplateResponse("manage_users.html", {"request": request, "users": users})

@router.get("/stats", response_class=HTMLResponse)
async def stats_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_admin_from_session(request, db)
    if not user:
        return RedirectResponse(url="/ui/login")

    # 这里可以添加统计数据的查询逻辑
    return templates.TemplateResponse("stats.html", {"request": request})