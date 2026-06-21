import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import case, func, or_
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
from core.content_service import list_published_announcements_payload, list_published_faqs_payload
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
from core.entitlement_service import (
    build_subscription_snapshot_for_existing_values,
    ensure_user_entitlement_state,
    rebuild_user_entitlement_state_from_legacy_fields,
    set_payment_order_snapshot,
    update_user_subscription_state,
)
from core.identity import has_bound_email, is_v2_identity_ready
from core.plan_catalog import get_paid_tiers
from core.quota_service import build_quota_snapshot, get_daily_subscription_limit
from core.rate_limiter import enforce_auth_attempt_rate_limit
from core import support_ticket_service
from core.v2_request_metrics import get_subject_daily_usage_row, list_subject_daily_usage_rows, resolve_usage_subject_for_user
from core.v2_dependencies import get_current_v2_user
from core.v2_dependencies import revoke_v2_user_session
from db.models import (
    LegacyPaymentOrder,
    PaymentOrder,
    RefreshToken,
    User,
    UserIdentityLink,
    UserSession,
    V2User,
    payment_lookup_digest,
    set_payment_order_custom_id,
    set_payment_order_remark,
)
from routers.user_center import (
    AFDIAN_WEBHOOK_FORWARD_HEADER,
    AFDIAN_WEBHOOK_OK,
    _process_afdian_webhook_payload,
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
    _resolve_upgrade_charge_months,
    _normalize_action,
    _normalize_addon_amount,
    _normalize_months,
    _normalize_plan_price,
    _safe_parse_amount,
    _to_utc_iso,
    _utc_now_naive,
)
from schemas.portal import (
    PortalActionResponse,
    PortalBillingAddonGuideResponse,
    PortalBillingGuideOptionResponse,
    PortalBillingGuideResponse,
    PortalBillingPlanResponse,
    PortalAnnouncementResponse,
    PortalBindEmailRequest,
    PortalChangePasswordRequest,
    PortalCreateOrderIntentRequest,
    PortalCreateSupportTicketRequest,
    PortalEntitlementResponse,
    PortalFaqItemResponse,
    PortalForgotPasswordRequest,
    PortalLegacyLinkRequest,
    PortalLegacyLinkResponse,
    PortalLegacyLoginRequest,
    PortalLegacyLoginResponse,
    PortalMigrationStatusResponse,
    PortalOrderIntentResponse,
    PortalOrderResponse,
    PortalResetPasswordRequest,
    PortalSupportTicketResponse,
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


def _form_text(value: object) -> str | None:
    if value is None or hasattr(value, "filename"):
        return None
    return str(value)


async def _read_support_ticket_payload(request: Request) -> tuple[PortalCreateSupportTicketRequest, list[UploadFile]]:
    content_type = request.headers.get("content-type", "").lower()
    attachments: list[UploadFile] = []

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key, value in form.multi_items():
            if key == support_ticket_service.ATTACHMENT_FIELD_NAME and hasattr(value, "filename") and hasattr(value, "read"):
                attachments.append(value)  # type: ignore[arg-type]
        try:
            payload = PortalCreateSupportTicketRequest(
                subject=_form_text(form.get("subject")),
                message=_form_text(form.get("message")),
                category=_form_text(form.get("category")),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc
        return payload, attachments

    try:
        raw_payload = await request.json()
    except Exception:
        raw_payload = {}
    if not isinstance(raw_payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ticket payload")
    try:
        return PortalCreateSupportTicketRequest.model_validate(raw_payload), attachments
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc


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


def _to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    resolved = _to_utc(value)
    if resolved is None:
        return None
    return resolved.replace(tzinfo=None)


def _extract_daily_limit_from_rule(limit_rule: Optional[str]) -> Optional[int]:
    for part in (segment.strip() for segment in str(limit_rule or "").split(";") if segment.strip()):
        lowered = part.lower()
        if lowered.endswith("/day"):
            try:
                return max(0, int(part.split("/", 1)[0].strip()))
            except (TypeError, ValueError):
                return None
    return None


def _legacy_daily_quota_to_price(daily_quota: Optional[int]) -> Optional[float]:
    if daily_quota is None or daily_quota <= 0:
        return None
    normalized_quota = max(0, int(daily_quota))
    return round((normalized_quota / 1500.0) * 5.0, 2)


def _extract_legacy_order_payload(order: LegacyPaymentOrder) -> dict[str, object]:
    raw_payload = getattr(order, "raw_payload", None)
    if not raw_payload:
        return {}
    try:
        parsed = json.loads(raw_payload)
    except Exception:
        return {}

    if not isinstance(parsed, dict):
        return {}

    custom_payload = parsed.get("custom_payload")
    if isinstance(custom_payload, dict):
        merged = dict(parsed)
        merged.update(custom_payload)
        return merged
    return parsed


def _resolve_legacy_subscription_basis(db: Session, legacy_user: User) -> tuple[Optional[float], Optional[int], Optional[int]]:
    orders = (
        db.query(LegacyPaymentOrder)
        .filter(
            LegacyPaymentOrder.username == legacy_user.username,
            LegacyPaymentOrder.status == "activated",
            LegacyPaymentOrder.action != "addon",
        )
        .order_by(LegacyPaymentOrder.activated_at.desc(), LegacyPaymentOrder.updated_at.desc(), LegacyPaymentOrder.id.desc())
        .all()
    )

    for order in orders:
        payload = _extract_legacy_order_payload(order)
        raw_plan_price = payload.get("plan_price")
        raw_limit_rule = payload.get("limit_rule")
        daily_quota = _extract_daily_limit_from_rule(str(raw_limit_rule or "").strip() or None)
        plan_price = None
        try:
            if raw_plan_price is not None:
                plan_price = round(float(raw_plan_price), 2)
        except (TypeError, ValueError):
            plan_price = None
        if plan_price is None:
            plan_price = _legacy_daily_quota_to_price(daily_quota)
        if plan_price is not None and plan_price > 0:
            return plan_price, daily_quota, order.id

    fallback_daily_quota = _extract_daily_limit_from_rule(getattr(legacy_user, "limit_rule", None))
    fallback_plan_price = _legacy_daily_quota_to_price(fallback_daily_quota)
    return fallback_plan_price, fallback_daily_quota, None


def _calculate_remaining_subscription_value(
    *,
    legacy_expiration: Optional[datetime],
    legacy_plan_price: Optional[float],
    bind_time_utc: datetime,
) -> tuple[float, float]:
    if legacy_expiration is None or legacy_plan_price is None or legacy_plan_price <= 0:
        return 0.0, 0.0

    remaining_seconds = max(0.0, (legacy_expiration - bind_time_utc).total_seconds())
    if remaining_seconds <= 0:
        return 0.0, 0.0

    remaining_days = remaining_seconds / 86400.0
    remaining_value = (remaining_days / float(settings.AFDIAN_RENEW_DAYS)) * float(legacy_plan_price)
    return remaining_value, remaining_days


def _convert_value_to_days(remaining_value: float, target_plan_price: float) -> int:
    if remaining_value <= 0 or target_plan_price <= 0:
        return 0
    converted_days = (float(remaining_value) / float(target_plan_price)) * float(settings.AFDIAN_RENEW_DAYS)
    return max(1, int(math.ceil(converted_days)))


def _merge_legacy_subscription_value(db: Session, target_user: V2User, legacy_user: User) -> None:
    bind_time_utc = datetime.now(timezone.utc)
    legacy_expiration_utc = _to_utc(getattr(legacy_user, "expiration_date", None))
    if legacy_expiration_utc is None or legacy_expiration_utc <= bind_time_utc or not bool(getattr(legacy_user, "is_active", True)):
        return

    legacy_plan_price, legacy_daily_quota, source_order_id = _resolve_legacy_subscription_basis(db, legacy_user)
    remaining_value, _ = _calculate_remaining_subscription_value(
        legacy_expiration=legacy_expiration_utc,
        legacy_plan_price=legacy_plan_price,
        bind_time_utc=bind_time_utc,
    )
    if remaining_value <= PRICE_EPSILON:
        return

    target_state, _ = ensure_user_entitlement_state(db, target_user)
    target_expiration_utc = _to_utc(getattr(target_user, "expiration_date", None))
    target_has_active_paid = (
        bool(getattr(target_user, "is_active", True))
        and target_expiration_utc is not None
        and target_expiration_utc > bind_time_utc
        and float(getattr(target_state, "plan_price", 0) or 0) > PRICE_EPSILON
    )

    if target_has_active_paid:
        current_price = round(float(target_state.plan_price or 0), 2)
        converted_days = _convert_value_to_days(remaining_value, current_price)
        if converted_days <= 0:
            return
        base_expiration_utc = max(target_expiration_utc, bind_time_utc)
        next_expiration = base_expiration_utc + timedelta(days=converted_days)
        update_user_subscription_state(
            db,
            target_user,
            plan_price=current_price,
            expires_at=_to_naive_utc(next_expiration),
            source_order_id=source_order_id,
            pricing_mode=str(target_state.pricing_mode or "").strip() or None,
            status="active",
            plan_ref=str(target_state.plan_ref or "").strip() or None,
            daily_quota=int(target_state.daily_quota or 0) or None,
            rate_limit_rule=str(target_state.rate_limit_rule or "").strip() or None,
        )
        return

    normalized = build_subscription_snapshot_for_existing_values(
        legacy_plan_price or 0,
        daily_quota=legacy_daily_quota,
    )
    target_plan_price = round(float(normalized["plan_price"]), 2)
    converted_days = _convert_value_to_days(remaining_value, target_plan_price)
    if converted_days <= 0:
        return

    next_expiration = bind_time_utc + timedelta(days=converted_days)
    update_user_subscription_state(
        db,
        target_user,
        plan_price=target_plan_price,
        expires_at=_to_naive_utc(next_expiration),
        source_order_id=source_order_id,
        pricing_mode=str(normalized["pricing_mode"]),
        status="active",
        plan_ref=str(normalized["plan_ref"]),
        daily_quota=int(normalized["daily_quota"]),
        rate_limit_rule=str(normalized["rate_limit_rule"]),
        catalog_version=str(normalized["catalog_version"]),
    )


def _account_status(user: V2User) -> str:
    expired, _ = is_user_expired(user)
    if expired:
        return "expired"
    if not user.is_active:
        return "inactive"
    return "active"


def _plan_price(db: Session, user: V2User) -> float:
    subscription_state, _ = ensure_user_entitlement_state(db, user)
    return round(float(subscription_state.plan_price or 0), 2)


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
    price = _plan_price(db, user)
    return PortalUserResponse(
        id=str(user.id),
        userUuid=(user.user_uuid or "").strip(),
        username=user.username,
        legacyUsername=_resolve_legacy_username(db, user),
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

def _serialize_entitlements(user: V2User, db: Session) -> PortalEntitlementResponse:
    quota = build_quota_snapshot(db, user)
    plan_price = _plan_price(db, user)
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


def _summarize_guide_token(token: str) -> dict[str, object]:
    normalized = (token or "").strip()
    summary: dict[str, object] = {
        "present": bool(normalized),
        "length": len(normalized),
    }
    if not normalized:
        return summary

    try:
        payload = _parse_payment_guide_token(normalized)
    except Exception as exc:
        summary["parse_error"] = str(exc)
        return summary

    summary["payload"] = {
        "username": str(payload.get("username") or "").strip() or None,
        "user_uuid": str(payload.get("user_uuid") or "").strip() or None,
        "current_price": payload.get("current_price"),
        "subscription_mode": payload.get("subscription_mode"),
        "is_expired": payload.get("is_expired"),
        "ts": payload.get("ts"),
    }
    return summary


def _build_order_intent_audit_payload(
    current_user: V2User,
    payload: PortalCreateOrderIntentRequest,
    *,
    result: dict[str, object] | None = None,
    error_detail: str | None = None,
) -> dict[str, object]:
    audit_payload: dict[str, object] = {
        "user": {
            "user_uuid": (getattr(current_user, "user_uuid", None) or "").strip() or None,
            "username": current_user.username,
        },
        "request": {
            "planId": payload.planId,
            "action": payload.action,
            "months": payload.months,
            "targetMonthlyPrice": payload.targetMonthlyPrice,
            "addonAmount": payload.addonAmount,
            "guideToken": _summarize_guide_token(payload.guideToken or ""),
        },
    }
    if result is not None:
        audit_payload["result"] = result
    if error_detail:
        audit_payload["error"] = {"detail": error_detail}
    return audit_payload


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
    guide_user_uuid = str(parsed_guide.get("user_uuid") or "").strip()
    if guide_user_uuid:
        if guide_user_uuid != (user.user_uuid or "").strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guideToken does not belong to the current account")
    elif str(parsed_guide.get("username") or "").strip() != user.username:
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


def _serialize_order(order: PaymentOrder, support_ticket: Optional[dict[str, object]] = None) -> PortalOrderResponse:
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
        activationError=str(order.activation_error or "").strip(),
        activationErrorAt=_format_datetime(order.activation_error_at),
        supportTicket=support_ticket,
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


def _serialize_billing_guide(db: Session, user: V2User) -> PortalBillingGuideResponse:
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
        displayPlanName=str(raw.get("display_plan_name") or _plan_name(_plan_price(db, user))),
        displayMonthlyPrice=round(float(raw.get("display_monthly_price") or _plan_price(db, user)), 2),
        displayResourceQuota=int(raw.get("display_resource_quota") or _calculate_daily_limit_from_price(_plan_price(db, user))),
        displayResourceSummary=str(raw.get("display_resource_summary") or ""),
        anchorReason=str(raw.get("anchor_reason") or "current"),
        allowedActions=[str(item) for item in raw.get("allowed_actions", []) if str(item).strip()],
        upgradeChargeMonths=int(raw.get("upgrade_charge_months") or _resolve_upgrade_charge_months(user)),
        renewOption=_serialize_guide_option(raw.get("renew_option") or {}),
        upgradeOption=_serialize_guide_option(raw.get("upgrade_option") or {}),
        addonOption=_serialize_addon_guide(raw.get("addon_option") or {}),
        addonEnabled=bool(raw.get("addon_enabled", False)),
        addonResourceBalance=int(raw.get("addon_resource_balance") or 0),
        addonResourceTotal=int(raw.get("addon_resource_total") or 0),
        addonExpirationUtc=raw.get("addon_expiration_utc"),
        guideToken=str(raw.get("guide_token") or ""),
    )


def _build_plan_catalog(db: Session, user: V2User) -> list[PortalBillingPlanResponse]:
    current_price = _plan_price(db, user)
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
        months = _resolve_upgrade_charge_months(user)

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
    order.amount = donation_amount
    order.status = "pending"
    order.updated_at = _utc_now_naive()
    set_payment_order_snapshot(
        order,
        action=action,
        plan_price=plan_price,
        months=months,
        expected_amount=donation_amount,
        paid_amount=donation_amount,
        addon_resource_amount=addon_resource_amount,
    )
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
        legacy_email=getattr(legacy_user, "email", None),
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

    _merge_legacy_subscription_value(db, target_user, legacy_user)

    legacy_orders = (
        db.query(LegacyPaymentOrder)
        .filter(LegacyPaymentOrder.username == legacy_user.username)
        .order_by(LegacyPaymentOrder.created_at.asc(), LegacyPaymentOrder.id.asc())
        .all()
    )
    for legacy_order in legacy_orders:
        existing = None
        if legacy_order.out_trade_no:
            existing = db.query(PaymentOrder).filter(PaymentOrder.out_trade_no == legacy_order.out_trade_no).first()
        if existing is None and legacy_order.order_no:
            existing = db.query(PaymentOrder).filter(PaymentOrder.order_no == legacy_order.order_no).first()
        if existing is None and legacy_order.custom_id:
            existing = db.query(PaymentOrder).filter(PaymentOrder.custom_id_digest == payment_lookup_digest(legacy_order.custom_id)).first()
        if existing is not None:
            continue

        cloned_order = PaymentOrder(
            order_no=legacy_order.order_no,
            out_trade_no=legacy_order.out_trade_no,
            username=target_user.username,
            user_uuid=target_user.user_uuid,
            email=target_user.email,
            action=legacy_order.action,
            amount=legacy_order.amount,
            status=legacy_order.status,
            activated_at=legacy_order.activated_at,
            activation_source=legacy_order.activation_source,
            activation_error=legacy_order.activation_error,
            activation_error_at=legacy_order.activation_error_at,
            raw_payload=legacy_order.raw_payload,
            created_at=legacy_order.created_at,
            updated_at=legacy_order.updated_at,
        )
        set_payment_order_custom_id(cloned_order, legacy_order.custom_id)
        set_payment_order_remark(cloned_order, legacy_order.remark)
        legacy_payload = _extract_legacy_order_payload(legacy_order)
        legacy_action = str(legacy_payload.get("action") or legacy_order.action or "").strip().lower() or "renew"
        legacy_months = int(legacy_payload.get("months") or 1)
        legacy_plan_price = _safe_parse_amount(legacy_payload.get("plan_price"))
        if legacy_plan_price is None and legacy_order.amount is not None:
            legacy_plan_price = round(float(legacy_order.amount) / max(1, legacy_months), 2)
        legacy_expected_amount = _safe_parse_amount(legacy_payload.get("requested_amount"))
        if legacy_expected_amount is None:
            legacy_expected_amount = _safe_parse_amount(legacy_order.amount)
        set_payment_order_snapshot(
            cloned_order,
            action=legacy_action,
            plan_price=legacy_plan_price,
            months=legacy_months,
            expected_amount=legacy_expected_amount,
            paid_amount=_safe_parse_amount(legacy_order.amount),
            addon_resource_amount=int(legacy_payload.get("addon_resource_amount") or 0),
        )
        db.add(cloned_order)
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

    target_user.legacy_user_id = legacy_user.id
    rebuild_user_entitlement_state_from_legacy_fields(db, target_user)
    legacy_user.is_active = False
    db.flush()
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
    if payload.username is not None:
        next_username = payload.username.strip()
        if not next_username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username must not be empty")
        duplicate = (
            db.query(V2User)
            .filter(V2User.username == next_username, V2User.user_uuid != current_user.user_uuid)
            .first()
        )
        if duplicate is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
        current_user.username = next_username

    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user, db)


@router.get("/content/announcements", response_model=list[PortalAnnouncementResponse])
async def portal_announcements(
    db: Session = Depends(get_db),
    _: V2User = Depends(get_current_v2_user),
):
    return list_published_announcements_payload(db)


@router.get("/content/faqs", response_model=list[PortalFaqItemResponse])
async def portal_faqs(
    db: Session = Depends(get_db),
    _: V2User = Depends(get_current_v2_user),
):
    return list_published_faqs_payload(db)


@router.get("/account/entitlements", response_model=PortalEntitlementResponse)
async def portal_entitlements(db: Session = Depends(get_db), current_user: V2User = Depends(get_current_v2_user)):
    return _serialize_entitlements(current_user, db)


@router.get("/account/usage")
async def portal_usage(
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
    start_date: Optional[date] = Query(default=None, description="Start date in YYYY-MM-DD format"),
    end_date: Optional[date] = Query(default=None, description="End date in YYYY-MM-DD format"),
):
    now = datetime.now(timezone.utc)
    today_utc = now.date()

    if start_date is None and end_date is None:
        query_end_date = today_utc
        query_start_date = today_utc - timedelta(days=6)
    elif start_date is None:
        query_end_date = end_date
        query_start_date = end_date
    elif end_date is None:
        query_start_date = start_date
        query_end_date = start_date
    else:
        query_start_date = start_date
        query_end_date = end_date

    if query_start_date > query_end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_date_range", "message": "start_date cannot be later than end_date"},
        )

    if (query_end_date - query_start_date).days > 365:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "date_range_too_large", "message": "date range cannot exceed 366 days"},
        )

    usage_subject = resolve_usage_subject_for_user(current_user)
    if usage_subject is None:
        usage_rows = []
    else:
        subject_type, subject_key = usage_subject
        usage_rows = list_subject_daily_usage_rows(
            db,
            subject_type=subject_type,
            subject_key=subject_key,
            start_date=query_start_date,
            end_date=query_end_date,
        )

    daily_limit = get_daily_subscription_limit(current_user)
    quota_snapshot = build_quota_snapshot(db, current_user)

    daily_map = {}
    for row in usage_rows:
        day_text = row.usage_date.strftime("%Y-%m-%d") if hasattr(row.usage_date, "strftime") else str(row.usage_date)
        day_subscription_resource_cost = int((row.daily_resource_used or 0) or 0)
        daily_map[day_text] = {
            "date": day_text,
            "day": day_text,
            "daily_success_count": int((row.success_count or 0) or 0),
            "daily_failed_count": int((row.failed_count or 0) or 0),
            "daily_rate_limited_count": int((row.rate_limited_count or 0) or 0),
            "daily_resource_cost": int((row.resource_cost_sum or 0) or 0),
            "daily_subscription_resource_cost": day_subscription_resource_cost,
            "daily_addon_resource_cost": int((row.addon_resource_used or 0) or 0),
            "daily_deducted_request_count": int((row.deducted_request_count or 0) or 0),
            "daily_total_count": int((row.total_count or 0) or 0),
            "daily_limit": daily_limit,
            "remaining_daily": None if daily_limit is None else max(0, daily_limit - day_subscription_resource_cost),
        }

    daily_stats = []
    cursor = query_start_date
    while cursor <= query_end_date:
        cursor_text = cursor.strftime("%Y-%m-%d")
        daily_stats.append(
            daily_map.get(
                cursor_text,
                {
                    "date": cursor_text,
                    "day": cursor_text,
                    "daily_success_count": 0,
                    "daily_failed_count": 0,
                    "daily_rate_limited_count": 0,
                    "daily_resource_cost": 0,
                    "daily_subscription_resource_cost": 0,
                    "daily_addon_resource_cost": 0,
                    "daily_deducted_request_count": 0,
                    "daily_total_count": 0,
                    "daily_limit": daily_limit,
                    "remaining_daily": daily_limit,
                },
            )
        )
        cursor += timedelta(days=1)

    success_count = sum(int(item["daily_success_count"] or 0) for item in daily_stats)
    failed_count = sum(int(item["daily_failed_count"] or 0) for item in daily_stats)
    rate_limited_count = sum(int(item["daily_rate_limited_count"] or 0) for item in daily_stats)
    resource_cost_sum = sum(int(item["daily_resource_cost"] or 0) for item in daily_stats)
    daily_resource_cost_sum = sum(int(item["daily_subscription_resource_cost"] or 0) for item in daily_stats)
    addon_resource_cost_sum = sum(int(item["daily_addon_resource_cost"] or 0) for item in daily_stats)
    deducted_request_count = sum(int(item["daily_deducted_request_count"] or 0) for item in daily_stats)
    total_count = sum(int(item["daily_total_count"] or 0) for item in daily_stats)

    if usage_subject is None:
        today_success_count = 0
        today_total_count = 0
    else:
        subject_type, subject_key = usage_subject
        today_usage_row = get_subject_daily_usage_row(
            db,
            subject_type=subject_type,
            subject_key=subject_key,
            usage_date=today_utc,
        )
        today_success_count = int((getattr(today_usage_row, "success_count", 0) or 0))
        today_total_count = int((getattr(today_usage_row, "total_count", 0) or 0))

    end_date_text = query_end_date.strftime("%Y-%m-%d")
    end_day_subscription_resource_cost = int(daily_map.get(end_date_text, {}).get("daily_subscription_resource_cost", 0) or 0)
    remaining_daily = None if daily_limit is None else max(0, daily_limit - end_day_subscription_resource_cost)
    current_plan_price = _plan_price(db, current_user)
    subscription_state, _ = ensure_user_entitlement_state(db, current_user)

    expires_at = current_user.expiration_date
    expiration_utc = None
    remaining_seconds = None
    is_expired = False
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)
        expiration_utc = expires_at.isoformat().replace("+00:00", "Z")
        remaining_seconds = int((expires_at - now).total_seconds())
        is_expired = remaining_seconds <= 0

    return {
        "username": current_user.username,
        "user_uuid": (current_user.user_uuid or "").strip(),
        "is_test_user": bool(getattr(current_user, "is_default_test_user", False)),
        "is_active": bool(current_user.is_active),
        "is_expired": is_expired,
        "plan_name": _plan_name(current_plan_price),
        "plan_price": current_plan_price,
        "plan_ref": subscription_state.plan_ref,
        "pricing_mode": subscription_state.pricing_mode,
        "expiration_utc": expiration_utc,
        "remaining_seconds": remaining_seconds,
        "limit_rule": current_user.limit_rule or "",
        "daily_limit": daily_limit,
        "remaining_daily": remaining_daily,
        "daily_resource_limit": daily_limit,
        "remaining_resource": remaining_daily,
        "resource_used": resource_cost_sum,
        "subscription_resource_used": daily_resource_cost_sum,
        "addon_resource_used": addon_resource_cost_sum,
        "resource_deducted_request_count": deducted_request_count,
        "daily_success_count": success_count,
        "daily_failed_count": failed_count,
        "daily_rate_limited_count": rate_limited_count,
        "daily_total_count": total_count,
        "today_success_count": today_success_count,
        "today_total_count": today_total_count,
        "today_daily_resource_used": quota_snapshot.today_daily_used,
        "today_remaining_daily": quota_snapshot.remaining_daily,
        "addon_resource_balance": quota_snapshot.addon_resource_balance,
        "addon_resource_total": quota_snapshot.addon_resource_total,
        "addon_expiration_utc": quota_snapshot.addon_expiration_utc,
        "query_start_date": query_start_date.strftime("%Y-%m-%d"),
        "query_end_date": query_end_date.strftime("%Y-%m-%d"),
        "daily_stats": daily_stats,
    }


@router.get("/account/orders", response_model=list[PortalOrderResponse])
async def portal_orders(db: Session = Depends(get_db), current_user: V2User = Depends(get_current_v2_user)):
    orders = (
        db.query(PaymentOrder)
        .filter(or_(PaymentOrder.user_uuid == current_user.user_uuid, PaymentOrder.username == current_user.username))
        .order_by(PaymentOrder.created_at.desc(), PaymentOrder.id.desc())
        .all()
    )
    ticket_map = support_ticket_service.get_ticket_summary_for_orders(db, [int(order.id) for order in orders], current_user)
    return [_serialize_order(order, ticket_map.get(int(order.id))) for order in orders]


@router.get("/account/tickets", response_model=list[PortalSupportTicketResponse])
async def portal_support_tickets(
    include_closed: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    return support_ticket_service.list_user_tickets(db, current_user, include_closed=include_closed)


@router.post("/account/tickets", response_model=PortalSupportTicketResponse)
async def portal_create_support_ticket(
    request: Request,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    payload, attachments = await _read_support_ticket_payload(request)
    try:
        return await support_ticket_service.create_general_support_ticket(
            db,
            current_user=current_user,
            subject=payload.subject,
            message=payload.message,
            category=payload.category,
            attachments=attachments,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/account/tickets/{ticket_id}/messages", response_model=PortalSupportTicketResponse)
async def portal_add_support_ticket_message(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    payload, attachments = await _read_support_ticket_payload(request)
    try:
        return await support_ticket_service.add_user_ticket_message(
            db,
            ticket_id=ticket_id,
            current_user=current_user,
            message=payload.message,
            attachments=attachments,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Ticket not found":
            status_code = status.HTTP_404_NOT_FOUND
        elif detail == "Ticket is closed":
            status_code = status.HTTP_409_CONFLICT
        else:
            status_code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/account/tickets/{ticket_id}/attachments/{attachment_id}")
async def portal_support_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    try:
        payload = support_ticket_service.get_user_ticket_attachment_file(
            db,
            ticket_id=ticket_id,
            attachment_id=attachment_id,
            current_user=current_user,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FileResponse(
        payload["path"],
        media_type=payload["content_type"],
        filename=payload["filename"],
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/account/tickets/{ticket_id}", response_model=PortalSupportTicketResponse)
async def portal_support_ticket_detail(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    try:
        return support_ticket_service.get_user_ticket(db, ticket_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/account/orders/{order_id}/support-ticket", response_model=PortalSupportTicketResponse)
async def portal_create_order_support_ticket(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    payload, attachments = await _read_support_ticket_payload(request)
    try:
        return await support_ticket_service.create_order_support_ticket(
            db,
            order_id=order_id,
            current_user=current_user,
            message=payload.message,
            attachments=attachments,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Order not found":
            status_code = status.HTTP_404_NOT_FOUND
        elif detail == "Only activation failed orders can create a support ticket":
            status_code = status.HTTP_409_CONFLICT
        else:
            status_code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


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
    enforce_auth_attempt_rate_limit(
        request,
        scope_prefix="portal_legacy_login",
        ip_limit=settings.LEGACY_LOGIN_IP_LIMIT,
        identity=payload.legacyUsername,
        identity_limit=settings.LEGACY_LOGIN_IDENTITY_LIMIT,
    )
    legacy_username = payload.legacyUsername.strip()
    user = db.query(User).filter(User.username == legacy_username).first()
    if user is None or authenticate_user(db, legacy_username, payload.legacyPassword, allow_legacy_username=True) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid legacy credentials")
    if not bool(getattr(user, "is_active", True)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Legacy account is disabled")
    if bool(getattr(user, "is_default_test_user", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Default test user cannot be migrated")
    if has_bound_email(getattr(user, "email", None)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Legacy account already upgraded, please use email login")

    return PortalLegacyLoginResponse(
        migrationRequired=True,
        emailBound=bool(getattr(user, "email", None)),
        emailVerified=bool(getattr(user, "email_verified", False)),
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
    enforce_auth_attempt_rate_limit(
        request,
        scope_prefix="portal_legacy_link",
        ip_limit=settings.LEGACY_LOGIN_IP_LIMIT,
        identity=payload.legacyUsername,
        identity_limit=settings.LEGACY_LOGIN_IDENTITY_LIMIT,
    )
    legacy_username = payload.legacyUsername.strip()
    legacy_user = db.query(User).filter(User.username == legacy_username).first()
    if legacy_user is None or authenticate_user(db, legacy_username, payload.legacyPassword, allow_legacy_username=True) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid legacy credentials")
    if not bool(getattr(legacy_user, "is_active", True)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Legacy account is disabled")
    if legacy_user.id == int(getattr(current_user, "legacy_user_id", 0) or 0):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current account is already this legacy account")
    if bool(getattr(legacy_user, "is_default_test_user", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Default test user cannot be linked")

    legacy_email = getattr(legacy_user, "email", None)
    legacy_email_verified = bool(getattr(legacy_user, "email_verified", False))
    if current_user.email_verified and legacy_email_verified and current_user.email and legacy_email:
        if normalize_email(current_user.email) != normalize_email(legacy_email):
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
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    return _build_plan_catalog(db, current_user)


@router.get("/billing/guide", response_model=PortalBillingGuideResponse)
async def portal_billing_guide(
    _: None = Depends(_ensure_v2_billing_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    return _serialize_billing_guide(db, current_user)


@router.post("/billing/order-intents", response_model=PortalOrderIntentResponse)
async def portal_create_order_intent(
    payload: PortalCreateOrderIntentRequest,
    request: Request,
    _: None = Depends(_ensure_v2_billing_enabled),
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    if _migration_required(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Finish account migration before billing")
    request.state.audit_payload = _build_order_intent_audit_payload(current_user, payload)
    try:
        action, months, plan_price, donation_amount = _resolve_checkout_request(current_user, payload)
        order = _create_order_intent(db, current_user, action, months, plan_price, donation_amount)
        request.state.audit_payload = _build_order_intent_audit_payload(
            current_user,
            payload,
            result={
                "order_id": str(order.id),
                "status": _payment_status(order.status),
                "action": action,
                "months": months,
                "plan_price": round(float(plan_price or 0), 2),
                "donation_amount": round(float(donation_amount or 0), 2),
                "pay_url_generated": bool(_build_pay_url(order)),
            },
        )
        return PortalOrderIntentResponse(
            id=str(order.id),
            status=_payment_status(order.status),
            payUrl=_build_pay_url(order),
        )
    except HTTPException as exc:
        request.state.audit_payload = _build_order_intent_audit_payload(
            current_user,
            payload,
            error_detail=str(exc.detail),
        )
        raise


@router.post("/user-center/afdian/webhook")
async def portal_afdian_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook request body is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook request body is invalid")

    background_tasks.add_task(
        _process_afdian_webhook_payload,
        payload,
        request.headers.get(AFDIAN_WEBHOOK_FORWARD_HEADER) == "1",
    )
    return AFDIAN_WEBHOOK_OK


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
