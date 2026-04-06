from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.account_email_service import (
    EMAIL_BIND_PURPOSE,
    PASSWORD_CHANGE_PURPOSE,
    PASSWORD_RESET_PURPOSE,
    consume_email_challenge,
    create_email_challenge,
    get_active_email_challenge,
    send_email_code,
)
from core.afdian_service import afdian_service
from core.config import settings
from core.dependencies import get_current_user, get_db, hash_password, is_user_expired, normalize_email
from core.plan_catalog import get_paid_tiers
from core.quota_service import build_quota_snapshot
from db.models import PaymentOrder, User, set_payment_order_remark
from routers.user_center import (
    _calculate_addon_resource_from_amount,
    _calculate_daily_limit_from_price,
    _build_payment_guide_response,
    _ensure_order_record,
    _get_addon_price_step,
    _get_resource_increment_per_step,
    _get_user_price_and_limit_rule,
    _has_active_subscription,
    _is_downgrade_allowed,
    _is_first_purchase_like,
    _is_new_quota_purchase_enabled,
    _normalize_action,
    _normalize_addon_amount,
    _normalize_months,
    _normalize_plan_price,
    _to_utc_iso,
    _utc_now_naive,
)
from schemas.portal import (
    PortalActionResponse,
    PortalBillingAddonGuideResponse,
    PortalBillingGuideOptionResponse,
    PortalBillingGuideResponse,
    PortalBillingPlanResponse,
    PortalBindEmailRequest,
    PortalChangePasswordRequest,
    PortalCreateOrderIntentRequest,
    PortalEntitlementResponse,
    PortalForgotPasswordRequest,
    PortalOrderIntentResponse,
    PortalOrderResponse,
    PortalResetPasswordRequest,
    PortalUserResponse,
    PortalVerifyEmailRequest,
)

router = APIRouter()

PLACEHOLDER_PORTAL_PASSWORD = "__portal_authenticated_user__"


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _to_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _account_status(user: User) -> str:
    expired, _ = is_user_expired(user)
    if expired:
        return "expired"
    if not user.is_active:
        return "inactive"
    return "active"


def _plan_price(user: User) -> float:
    price, _ = _get_user_price_and_limit_rule(user)
    return round(float(price), 2)


def _plan_name(price: float) -> str:
    normalized = round(float(price), 2)
    if normalized <= 0:
        return "Free"
    return f"CNY {normalized:.2f} / month"


def _user_roles(user: User) -> list[str]:
    return ["admin"] if user.is_admin else ["member"]


def _serialize_user(user: User) -> PortalUserResponse:
    display_name = (getattr(user, "display_name", None) or user.username or "").strip()
    price = _plan_price(user)
    return PortalUserResponse(
        id=str(user.id),
        username=user.username,
        displayName=display_name,
        email=(user.email or "").strip(),
        emailVerified=bool(getattr(user, "email_verified", False)),
        planName=_plan_name(price),
        planPrice=price,
        accountStatus=_account_status(user),
        roles=_user_roles(user),
        createdAt=_format_datetime(getattr(user, "created_at", None)),
        lastLoginAt=_format_datetime(getattr(user, "last_login_at", None)),
    )


def _serialize_entitlements(user: User, db: Session) -> PortalEntitlementResponse:
    quota = build_quota_snapshot(db, user)
    plan_price = _plan_price(user)
    features = [
        f"Daily quota: {quota.daily_limit or _calculate_daily_limit_from_price(plan_price)}",
        f"Quota step-up per tier: +{_get_resource_increment_per_step()}",
    ]
    if _is_new_quota_purchase_enabled():
        features.append(f"Addon balance: {int(quota.addon_resource_balance or 0)}")

    return PortalEntitlementResponse(
        planName=_plan_name(plan_price),
        planPrice=plan_price,
        status="active" if _account_status(user) == "active" else "pending",
        remainingQuota=quota.remaining_daily,
        renewsAt=_to_utc_iso(user.expiration_date),
        features=features,
    )


def _payment_status(raw_status: Optional[str]) -> str:
    normalized = (raw_status or "").strip().lower()
    if normalized in {"activated", "paid"}:
        return "paid"
    if normalized in {"pending", "created"}:
        return "pending"
    if normalized in {"unpaid", "closed", "cancelled"}:
        return "cancelled"
    return normalized or "pending"


def _payment_title(order: PaymentOrder) -> str:
    action_map = {
        "renew": "Renew subscription",
        "upgrade": "Upgrade subscription",
        "downgrade": "Downgrade subscription",
        "addon": "Quota addon",
    }
    return action_map.get((order.action or "").strip().lower(), "Billing order")


def _build_pay_url(order: PaymentOrder) -> str:
    if not order.custom_id or order.amount is None or order.amount <= 0:
        return ""
    return afdian_service.build_donation_url(order.custom_id, float(order.amount))


def _serialize_order(order: PaymentOrder) -> PortalOrderResponse:
    status_text = _payment_status(order.status)
    return PortalOrderResponse(
        id=str(order.id),
        title=_payment_title(order),
        action=(order.action or "").strip().lower(),
        amount=round(float(order.amount or 0.0), 2),
        status=status_text,
        createdAt=_format_datetime(order.created_at),
        payUrl=_build_pay_url(order) if status_text == "pending" else "",
    )


def _serialize_guide_option(payload: dict) -> PortalBillingGuideOptionResponse:
    return PortalBillingGuideOptionResponse(
        allowed=bool(payload.get("allowed", False)),
        readonly=bool(payload.get("readonly", False)),
        defaultMonthlyPrice=payload.get("default_monthly_price"),
        minMonthlyPrice=payload.get("min_monthly_price"),
        maxMonthlyPrice=payload.get("max_monthly_price"),
        step=payload.get("step"),
        targetResourceQuotaPreview=payload.get("target_resource_quota_preview"),
    )


def _serialize_addon_guide(payload: dict) -> PortalBillingAddonGuideResponse:
    return PortalBillingAddonGuideResponse(
        allowed=bool(payload.get("allowed", False)),
        readonly=bool(payload.get("readonly", False)),
        defaultAmount=payload.get("default_amount"),
        minAmount=payload.get("min_amount"),
        maxAmount=payload.get("max_amount"),
        step=payload.get("step"),
        resourceIncrement=payload.get("resource_increment"),
        defaultResourceQuotaPreview=payload.get("default_resource_quota_preview"),
        requiresActiveSubscription=bool(payload.get("requires_active_subscription", False)),
    )


def _serialize_billing_guide(user: User) -> PortalBillingGuideResponse:
    raw = _build_payment_guide_response(user, user.username)
    return PortalBillingGuideResponse(
        username=str(raw.get("username") or user.username),
        isRegistered=bool(raw.get("is_registered", True)),
        isExpired=bool(raw.get("is_expired", False)),
        status=str(raw.get("status") or "ok"),
        priceStep=int(raw.get("price_step") or 5),
        resourceIncrementPerStep=int(raw.get("resource_increment_per_step") or 0),
        displayPlanName=str(raw.get("display_plan_name") or _plan_name(_plan_price(user))),
        displayMonthlyPrice=round(float(raw.get("display_monthly_price") or _plan_price(user)), 2),
        displayResourceQuota=int(raw.get("display_resource_quota") or _calculate_daily_limit_from_price(_plan_price(user))),
        displayResourceSummary=str(raw.get("display_resource_summary") or ""),
        anchorReason=str(raw.get("anchor_reason") or "current"),
        allowedActions=[str(item) for item in raw.get("allowed_actions", []) if str(item).strip()],
        renewOption=_serialize_guide_option(raw.get("renew_option") or {}),
        upgradeOption=_serialize_guide_option(raw.get("upgrade_option") or {}),
        downgradeOption=_serialize_guide_option(raw.get("downgrade_option") or {}),
        addonOption=_serialize_addon_guide(raw.get("addon_option") or {}),
        addonEnabled=bool(raw.get("addon_enabled", False)),
        addonResourceBalance=int(raw.get("addon_resource_balance") or 0),
        addonResourceTotal=int(raw.get("addon_resource_total") or 0),
        addonExpirationUtc=raw.get("addon_expiration_utc"),
        guideToken=str(raw.get("guide_token") or ""),
    )


def _build_plan_catalog(user: User) -> list[PortalBillingPlanResponse]:
    current_price = _plan_price(user)
    plans: list[PortalBillingPlanResponse] = []

    paid_tiers = get_paid_tiers()
    featured_price = paid_tiers[1]["monthly_price"] if len(paid_tiers) > 1 else paid_tiers[0]["monthly_price"]

    for tier in paid_tiers:
        price = round(float(tier["monthly_price"]), 2)
        if price + 0.0001 < current_price:
            continue

        direction = "Renew" if abs(price - current_price) <= 0.0001 and current_price > 0 else "Upgrade"
        daily_limit = int(tier["daily_quota"])
        plans.append(
            PortalBillingPlanResponse(
                id=f"subscription:{price:.2f}:1",
                name=f"{direction} {_plan_name(price)}",
                description="Create a billing intent against the current signed-in account.",
                price=price,
                featured=abs(price - float(featured_price)) <= 0.0001,
                features=[
                    f"Daily quota {daily_limit}",
                    "Order eligibility is rechecked on the server before payment.",
                ],
            )
        )

    if _is_new_quota_purchase_enabled():
        addon_price = round(_get_addon_price_step(), 2)
        addon_resource = _calculate_addon_resource_from_amount(addon_price)
        plans.append(
            PortalBillingPlanResponse(
                id=f"addon:{addon_price:.2f}:1",
                name="Quota addon",
                description="Available only for users with an active subscription.",
                price=addon_price,
                cycle="one-time",
                features=[
                    f"Adds {addon_resource} quota per purchase",
                    "Active subscription is required when creating the order.",
                ],
            )
        )

    return plans


def _resolve_plan(user: User, plan_id: str) -> tuple[str, int, float, float]:
    parts = (plan_id or "").split(":")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid planId format")

    plan_type, raw_price, raw_months = parts
    months = _normalize_months(raw_months)
    current_price, _ = _get_user_price_and_limit_rule(user)
    expired, _ = is_user_expired(user)
    first_purchase_like = _is_first_purchase_like(user, expired)

    if plan_type == "addon":
        if not _is_new_quota_purchase_enabled():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quota addon is not enabled")
        if not _has_active_subscription(user):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Active subscription required")
        addon_price = _normalize_addon_amount(float(raw_price))
        return "addon", months, addon_price, round(addon_price, 2)

    if plan_type != "subscription":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported plan type")

    target_price = _normalize_plan_price(float(raw_price))
    action = "renew"

    if first_purchase_like:
        action = "renew"
    elif target_price > current_price:
        action = "upgrade"
    elif target_price < current_price:
        action = "downgrade"

    if action == "downgrade" and not _is_downgrade_allowed(user, expired, current_price):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Downgrade is not allowed")

    if action == "upgrade":
        donation_amount = round(max(0.0, target_price - current_price) * months, 2)
    else:
        donation_amount = round(target_price * months, 2)

    if donation_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Computed order amount is invalid")

    return action, months, target_price, donation_amount


def _resolve_checkout_request(user: User, payload: PortalCreateOrderIntentRequest) -> tuple[str, int, float, float]:
    requested_action = (payload.action or "").strip().lower()
    if payload.planId:
        resolved = _resolve_plan(user, payload.planId)
    else:
        months = _normalize_months(payload.months or 1)
        if requested_action == "addon":
            if payload.addonAmount is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="addonAmount is required")
            plan_id = f"addon:{float(payload.addonAmount):.2f}:1"
        else:
            if payload.targetMonthlyPrice is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="targetMonthlyPrice is required")
            plan_id = f"subscription:{float(payload.targetMonthlyPrice):.2f}:{months}"
        resolved = _resolve_plan(user, plan_id)

    action, months, plan_price, donation_amount = resolved
    if requested_action and requested_action != action:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected action does not match the target amount")
    return action, months, plan_price, donation_amount


def _create_order_intent(
    db: Session,
    user: User,
    action: str,
    months: int,
    plan_price: float,
    donation_amount: float,
) -> PaymentOrder:
    addon_resource_amount = _calculate_addon_resource_from_amount(plan_price) if action == "addon" else 0
    custom_id = afdian_service.build_custom_id(
        user.username,
        PLACEHOLDER_PORTAL_PASSWORD,
        _normalize_action(action),
        extra_payload={
            "months": months,
            "plan_price": plan_price,
            "requested_amount": donation_amount,
            "target_monthly_price": plan_price,
            "portal": True,
            "addon_resource_amount": addon_resource_amount,
        },
    )

    order = _ensure_order_record(db, order_no=None, out_trade_no=None, custom_id=custom_id)
    set_payment_order_remark(order, custom_id)
    order.username = user.username
    order.action = action
    order.amount = donation_amount
    order.status = "pending"
    order.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(order)
    return order

def _build_code_response(message: str, token: str) -> PortalActionResponse:
    return PortalActionResponse(
        message=message,
        token=token,
        expiresIn=settings.EMAIL_CODE_TTL_MINUTES * 60,
    )


@router.get("/account/profile", response_model=PortalUserResponse)
async def portal_profile(current_user: User = Depends(get_current_user)):
    return _serialize_user(current_user)


@router.get("/account/entitlements", response_model=PortalEntitlementResponse)
async def portal_entitlements(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _serialize_entitlements(current_user, db)


@router.get("/account/orders", response_model=list[PortalOrderResponse])
async def portal_orders(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    orders = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.username == current_user.username)
        .order_by(PaymentOrder.created_at.desc(), PaymentOrder.id.desc())
        .all()
    )
    return [_serialize_order(order) for order in orders]


@router.post("/account/change-password/send-code", response_model=PortalActionResponse)
async def portal_send_change_password_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.email or not current_user.email_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bind and verify your email before changing password")

    challenge = create_email_challenge(
        db,
        username=current_user.username,
        email=current_user.email,
        purpose=PASSWORD_CHANGE_PURPOSE,
    )
    send_email_code(email=current_user.email, purpose=PASSWORD_CHANGE_PURPOSE, code=challenge.code or "")
    db.commit()
    return _build_code_response("Verification code sent to your email", challenge.token)


@router.post("/account/change-password", response_model=PortalActionResponse)
async def portal_change_password(
    payload: PortalChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.email or not current_user.email_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bind and verify your email before changing password")

    challenge = get_active_email_challenge(
        db,
        token=payload.verificationToken.strip(),
        purpose=PASSWORD_CHANGE_PURPOSE,
        code=payload.verificationCode.strip(),
    )
    if challenge.username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token does not belong to the current user")

    current_user.password = hash_password(payload.newPassword)
    consume_email_challenge(db, challenge)
    db.commit()
    return PortalActionResponse(message="Password updated")


@router.post("/account/bind-email", response_model=PortalActionResponse)
async def portal_bind_email(
    payload: PortalBindEmailRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    email = normalize_email(payload.email)
    duplicate_user = (
        db.query(User)
        .filter(User.email == email, User.username != current_user.username)
        .first()
    )
    if duplicate_user is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    verification_token = create_email_challenge(
        db,
        username=current_user.username,
        purpose=EMAIL_BIND_PURPOSE,
        email=email,
    )
    send_email_code(email=email, purpose=EMAIL_BIND_PURPOSE, code=verification_token.code or "")
    db.commit()
    return _build_code_response("Verification code sent to the new email address", verification_token.token)


@router.post("/account/verify-email", response_model=PortalActionResponse)
async def portal_verify_email(
    payload: PortalVerifyEmailRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = get_active_email_challenge(
        db,
        token=payload.token.strip(),
        purpose=EMAIL_BIND_PURPOSE,
        code=(payload.code or "").strip(),
    )
    user = db.query(User).filter(User.username == record.username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token does not belong to the current user")
    if not record.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain email")

    duplicate_user = db.query(User).filter(User.email == record.email, User.username != user.username).first()
    if duplicate_user is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    user.email = record.email
    user.email_verified = True
    consume_email_challenge(db, record)
    db.commit()
    return PortalActionResponse(message="Email verified")


@router.post("/account/forgot-password", response_model=PortalActionResponse)
async def portal_forgot_password(payload: PortalForgotPasswordRequest, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    user = db.query(User).filter(User.email == email, User.email_verified == True).first()
    if user is None:
        return PortalActionResponse(message="If the email exists, a verification code has been prepared.")

    reset_token = create_email_challenge(
        db,
        username=user.username,
        purpose=PASSWORD_RESET_PURPOSE,
        email=email,
    )
    send_email_code(email=email, purpose=PASSWORD_RESET_PURPOSE, code=reset_token.code or "")
    db.commit()
    return _build_code_response("Verification code sent to your email", reset_token.token)


@router.post("/account/reset-password", response_model=PortalActionResponse)
async def portal_reset_password(payload: PortalResetPasswordRequest, db: Session = Depends(get_db)):
    record = get_active_email_challenge(
        db,
        token=payload.token.strip(),
        purpose=PASSWORD_RESET_PURPOSE,
        code=payload.code.strip(),
    )
    user = db.query(User).filter(User.username == record.username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password = hash_password(payload.password)
    consume_email_challenge(db, record)
    db.commit()
    return PortalActionResponse(message="Password reset complete")


@router.get("/billing/plans", response_model=list[PortalBillingPlanResponse])
async def portal_billing_plans(current_user: User = Depends(get_current_user)):
    return _build_plan_catalog(current_user)


@router.get("/billing/guide", response_model=PortalBillingGuideResponse)
async def portal_billing_guide(current_user: User = Depends(get_current_user)):
    return _serialize_billing_guide(current_user)


@router.post("/billing/order-intents", response_model=PortalOrderIntentResponse)
async def portal_create_order_intent(
    payload: PortalCreateOrderIntentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    action, months, plan_price, donation_amount = _resolve_checkout_request(current_user, payload)
    order = _create_order_intent(db, current_user, action, months, plan_price, donation_amount)
    return PortalOrderIntentResponse(
        id=str(order.id),
        status=_payment_status(order.status),
        payUrl=_build_pay_url(order),
    )


@router.get("/billing/order-intents/{order_id}", response_model=PortalOrderIntentResponse)
async def portal_order_intent_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.id == order_id, PaymentOrder.username == current_user.username)
        .first()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order intent not found")

    return PortalOrderIntentResponse(
        id=str(order.id),
        status=_payment_status(order.status),
        payUrl=_build_pay_url(order),
    )


@router.post("/billing/orders/{order_id}/pay", response_model=PortalOrderIntentResponse)
async def portal_pay_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.id == order_id, PaymentOrder.username == current_user.username)
        .first()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    return PortalOrderIntentResponse(
        id=str(order.id),
        status=_payment_status(order.status),
        payUrl=_build_pay_url(order),
    )
