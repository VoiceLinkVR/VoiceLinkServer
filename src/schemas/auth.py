from typing import Optional

from pydantic import BaseModel, Field


class AuthRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=100)


class AuthLoginRequest(BaseModel):
    login: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class AuthRefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1, max_length=255)


class AuthLogoutRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=1, max_length=255)


class AuthUserResponse(BaseModel):
    id: str
    username: str
    email: str = ""
    display_name: str
    email_verified: bool = False
    is_admin: bool = False


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: AuthUserResponse
