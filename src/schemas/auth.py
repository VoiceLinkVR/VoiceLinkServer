from typing import Optional

from pydantic import BaseModel, Field, model_validator


class AuthRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=255)
    verification_token: str = Field(..., min_length=8, max_length=255)
    verification_code: str = Field(..., min_length=4, max_length=16)


class AuthRegisterCodeRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class AuthLoginRequest(BaseModel):
    login: Optional[str] = Field(default=None, min_length=1, max_length=255)
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)

    @model_validator(mode="after")
    def ensure_login_value(self) -> "AuthLoginRequest":
        if not (self.login or self.email):
            raise ValueError("Either login or email is required")
        return self


class AuthRefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1, max_length=255)


class AuthLogoutRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=1, max_length=255)


class AuthDeviceStartRequest(BaseModel):
    client_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    scope: Optional[str] = Field(default="vrcls.client", min_length=1, max_length=255)


class AuthDeviceAuthorizeRequest(BaseModel):
    user_code: str = Field(..., min_length=4, max_length=32)
    decision: str = Field(default="approve", min_length=4, max_length=16)


class AuthDeviceTokenRequest(BaseModel):
    device_code: str = Field(..., min_length=16, max_length=255)


class AuthUserResponse(BaseModel):
    id: str
    user_uuid: str
    username: str
    legacy_username: str = ""
    email: str = ""
    display_name: str
    email_verified: bool = False
    is_admin: bool = False
    identity_version: int = 1
    migration_required: bool = False


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: AuthUserResponse


class AuthVerificationChallengeResponse(BaseModel):
    message: str
    verification_token: str
    expires_in: int


class AuthDeviceStartResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int = 5


class AuthDeviceRequestResponse(BaseModel):
    user_code: str
    client_name: str
    requested_scope: str
    status: str
    expires_at: str
    username: str = ""
    user_uuid: str = ""
    display_name: str = ""


class AuthDeviceActionResponse(BaseModel):
    message: str
    status: str


class AuthDeviceTokenResponse(BaseModel):
    status: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: Optional[int] = None
    poll_interval: int = 5
    user: Optional[AuthUserResponse] = None
