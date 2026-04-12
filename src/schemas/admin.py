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
