from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from core.security_policy import validate_password_strength


class AdminAuthBootstrapRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=10, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_strength(value, context="password")


class AdminAuthLoginRequest(BaseModel):
    login: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class AdminUserResponse(BaseModel):
    id: str
    username: str
    email: str = ""
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
    password: str = Field(..., min_length=10, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    is_admin: bool = False
    is_active: bool = True
    limit_rule: Optional[str] = Field(default=None, max_length=100)
    expiration_date: Optional[datetime] = None

    @field_validator("password")
    @classmethod
    def validate_create_password(cls, value: str) -> str:
        return validate_password_strength(value, context="password")


class AdminUserUpdateRequest(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    password: Optional[str] = Field(default=None, min_length=10, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    limit_rule: Optional[str] = Field(default=None, max_length=100)
    expiration_date: Optional[datetime] = None

    @field_validator("password")
    @classmethod
    def validate_update_password(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return validate_password_strength(value, context="password")


class AdminUserEntitlementCorrectionRequest(BaseModel):
    plan_price: Optional[float] = Field(default=None, ge=0)
    daily_quota: Optional[int] = Field(default=None, ge=0)
    pricing_mode: Optional[str] = Field(default=None, min_length=1, max_length=32)
    plan_ref: Optional[str] = Field(default=None, max_length=100)
    subscription_status: Optional[str] = Field(default=None, min_length=1, max_length=32)
    subscription_expires_at: Optional[datetime] = None
    rate_limit_rule: Optional[str] = Field(default=None, max_length=255)
    addon_balance: Optional[int] = Field(default=None, ge=0)
    addon_total_purchased: Optional[int] = Field(default=None, ge=0)
    addon_expires_at: Optional[datetime] = None
    clear_addon: bool = False
    reset_to_free: bool = False

    @field_validator("pricing_mode")
    @classmethod
    def validate_pricing_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"free", "fixed_tiers", "ladder"}:
            raise ValueError("pricing_mode must be free, fixed_tiers or ladder")
        return normalized

    @field_validator("subscription_status")
    @classmethod
    def validate_subscription_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"free", "active", "inactive"}:
            raise ValueError("subscription_status must be free, active or inactive")
        return normalized


class AdminOrderLookupRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=512)


class AdminOrderActivateRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=512)


class AdminStatsQuery(BaseModel):
    hour: Optional[str] = Field(default=None, max_length=32)


class AdminAnnouncementItemResponse(BaseModel):
    id: str
    title: str
    content: str
    display_mode: str
    show_in_overview: bool = False
    sort_order: int
    is_published: bool
    published_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by_username: Optional[str] = None


class AdminAnnouncementListResponse(BaseModel):
    items: list[AdminAnnouncementItemResponse]


class AdminAnnouncementCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=50000)
    display_mode: str = Field(default="banner", min_length=4, max_length=16)
    show_in_overview: bool = False
    sort_order: int = Field(default=100, ge=0, le=999999)
    is_published: bool = False

    @field_validator("display_mode")
    @classmethod
    def validate_display_mode(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"banner", "modal"}:
            raise ValueError("display_mode must be banner or modal")
        return normalized


class AdminAnnouncementUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=50000)
    display_mode: Optional[str] = Field(default=None, min_length=4, max_length=16)
    show_in_overview: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=999999)
    is_published: Optional[bool] = None

    @field_validator("display_mode")
    @classmethod
    def validate_optional_display_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"banner", "modal"}:
            raise ValueError("display_mode must be banner or modal")
        return normalized


class AdminFaqItemResponse(BaseModel):
    id: str
    title: str
    content: str
    sort_order: int
    is_published: bool
    published_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by_username: Optional[str] = None


class AdminFaqListResponse(BaseModel):
    items: list[AdminFaqItemResponse]


class AdminFaqCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=50000)
    sort_order: int = Field(default=100, ge=0, le=999999)
    is_published: bool = False


class AdminFaqUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=50000)
    sort_order: Optional[int] = Field(default=None, ge=0, le=999999)
    is_published: Optional[bool] = None


class AdminSiteConfigGroupResponse(BaseModel):
    key: str
    label: str
    description: str
    count: int


class AdminSiteConfigItemResponse(BaseModel):
    key: str
    group: str
    group_label: str
    label: str
    description: str
    is_secret: bool
    requires_restart: bool
    source: str
    value: str
    raw_value: Any = None
    base_value: str
    base_raw_value: Any = None
    updated_at: Optional[str] = None
    updated_by_username: Optional[str] = None
    updated_note: Optional[str] = None


class AdminSiteConfigListResponse(BaseModel):
    groups: list[AdminSiteConfigGroupResponse]
    items: list[AdminSiteConfigItemResponse]


class AdminSiteConfigUpdateItemRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    value: Any = None
    clear: bool = False


class AdminSiteConfigUpdateRequest(BaseModel):
    items: list[AdminSiteConfigUpdateItemRequest] = Field(default_factory=list, min_length=1)
    change_note: Optional[str] = Field(default=None, max_length=500)


class AdminSiteConfigUpdateResponse(AdminSiteConfigListResponse):
    applied: bool
    message: str


class AdminSiteConfigAuditItemResponse(BaseModel):
    id: str
    config_key: str
    config_group: str
    operation: str
    is_secret: bool
    requires_restart: bool
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_fingerprint: Optional[str] = None
    new_fingerprint: Optional[str] = None
    changed_by_user_id: Optional[str] = None
    changed_by_username: Optional[str] = None
    changed_from_ip: Optional[str] = None
    change_note: Optional[str] = None
    created_at: Optional[str] = None


class AdminSiteConfigAuditResponse(BaseModel):
    items: list[AdminSiteConfigAuditItemResponse]
