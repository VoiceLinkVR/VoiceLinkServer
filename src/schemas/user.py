from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime

from core.security_policy import validate_password_strength

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    is_admin: bool = False
    is_active: bool = True
    limit_rule: Optional[str] = None
    expiration_date: Optional[datetime] = None

class UserCreate(UserBase):
    password: str = Field(..., min_length=10)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_strength(value, context="password")

class UserUpdate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: Optional[str] = Field(None, min_length=10)
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    limit_rule: Optional[str] = None
    expiration_date: Optional[datetime] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return validate_password_strength(value, context="password")

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
