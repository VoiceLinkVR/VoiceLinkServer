from typing import List, Optional

from pydantic import BaseModel, Field


class PortalChangePasswordRequest(BaseModel):
    currentPassword: str = Field(..., min_length=1, max_length=255)
    newPassword: str = Field(..., min_length=6, max_length=255)


class PortalCreateOrderIntentRequest(BaseModel):
    planId: Optional[str] = Field(default=None, min_length=1, max_length=100)
    action: Optional[str] = Field(default=None, min_length=1, max_length=32)
    months: Optional[int] = Field(default=None, ge=1, le=24)
    targetMonthlyPrice: Optional[float] = Field(default=None, ge=0)
    addonAmount: Optional[float] = Field(default=None, ge=0)
    guideToken: Optional[str] = Field(default=None, min_length=1, max_length=2048)


class PortalBindEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class PortalForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class PortalResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=6, max_length=255)


class PortalVerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)
    code: Optional[str] = Field(default=None, min_length=1, max_length=32)


class PortalActionResponse(BaseModel):
    message: str
    nextUrl: str = ""
    token: str = ""


class PortalUserResponse(BaseModel):
    id: str
    username: str
    displayName: str
    email: str = ""
    emailVerified: bool = False
    planName: str
    planPrice: float = 0
    accountStatus: str
    roles: List[str]
    createdAt: Optional[str] = None
    lastLoginAt: Optional[str] = None


class PortalEntitlementResponse(BaseModel):
    planName: str
    planPrice: float = 0
    status: str
    remainingQuota: Optional[int] = None
    renewsAt: Optional[str] = None
    features: List[str]


class PortalOrderResponse(BaseModel):
    id: str
    title: str
    action: str = ""
    amount: float
    currency: str = "CNY"
    status: str
    createdAt: Optional[str] = None
    payUrl: str = ""


class PortalBillingPlanResponse(BaseModel):
    id: str
    name: str
    description: str
    price: float
    currency: str = "CNY"
    cycle: str = "monthly"
    featured: bool = False
    features: List[str]


class PortalBillingGuideOptionResponse(BaseModel):
    allowed: bool
    readonly: bool
    defaultMonthlyPrice: Optional[float] = None
    minMonthlyPrice: Optional[float] = None
    maxMonthlyPrice: Optional[float] = None
    step: Optional[float] = None
    targetResourceQuotaPreview: Optional[int] = None


class PortalBillingAddonGuideResponse(BaseModel):
    allowed: bool
    readonly: bool
    defaultAmount: Optional[float] = None
    minAmount: Optional[float] = None
    maxAmount: Optional[float] = None
    step: Optional[float] = None
    resourceIncrement: Optional[int] = None
    defaultResourceQuotaPreview: Optional[int] = None
    requiresActiveSubscription: bool = False


class PortalBillingGuideResponse(BaseModel):
    username: str
    isRegistered: bool
    isExpired: bool
    status: str
    priceStep: int
    resourceIncrementPerStep: int
    displayPlanName: str
    displayMonthlyPrice: float
    displayResourceQuota: int
    displayResourceSummary: str
    anchorReason: str
    allowedActions: List[str]
    renewOption: PortalBillingGuideOptionResponse
    upgradeOption: PortalBillingGuideOptionResponse
    downgradeOption: PortalBillingGuideOptionResponse
    addonOption: PortalBillingAddonGuideResponse
    addonEnabled: bool = False
    addonResourceBalance: int = 0
    addonResourceTotal: int = 0
    addonExpirationUtc: Optional[str] = None
    guideToken: str


class PortalOrderIntentResponse(BaseModel):
    id: str
    status: str
    payUrl: str = ""
