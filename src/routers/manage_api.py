from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict

from core.dependencies import get_db, get_current_user, get_current_admin_user, hash_password
from db.models import User
from schemas.user import UserCreate, UserUpdate

router = APIRouter()

class UserCredentials(BaseModel):
    username: str
    password: str

class UserDelete(BaseModel):
    username: str

@router.post("/registerAdmin")
async def register_admin(creds: UserCredentials, db: Session = Depends(get_db)):
    admin_count = db.query(User).filter_by(is_admin=True).count()
    if admin_count != 0:
        # 如果已有管理员，需要管理员权限来创建新管理员
        # 此处简化为直接调用 get_current_admin_user，它会进行JWT验证和权限检查
        # 注意：这需要客户端在请求头中提供一个已存在的管理员的JWT
        try:
            await get_current_admin_user(await get_current_user(db=db))
        except HTTPException:
             raise HTTPException(status_code=403, detail="An admin already exists. Admin rights required to create another.")

    if db.query(User).filter_by(username=creds.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed_password = hash_password(creds.password)
    new_admin = User(username=creds.username, password=hashed_password, is_admin=True, is_active=True)
    db.add(new_admin)
    db.commit()
    return {"message": "AdminUser created successfully"}

@router.post("/changePassword")
async def change_password(creds: UserCredentials, admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    user_to_change = db.query(User).filter_by(username=creds.username).first()
    if not user_to_change:
        raise HTTPException(status_code=404, detail="User not found")

    user_to_change.password = hash_password(creds.password)
    db.commit()
    return {"message": f"user:{creds.username}, Password changed successfully"}

@router.post("/register")
async def register(creds: UserCredentials, admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    if db.query(User).filter_by(username=creds.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed_password = hash_password(creds.password)
    new_user = User(username=creds.username, password=hashed_password, is_admin=False, is_active=True)
    db.add(new_user)
    db.commit()
    return {"message": "User created successfully"}

@router.post("/addUser")
async def add_user(user_data: UserCreate, admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    """
    添加新用户，支持设置所有字段
    """
    if db.query(User).filter_by(username=user_data.username).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists")

    hashed_password = hash_password(user_data.password)
    new_user = User(
        username=user_data.username,
        password=hashed_password,
        is_admin=user_data.is_admin,
        is_active=user_data.is_active,
        limit_rule=user_data.limit_rule,
        expiration_date=user_data.expiration_date
    )
    db.add(new_user)
    db.commit()
    return {"message": "User created successfully", "user": {"username": user_data.username}}

@router.post("/updateUser")
async def update_user(user_data: UserUpdate, admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    """
    更新用户信息，支持修改所有字段
    """
    user_to_update = db.query(User).filter_by(username=user_data.username).first()
    if not user_to_update:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Username not exist")

    # 更新密码（如果提供了新密码）
    if user_data.password:
        user_to_update.password = hash_password(user_data.password)

    # 更新其他字段
    if user_data.is_admin is not None:
        user_to_update.is_admin = user_data.is_admin
    if user_data.is_active is not None:
        user_to_update.is_active = user_data.is_active
    if user_data.limit_rule is not None:
        user_to_update.limit_rule = user_data.limit_rule
    if user_data.expiration_date is not None:
        user_to_update.expiration_date = user_data.expiration_date

    db.commit()
    return {"message": f"user:{user_data.username}, updated successfully"}

@router.post("/deleteUser")
async def delete_user(user_data: UserDelete, admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    user_to_delete = db.query(User).filter_by(username=user_data.username).first()
    if not user_to_delete:
        raise HTTPException(status_code=400, detail="Username not exist")

    db.delete(user_to_delete)
    db.commit()
    return {"message": "User deleted successfully"}