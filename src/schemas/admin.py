from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AdminAuthBootstrapRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=100)


class AdminAuthLoginRequest(BaseModel):
    login: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class AdminUserResponse(BaseModel):
    id: str
    username: str
    email: str = ""
    display_name: str
    email_verified: bool = False
    is_admin: bool = True


class AdminAuthStateResponse(BaseModel):
    has_admin: bool
    user: Optional[AdminUserResponse] = None


class AdminAuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: AdminUserResponse


class AdminUserListResponse(BaseModel):
    items: list[dict[str, Any]]
    summary: dict[str, Any]


class AdminUserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=100)
    is_admin: bool = False
    is_active: bool = True
    limit_rule: Optional[str] = Field(default=None, max_length=100)
    expiration_date: Optional[datetime] = None


class AdminUserUpdateRequest(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    password: Optional[str] = Field(default=None, min_length=6, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=100)
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    limit_rule: Optional[str] = Field(default=None, max_length=100)
    expiration_date: Optional[datetime] = None


class AdminOrderLookupRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=512)


class AdminOrderActivateRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=512)


class AdminStatsQuery(BaseModel):
    hour: Optional[str] = Field(default=None, max_length=32)


class AdminPlanTierPayload(BaseModel):
    monthly_price: float = Field(..., gt=0)
    daily_quota: int = Field(..., gt=0)


class AdminPlanTierResponse(BaseModel):
    id: str
    monthly_price: float
    daily_quota: int


class AdminPlanCatalogResponse(BaseModel):
    free_plan: AdminPlanTierResponse
    tiers: list[AdminPlanTierResponse]


class AdminPlanCatalogUpdateRequest(BaseModel):
    free_daily_quota: int = Field(..., gt=0)
    tiers: list[AdminPlanTierPayload] = Field(default_factory=list, min_length=1)
