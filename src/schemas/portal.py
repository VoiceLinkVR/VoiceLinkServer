from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PortalChangePasswordRequest(BaseModel):
    verificationToken: str = Field(..., min_length=8, max_length=255)
    verificationCode: str = Field(..., min_length=4, max_length=16)
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
    migrationToken: Optional[str] = Field(default=None, min_length=1, max_length=255)


class PortalUpdateProfileRequest(BaseModel):
    displayName: Optional[str] = Field(default=None, max_length=100)


class PortalSendEmailCodeRequest(BaseModel):
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)


class PortalForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class PortalResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=4, max_length=16)
    password: str = Field(..., min_length=6, max_length=255)


class PortalVerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)
    code: Optional[str] = Field(default=None, min_length=1, max_length=32)
    migrationToken: Optional[str] = Field(default=None, min_length=1, max_length=255)


class PortalLegacyLoginRequest(BaseModel):
    legacyUsername: str = Field(..., min_length=1, max_length=50)
    legacyPassword: str = Field(..., min_length=1, max_length=255)


class PortalLegacyLinkRequest(BaseModel):
    legacyUsername: str = Field(..., min_length=1, max_length=50)
    legacyPassword: str = Field(..., min_length=1, max_length=255)


class PortalActionResponse(BaseModel):
    message: str
    nextUrl: str = ""
    token: str = ""
    expiresIn: int = 0


class PortalLegacyLoginResponse(BaseModel):
    migrationRequired: bool = True
    emailBound: bool = False
    emailVerified: bool = False
    legacyUserId: Optional[int] = None
    legacyUsername: str
    migrationToken: str


class PortalLegacyLinkResponse(BaseModel):
    message: str
    mergedOrders: int = 0
    mergedAddons: int = 0


class PortalMigrationStatusResponse(BaseModel):
    migrationRequired: bool = False
    emailBound: bool = False
    emailVerified: bool = False
    legacyUsername: str = ""
    identityVersion: int = 1


class PortalUserResponse(BaseModel):
    id: str
    userUuid: str
    username: str
    legacyUsername: str = ""
    displayName: str
    email: str = ""
    emailVerified: bool = False
    identityVersion: int = 1
    migrationRequired: bool = False
    planName: str
    planPrice: float = 0
    accountStatus: str
    roles: List[str]
    createdAt: Optional[str] = None
    lastLoginAt: Optional[str] = None


class PortalEntitlementResponse(BaseModel):
    userUuid: str
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
    cancellable: bool = False


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
    pricingMode: str = "fixed_tiers"
    defaultMonthlyPrice: Optional[float] = None
    minMonthlyPrice: Optional[float] = None
    maxMonthlyPrice: Optional[float] = None
    step: Optional[float] = None
    targetResourceQuotaPreview: Optional[int] = None
    priceOptions: List[float] = Field(default_factory=list)
    quotaPreviewByPrice: Dict[str, int] = Field(default_factory=dict)


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
    userUuid: str = ""
    username: str
    isRegistered: bool
    isExpired: bool
    status: str
    subscriptionMode: str = "fixed_tiers"
    priceStep: float
    resourceIncrementPerStep: int
    displayPlanName: str
    displayMonthlyPrice: float
    displayResourceQuota: int
    displayResourceSummary: str
    anchorReason: str
    allowedActions: List[str]
    renewOption: PortalBillingGuideOptionResponse
    upgradeOption: PortalBillingGuideOptionResponse
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
