from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_
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
from core.dependencies import (
    authenticate_user,
    build_access_token_payload,
    create_access_token,
    get_db,
    hash_password,
    is_user_expired,
    normalize_email,
    utcnow_naive,
)
from core.identity import build_default_display_name, has_bound_email, is_v2_identity_ready
from core.plan_catalog import get_paid_tiers
from core.quota_service import build_quota_snapshot
from core.v2_dependencies import get_current_v2_user
from core.v2_dependencies import revoke_v2_user_session
from db.models import PaymentOrder, RefreshToken, User, UserIdentityLink, UserSession, V2User, set_payment_order_remark
from routers.user_center import (
    PRICE_EPSILON,
    _calculate_addon_resource_from_amount,
    _calculate_daily_limit_from_price,
    _build_payment_guide_response,
    _ensure_order_record,
    _parse_payment_guide_token,
    _get_addon_price_step,
    _get_resource_increment_per_step,
    _get_user_price_and_limit_rule,
    _has_active_subscription,
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
    PortalLegacyLinkRequest,
    PortalLegacyLinkResponse,
    PortalLegacyLoginRequest,
    PortalLegacyLoginResponse,
    PortalMigrationStatusResponse,
    PortalOrderIntentResponse,
    PortalOrderResponse,
    PortalResetPasswordRequest,
    PortalUpdateProfileRequest,
    PortalUserResponse,
    PortalVerifyEmailRequest,
)

router = APIRouter()

PLACEHOLDER_PORTAL_PASSWORD = "__portal_authenticated_user__"


def _ensure_v2_billing_enabled() -> None:
    if not settings.API_V2_ENABLED or not settings.API_V2_PORTAL_ENABLED or not settings.API_V2_BILLING_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def _ensure_v2_legacy_migration_enabled() -> None:
    if not settings.API_V2_ENABLED or not settings.API_V2_PORTAL_ENABLED or not settings.API_V2_LEGACY_MIGRATION_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


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


def _account_status(user: V2User) -> str:
    expired, _ = is_user_expired(user)
    if expired:
        return "expired"
    if not user.is_active:
        return "inactive"
    return "active"


def _plan_price(user: V2User) -> float:
    price, _ = _get_user_price_and_limit_rule(user)
    return round(float(price), 2)


def _plan_name(price: float) -> str:
    normalized = round(float(price), 2)
    if normalized <= 0:
        return "Free"
    return f"CNY {normalized:.2f} / month"


def _user_roles(user: V2User) -> list[str]:
    return ["admin"] if user.is_admin else ["member"]


def _migration_required(user: V2User) -> bool:
    return not is_v2_identity_ready(
        email=getattr(user, "email", None),
        identity_version=getattr(user, "identity_version", 2),
    )


def _get_identity_link(db: Session, user_uuid: str) -> UserIdentityLink | None:
    return (
        db.query(UserIdentityLink)
        .filter(UserIdentityLink.user_uuid == user_uuid, UserIdentityLink.status == "linked")
        .order_by(UserIdentityLink.linked_at.asc(), UserIdentityLink.id.asc())
        .first()
    )


def _resolve_legacy_username(db: Session, user: V2User) -> str:
    if int(getattr(user, "legacy_user_id", 0) or 0) > 0:
        legacy_user = db.query(User).filter(User.id == user.legacy_user_id).first()
        if legacy_user is not None and legacy_user.username:
            return legacy_user.username
    link = _get_identity_link(db, user.user_uuid)
    if link is not None and link.legacy_username:
        return link.legacy_username
    return ""


def _serialize_user(user: V2User, db: Session) -> PortalUserResponse:
    display_name = (getattr(user, "display_name", None) or "").strip() or build_default_display_name(
        user_uuid=user.user_uuid,
        user_id=user.id,
    )
    price = _plan_price(user)
    return PortalUserResponse(
        id=str(user.id),
        userUuid=(user.user_uuid or "").strip(),
        username=user.username,
        legacyUsername=_resolve_legacy_username(db, user),
        displayName=display_name,
        email=(user.email or "").strip(),
        emailVerified=bool(getattr(user, "email_verified", False)),
        identityVersion=int(getattr(user, "identity_version", 2) or 2),
        migrationRequired=_migration_required(user),
        planName=_plan_name(price),
        planPrice=price,
        accountStatus=_account_status(user),
        roles=_user_roles(user),
        createdAt=_format_datetime(getattr(user, "created_at", None)),
        lastLoginAt=_format_datetime(getattr(user, "last_login_at", None)),
    )


def _normalize_display_name(value: Optional[str], user: V2User) -> str:
    normalized = (value or "").strip()
    if normalized:
        return normalized
    return build_default_display_name(user_uuid=user.user_uuid, user_id=user.id)


def _serialize_entitlements(user: V2User, db: Session) -> PortalEntitlementResponse:
    quota = build_quota_snapshot(db, user)
    plan_price = _plan_price(user)
    features = [
        f"Daily quota: {quota.daily_limit or _calculate_daily_limit_from_price(plan_price)}",
        f"Quota step-up per tier: +{_get_resource_increment_per_step()}",
    ]
    if _is_new_quota_purchase_enabled():
        features.append(f"Addon balance: {int(quota.addon_resource_balance or 0)}")

    return PortalEntitlementResponse(
        userUuid=(user.user_uuid or "").strip(),
        planName=_plan_name(plan_price),
        planPrice=plan_price,
        status="restricted" if _migration_required(user) else ("active" if _account_status(user) == "active" else "pending"),
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
        "addon": "Quota addon",
    }
    return action_map.get((order.action or "").strip().lower(), "Billing order")


def _build_pay_url(order: PaymentOrder) -> str:
    if not order.custom_id or order.amount is None or order.amount <= 0:
        return ""
    return afdian_service.build_donation_url(order.custom_id, float(order.amount))


def _guide_price_key(price: float) -> str:
    return f"{round(float(price or 0), 2):.2f}"


def _guide_prices(option_payload: dict) -> list[float]:
    if str(option_payload.get("pricing_mode") or "").strip().lower() == "ladder":
        return []

    prices = []
    for raw_price in option_payload.get("price_options") or []:
        try:
            prices.append(round(float(raw_price), 2))
        except (TypeError, ValueError):
            continue
    if prices:
        return prices

    default_price = option_payload.get("default_monthly_price")
    if default_price is None:
        return []
    try:
        return [round(float(default_price), 2)]
    except (TypeError, ValueError):
        return []


def _guide_option_for_action(raw_guide: dict, action: str) -> dict:
    if action == "renew":
        return raw_guide.get("renew_option") or {}
    if action == "upgrade":
        return raw_guide.get("upgrade_option") or {}
    if action == "addon":
        return raw_guide.get("addon_option") or {}
    return {}


def _validate_checkout_request_against_guide(
    user: V2User,
    payload: PortalCreateOrderIntentRequest,
    action: str,
    plan_price: float,
) -> None:
    guide_token = (payload.guideToken or "").strip()
    if not guide_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guideToken is required")

    try:
        parsed_guide = _parse_payment_guide_token(guide_token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid guideToken: {exc}") from exc

    if afdian_service.is_custom_id_expired(parsed_guide):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guideToken expired")
    if str(parsed_guide.get("username") or "").strip() != user.username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guideToken does not belong to the current account")

    raw_guide = _build_payment_guide_response(user, user.username)
    allowed_actions = [str(item).strip().lower() for item in raw_guide.get("allowed_actions", []) if str(item).strip()]
    if action not in allowed_actions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected action is no longer available")

    option_payload = _guide_option_for_action(raw_guide, action)
    if not option_payload or not bool(option_payload.get("allowed", False)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected action is no longer available")

    normalized_price = round(float(plan_price), 2)
    if action == "addon":
        min_amount = option_payload.get("min_amount")
        max_amount = option_payload.get("max_amount")
        step = float(option_payload.get("step") or _get_addon_price_step())

        if min_amount is not None and normalized_price + PRICE_EPSILON < round(float(min_amount), 2):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected addon amount is too low")
        if max_amount is not None and normalized_price > round(float(max_amount), 2) + PRICE_EPSILON:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected addon amount is too high")

        base_amount = round(float(min_amount), 2) if min_amount is not None else step
        offset = Decimal(str(normalized_price - base_amount)) / Decimal(str(step))
        if offset != offset.to_integral_value():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected addon amount is invalid")
        return

    allowed_prices = _guide_prices(option_payload)
    if allowed_prices and normalized_price not in allowed_prices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected tier is no longer available")

    if not allowed_prices:
        min_price = option_payload.get("min_monthly_price")
        max_price = option_payload.get("max_monthly_price")
        step = float(option_payload.get("step") or 0)
        if min_price is not None and normalized_price + PRICE_EPSILON < round(float(min_price), 2):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected tier is below the allowed range")
        if max_price is not None and normalized_price > round(float(max_price), 2) + PRICE_EPSILON:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected tier is above the allowed range")
        if step > 0 and min_price is not None:
            base_price = round(float(min_price), 2)
            offset = Decimal(str(normalized_price - base_price)) / Decimal(str(step))
            if offset != offset.to_integral_value():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected tier does not match the current pricing step")


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
        cancellable=status_text == "pending",
    )


def _serialize_guide_option(payload: dict) -> PortalBillingGuideOptionResponse:
    return PortalBillingGuideOptionResponse(
        allowed=bool(payload.get("allowed", False)),
        readonly=bool(payload.get("readonly", False)),
        pricingMode=str(payload.get("pricing_mode") or "fixed_tiers"),
        defaultMonthlyPrice=payload.get("default_monthly_price"),
        minMonthlyPrice=payload.get("min_monthly_price"),
        maxMonthlyPrice=payload.get("max_monthly_price"),
        step=payload.get("step"),
        targetResourceQuotaPreview=payload.get("target_resource_quota_preview"),
        priceOptions=[round(float(item), 2) for item in (payload.get("price_options") or [])],
        quotaPreviewByPrice={
            str(key): int(value)
            for key, value in (payload.get("quota_preview_by_price") or {}).items()
            if value is not None
        },
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


def _serialize_billing_guide(user: V2User) -> PortalBillingGuideResponse:
    raw = _build_payment_guide_response(user, user.username)
    if _migration_required(user):
        raw = {
            **raw,
            "status": "migration_required",
            "allowed_actions": [],
            "renew_option": {**(raw.get("renew_option") or {}), "allowed": False},
            "upgrade_option": {**(raw.get("upgrade_option") or {}), "allowed": False},
            "addon_option": {**(raw.get("addon_option") or {}), "allowed": False},
            "addon_enabled": False,
        }
    return PortalBillingGuideResponse(
        userUuid=(user.user_uuid or "").strip(),
        username=str(raw.get("username") or user.username),
        isRegistered=bool(raw.get("is_registered", True)),
        isExpired=bool(raw.get("is_expired", False)),
        status=str(raw.get("status") or "ok"),
        subscriptionMode=str(raw.get("subscription_mode") or "fixed_tiers"),
        priceStep=round(float(raw.get("price_step") or 5), 2),
        resourceIncrementPerStep=int(raw.get("resource_increment_per_step") or 0),
        displayPlanName=str(raw.get("display_plan_name") or _plan_name(_plan_price(user))),
        displayMonthlyPrice=round(float(raw.get("display_monthly_price") or _plan_price(user)), 2),
        displayResourceQuota=int(raw.get("display_resource_quota") or _calculate_daily_limit_from_price(_plan_price(user))),
        displayResourceSummary=str(raw.get("display_resource_summary") or ""),
        anchorReason=str(raw.get("anchor_reason") or "current"),
        allowedActions=[str(item) for item in raw.get("allowed_actions", []) if str(item).strip()],
        renewOption=_serialize_guide_option(raw.get("renew_option") or {}),
        upgradeOption=_serialize_guide_option(raw.get("upgrade_option") or {}),
        addonOption=_serialize_addon_guide(raw.get("addon_option") or {}),
        addonEnabled=bool(raw.get("addon_enabled", False)),
        addonResourceBalance=int(raw.get("addon_resource_balance") or 0),
        addonResourceTotal=int(raw.get("addon_resource_total") or 0),
        addonExpirationUtc=raw.get("addon_expiration_utc"),
        guideToken=str(raw.get("guide_token") or ""),
    )


def _build_plan_catalog(user: V2User) -> list[PortalBillingPlanResponse]:
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

    return plans


def _resolve_plan(user: V2User, plan_id: str) -> tuple[str, int, float, float]:
    parts = (plan_id or "").split(":")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid planId format")

    plan_type, raw_price, raw_months = parts
    months = _normalize_months(raw_months)
    current_price, _ = _get_user_price_and_limit_rule(user)
    expired, _ = is_user_expired(user)
    first_purchase_like = _is_first_purchase_like(user, expired)
    free_or_expired = expired or current_price <= PRICE_EPSILON

    if plan_type == "addon":
        addon_amount = _normalize_addon_amount(float(raw_price))
        if not _is_new_quota_purchase_enabled():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Resource packs are not available in the current billing mode")
        if not _has_active_subscription(user):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Active subscription required before buying a resource pack")
        return "addon", 1, addon_amount, addon_amount

    if plan_type != "subscription":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported plan type")

    target_price = _normalize_plan_price(float(raw_price))
    action = "renew"

    if first_purchase_like or free_or_expired:
        action = "renew"
    elif target_price > current_price:
        action = "upgrade"
    elif target_price < current_price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected tier must not be lower than current tier",
        )

    if action == "upgrade":
        donation_amount = round(max(0.0, target_price - current_price) * months, 2)
    else:
        donation_amount = round(target_price * months, 2)

    if donation_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Computed order amount is invalid")

    return action, months, target_price, donation_amount


def _resolve_checkout_request(user: V2User, payload: PortalCreateOrderIntentRequest) -> tuple[str, int, float, float]:
    requested_action = (payload.action or "").strip().lower()
    if requested_action and requested_action not in {"renew", "upgrade", "addon"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported billing action")
    if payload.planId:
        resolved = _resolve_plan(user, payload.planId)
    else:
        months = _normalize_months(payload.months or 1)
        if requested_action == "addon":
            if payload.addonAmount is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="addonAmount is required")
            addon_amount = _normalize_addon_amount(payload.addonAmount)
            resolved = ("addon", 1, addon_amount, addon_amount)
        else:
            if payload.targetMonthlyPrice is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="targetMonthlyPrice is required")
            plan_id = f"subscription:{float(payload.targetMonthlyPrice):.2f}:{months}"
            resolved = _resolve_plan(user, plan_id)

    action, months, plan_price, donation_amount = resolved
    if requested_action and requested_action != action:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected action does not match the target amount")
    _validate_checkout_request_against_guide(user, payload, action, plan_price)
    return action, months, plan_price, donation_amount


def _create_order_intent(
    db: Session,
    user: V2User,
    action: str,
    months: int,
    plan_price: float,
    donation_amount: float,
) -> PaymentOrder:
    addon_resource_amount = _calculate_addon_resource_from_amount(plan_price) if action == "addon" else 0
    custom_id = afdian_service.build_v2_custom_order_id(
        user_uuid=user.user_uuid,
        action=_normalize_action(action),
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
    order.user_uuid = user.user_uuid
    order.email = user.email
    order.plan_id = f"{action}:{plan_price:.2f}:{months}"
    order.billing_version = 2
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


def _revoke_all_sessions(db: Session, user: User, *, keep_session_id: str | None = None) -> None:
    now = utcnow_naive()
    sessions_query = db.query(UserSession).filter(UserSession.revoked_at.is_(None))
    if getattr(user, "user_uuid", None):
        sessions_query = sessions_query.filter(UserSession.user_uuid == user.user_uuid)
    else:
        sessions_query = sessions_query.filter(UserSession.user_id == user.id)
    sessions = sessions_query.all()
    for session in sessions:
        if keep_session_id and session.session_id == keep_session_id:
            continue
        session.revoked_at = now

    refresh_query = db.query(RefreshToken).filter(RefreshToken.revoked_at.is_(None))
    if getattr(user, "user_uuid", None):
        refresh_query = refresh_query.filter(RefreshToken.user_uuid == user.user_uuid)
    else:
        refresh_query = refresh_query.filter(RefreshToken.user_id == user.id)
    refresh_tokens = refresh_query.all()
    for token in refresh_tokens:
        if keep_session_id and token.session_id == keep_session_id:
            continue
        token.revoked_at = now


def _build_migration_token(user: User) -> str:
    return create_access_token(
        build_access_token_payload(user, session_type="migration", token_type="migration"),
        expires_delta=timedelta(days=1),
    )


def _ensure_identity_link(
    db: Session,
    *,
    target_user: V2User,
    legacy_user: User,
    client_ip: str | None,
    note: str,
) -> UserIdentityLink:
    existing = (
        db.query(UserIdentityLink)
        .filter(UserIdentityLink.legacy_username == legacy_user.username, UserIdentityLink.status == "linked")
        .first()
    )
    if existing is not None:
        if existing.user_uuid != target_user.user_uuid:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Legacy account is already linked to another account")
        return existing

    link = UserIdentityLink(
        user_uuid=target_user.user_uuid,
        legacy_user_id=legacy_user.id,
        legacy_username=legacy_user.username,
        legacy_email=legacy_user.email,
        link_type="legacy_password_bind",
        status="linked",
        linked_at=utcnow_naive(),
        linked_by_ip=client_ip,
        audit_note=note,
    )
    db.add(link)
    db.flush()
    return link


def _merge_legacy_assets(db: Session, target_user: V2User, legacy_user: User) -> tuple[int, int]:
    merged_orders = 0
    merged_addons = 0

    orders = (
        db.query(PaymentOrder)
        .filter(
            or_(PaymentOrder.user_uuid == legacy_user.user_uuid, PaymentOrder.username == legacy_user.username),
            or_(PaymentOrder.user_uuid.is_(None), PaymentOrder.user_uuid != target_user.user_uuid),
        )
        .all()
    )
    for order in orders:
        order.user_uuid = target_user.user_uuid
        if target_user.email:
            order.email = target_user.email
        if not order.billing_version:
            order.billing_version = 1
        merged_orders += 1

    legacy_balance = int(getattr(legacy_user, "addon_resource_balance", 0) or 0)
    legacy_total = int(getattr(legacy_user, "addon_resource_total", 0) or 0)
    if legacy_balance > 0:
        target_user.addon_resource_balance = int(target_user.addon_resource_balance or 0) + legacy_balance
        target_user.addon_resource_total = int(target_user.addon_resource_total or 0) + max(legacy_total, legacy_balance)
        if legacy_user.addon_expiration_date and (
            not target_user.addon_expiration_date or legacy_user.addon_expiration_date > target_user.addon_expiration_date
        ):
            target_user.addon_expiration_date = legacy_user.addon_expiration_date
        merged_addons = legacy_balance

    target_expiration = _to_utc(target_user.expiration_date)
    legacy_expiration = _to_utc(legacy_user.expiration_date)
    if legacy_expiration and (target_expiration is None or legacy_expiration > target_expiration):
        target_user.expiration_date = legacy_user.expiration_date
        if legacy_user.limit_rule:
            target_user.limit_rule = legacy_user.limit_rule
        target_user.is_active = bool(legacy_user.is_active)

    return merged_orders, merged_addons


@router.get("/account/profile", response_model=PortalUserResponse)
async def portal_profile(db: Session = Depends(get_db), current_user: V2User = Depends(get_current_v2_user)):
    return _serialize_user(current_user, db)


@router.patch("/account/profile", response_model=PortalUserResponse)
async def portal_update_profile(
    payload: PortalUpdateProfileRequest,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    if payload.displayName is not None:
        current_user.display_name = _normalize_display_name(payload.displayName, current_user)

    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user, db)


@router.get("/account/entitlements", response_model=PortalEntitlementResponse)
async def portal_entitlements(db: Session = Depends(get_db), current_user: V2User = Depends(get_current_v2_user)):
    return _serialize_entitlements(current_user, db)


@router.get("/account/orders", response_model=list[PortalOrderResponse])
async def portal_orders(db: Session = Depends(get_db), current_user: V2User = Depends(get_current_v2_user)):
    orders = (
        db.query(PaymentOrder)
        .filter(or_(PaymentOrder.user_uuid == current_user.user_uuid, PaymentOrder.username == current_user.username))
        .order_by(PaymentOrder.created_at.desc(), PaymentOrder.id.desc())
        .all()
    )
    return [_serialize_order(order) for order in orders]


@router.get("/account/migration-status", response_model=PortalMigrationStatusResponse)
async def portal_migration_status(
    _: None = Depends(_ensure_v2_legacy_migration_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    return PortalMigrationStatusResponse(
        migrationRequired=_migration_required(current_user),
        emailBound=bool(current_user.email),
        emailVerified=bool(current_user.email_verified),
        legacyUsername=_resolve_legacy_username(db, current_user),
        identityVersion=int(getattr(current_user, "identity_version", 1) or 1),
    )


@router.post("/account/legacy-login", response_model=PortalLegacyLoginResponse)
async def portal_legacy_login(
    payload: PortalLegacyLoginRequest,
    request: Request,
    _: None = Depends(_ensure_v2_legacy_migration_enabled),
    db: Session = Depends(get_db),
):
    legacy_username = payload.legacyUsername.strip()
    user = db.query(User).filter(User.username == legacy_username).first()
    if user is None or authenticate_user(db, legacy_username, payload.legacyPassword, allow_legacy_username=True) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid legacy credentials")
    if bool(getattr(user, "is_default_test_user", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Default test user cannot be migrated")
    if has_bound_email(getattr(user, "email", None)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Legacy account already upgraded, please use email login")

    return PortalLegacyLoginResponse(
        migrationRequired=True,
        emailBound=bool(user.email),
        emailVerified=bool(user.email_verified),
        legacyUserId=user.id,
        legacyUsername=user.username,
        migrationToken=_build_migration_token(user),
    )


@router.post("/account/legacy-link", response_model=PortalLegacyLinkResponse)
async def portal_legacy_link(
    payload: PortalLegacyLinkRequest,
    request: Request,
    _: None = Depends(_ensure_v2_legacy_migration_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    legacy_username = payload.legacyUsername.strip()
    legacy_user = db.query(User).filter(User.username == legacy_username).first()
    if legacy_user is None or authenticate_user(db, legacy_username, payload.legacyPassword, allow_legacy_username=True) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid legacy credentials")
    if legacy_user.id == int(getattr(current_user, "legacy_user_id", 0) or 0):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current account is already this legacy account")
    if bool(getattr(legacy_user, "is_default_test_user", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Default test user cannot be linked")

    if current_user.email_verified and legacy_user.email_verified and current_user.email and legacy_user.email:
        if normalize_email(current_user.email) != normalize_email(legacy_user.email):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Verified email conflict, manual merge required")

    _ensure_identity_link(
        db,
        target_user=current_user,
        legacy_user=legacy_user,
        client_ip=request.client.host if request.client else None,
        note="portal_legacy_link",
    )
    merged_orders, merged_addons = _merge_legacy_assets(db, current_user, legacy_user)
    db.commit()
    return PortalLegacyLinkResponse(
        message="Legacy account linked successfully",
        mergedOrders=merged_orders,
        mergedAddons=merged_addons,
    )


@router.post("/account/change-password/send-code", response_model=PortalActionResponse)
async def portal_send_change_password_code(
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    if not current_user.email or not current_user.email_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bind and verify your email before changing password")

    challenge = create_email_challenge(
        db,
        username=current_user.username,
        user_uuid=current_user.user_uuid,
        email=current_user.email,
        purpose=PASSWORD_CHANGE_PURPOSE,
    )
    send_email_code(email=current_user.email, purpose=PASSWORD_CHANGE_PURPOSE, code=challenge.code or "")
    db.commit()
    return _build_code_response("Verification code sent to your email", challenge.token)


@router.post("/account/change-password", response_model=PortalActionResponse)
async def portal_change_password(
    payload: PortalChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
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
    _revoke_all_sessions(db, current_user)
    consume_email_challenge(db, challenge)
    revoke_v2_user_session(db, request)
    db.commit()
    return PortalActionResponse(message="Password updated")


@router.post("/account/bind-email", response_model=PortalActionResponse)
async def portal_bind_email(
    payload: PortalBindEmailRequest,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    email = normalize_email(payload.email)
    duplicate_user = (
        db.query(V2User)
        .filter(V2User.email == email, V2User.user_uuid != current_user.user_uuid)
        .first()
    )
    if duplicate_user is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    verification_token = create_email_challenge(
        db,
        username=current_user.username,
        user_uuid=current_user.user_uuid,
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
    current_user: V2User = Depends(get_current_v2_user),
):
    record = get_active_email_challenge(
        db,
        token=payload.token.strip(),
        purpose=EMAIL_BIND_PURPOSE,
        code=(payload.code or "").strip(),
    )
    user = None
    if record.user_uuid:
        user = db.query(V2User).filter(V2User.user_uuid == record.user_uuid).first()
    if user is None:
        user = db.query(V2User).filter(V2User.username == record.username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.username != current_user.username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token does not belong to the current user")
    if not record.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain email")

    duplicate_user = db.query(V2User).filter(V2User.email == record.email, V2User.user_uuid != user.user_uuid).first()
    if duplicate_user is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    user.email = record.email
    user.email_verified = True
    if int(getattr(user, "identity_version", 2) or 2) < 2:
        user.identity_version = 2
    if not (getattr(user, "display_name", None) or "").strip():
        user.display_name = build_default_display_name(user_uuid=user.user_uuid, user_id=user.id)
    consume_email_challenge(db, record)
    db.commit()
    return PortalActionResponse(message="Email verified")


@router.post("/account/forgot-password", response_model=PortalActionResponse)
async def portal_forgot_password(payload: PortalForgotPasswordRequest, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    user = db.query(V2User).filter(V2User.email == email, V2User.email_verified == True).first()
    if user is None:
        return PortalActionResponse(message="If the email exists, a verification code has been prepared.")

    reset_token = create_email_challenge(
        db,
        username=user.username,
        user_uuid=user.user_uuid,
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
    user = None
    if record.user_uuid:
        user = db.query(V2User).filter(V2User.user_uuid == record.user_uuid).first()
    if user is None:
        user = db.query(V2User).filter(V2User.username == record.username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password = hash_password(payload.password)
    _revoke_all_sessions(db, user)
    consume_email_challenge(db, record)
    db.commit()
    return PortalActionResponse(message="Password reset complete")


@router.get("/billing/plans", response_model=list[PortalBillingPlanResponse])
async def portal_billing_plans(
    _: None = Depends(_ensure_v2_billing_enabled),
    current_user: V2User = Depends(get_current_v2_user),
):
    return _build_plan_catalog(current_user)


@router.get("/billing/guide", response_model=PortalBillingGuideResponse)
async def portal_billing_guide(
    _: None = Depends(_ensure_v2_billing_enabled),
    current_user: V2User = Depends(get_current_v2_user),
):
    return _serialize_billing_guide(current_user)


@router.post("/billing/order-intents", response_model=PortalOrderIntentResponse)
async def portal_create_order_intent(
    payload: PortalCreateOrderIntentRequest,
    _: None = Depends(_ensure_v2_billing_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    if _migration_required(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Finish account migration before billing")
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
    _: None = Depends(_ensure_v2_billing_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    order = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.id == order_id,
            or_(PaymentOrder.user_uuid == current_user.user_uuid, PaymentOrder.username == current_user.username),
        )
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
    _: None = Depends(_ensure_v2_billing_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    if _migration_required(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Finish account migration before billing")
    order = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.id == order_id,
            or_(PaymentOrder.user_uuid == current_user.user_uuid, PaymentOrder.username == current_user.username),
        )
        .first()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if _payment_status(order.status) != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only pending orders can be reopened")

    return PortalOrderIntentResponse(
        id=str(order.id),
        status=_payment_status(order.status),
        payUrl=_build_pay_url(order),
    )


@router.post("/billing/orders/{order_id}/cancel", response_model=PortalActionResponse)
async def portal_cancel_order(
    order_id: int,
    _: None = Depends(_ensure_v2_billing_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    order = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.id == order_id,
            or_(PaymentOrder.user_uuid == current_user.user_uuid, PaymentOrder.username == current_user.username),
        )
        .first()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    status_text = _payment_status(order.status)
    if status_text == "paid":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Paid orders cannot be cancelled")
    if status_text == "cancelled":
        return PortalActionResponse(message="Order already cancelled")

    order.status = "cancelled"
    order.updated_at = _utc_now_naive()
    db.commit()
    return PortalActionResponse(message="Order cancelled")
