from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from core.dependencies import get_db, create_access_token, pwd_context, get_current_user
from db.models import User

router = APIRouter()

@router.post("/login")
def login_for_access_token(db: Session = Depends(get_db), form_data: OAuth2PasswordRequestForm = Depends()):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not pwd_context.verify(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User has been disabled")

    access_token = create_access_token(data={"sub": user.username})
    return {"message": "Login successful", "access_token": access_token, "token_type": "bearer"}

@router.post("/whisper/transcriptions")
async def whisper_transcriptions(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 'current_user' 已经包含了认证通过的用户对象
    # 'file' 是一个 UploadFile 对象

    # 可以在这里添加速率限制逻辑
    # limiter.limit("...")(...)

    # 记录请求开始
    # ...

    audio_bytes = await file.read()
    # ... 调用 whisperclient 的逻辑 ...

    # 返回结果
    # ...
    return {"text": "transcribed_text"}

# Placeholder for other API endpoints
@router.get("/user/me")
async def read_users_me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username, "is_admin": current_user.is_admin}