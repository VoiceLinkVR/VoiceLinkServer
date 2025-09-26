from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    is_admin: bool = False
    is_active: bool = True
    limit_rule: Optional[str] = None
    expiration_date: Optional[datetime] = None

class UserCreate(UserBase):
    password: str = Field(..., min_length=6)

class UserResponse(UserBase):
    id: int

    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    message: str = "Login successful"

class TokenData(BaseModel):
    username: Optional[str] = None