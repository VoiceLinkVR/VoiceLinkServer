from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from core.config import settings
from core.plan_catalog import (
    SUBSCRIPTION_MODE_FIXED_TIERS,
    SUBSCRIPTION_MODE_LADDER,
    build_limit_rule,
    build_tier_for_price,
    find_paid_tier_by_price,
    get_free_plan,
    get_ladder_billing_config,
    get_paid_tiers,
    get_subscription_mode,
    load_plan_catalog,
    resolve_plan_from_rule,
)
from db.models import PaymentOrder, User, UserAddonWallet, UserSubscriptionState, V2User

ORDER_KIND_SUBSCRIPTION = "subscription"
ORDER_KIND_ADDON = "addon"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_naive() -> datetime:
    return _now_utc().replace(tzinfo=None)


def _normalize_expiration_utc(expiration_date: Optional[datetime]) -> Optional[datetime]:
    if expiration_date is None:
        return None
    if expiration_date.tzinfo is None:
        return expiration_date.replace(tzinfo=timezone.utc)
    return expiration_date.astimezone(timezone.utc)


def _normalize_rate_limit_rule(limit_rule: Optional[str]) -> str:
    parts: list[str] = []
    for part in (segment.strip() for segment in str(limit_rule or "").split(";") if segment.strip()):
        lowered = part.lower()
        if lowered.startswith("plan_id="):
            continue
        if lowered.endswith("/day"):
            continue
        parts.append(part)
    return ";".join(parts)


def _default_rate_limit_rule() -> str:
    free_plan = get_free_plan()
    return (
        _normalize_rate_limit_rule(free_plan.get("rate_limit"))
        or _normalize_rate_limit_rule(settings.FREE_PLAN_LIMIT_RULE)
        or "8/minute"
    )


def _resolve_subscription_rate_limit_rule(
    *,
    explicit_rate_limit_rule: Optional[str],
    catalog_rate_limit_rule: Optional[str],
    plan_price: float,
) -> str:
    explicit_normalized = _normalize_rate_limit_rule(explicit_rate_limit_rule)
    catalog_normalized = _normalize_rate_limit_rule(catalog_rate_limit_rule)
    is_paid_plan = round(float(plan_price or 0), 2) > 0

    if is_paid_plan:
        if explicit_rate_limit_rule is not None:
            return explicit_normalized
        return catalog_normalized

    return explicit_normalized or catalog_normalized or _default_rate_limit_rule()


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _current_pricing_mode() -> str:
    return get_subscription_mode(load_plan_catalog())


def _pick_fixed_tier(plan_price: float, daily_quota: int | None = None) -> dict[str, Any]:
    tiers = get_paid_tiers()
    if daily_quota is not None:
        matched_by_quota = next((tier for tier in tiers if int(tier["daily_quota"]) == int(daily_quota)), None)
        if matched_by_quota is not None:
            return dict(matched_by_quota)

    exact_by_price = find_paid_tier_by_price(plan_price)
    if exact_by_price is not None:
        return dict(exact_by_price)

    if daily_quota is not None:
        return dict(min(tiers, key=lambda tier: abs(int(tier["daily_quota"]) - int(daily_quota))))

    return dict(min(tiers, key=lambda tier: abs(round(float(tier["monthly_price"]), 2) - round(float(plan_price), 2))))


def _pick_ladder_tier(plan_price: float, daily_quota: int | None = None) -> dict[str, Any]:
    catalog = load_plan_catalog()
    ladder = get_ladder_billing_config(catalog)
    base_price = round(float(ladder["base_price"]), 2)
    min_price = round(float(ladder["min_monthly_price"]), 2)
    max_price = round(float(ladder["max_monthly_price"]), 2)
    price_step = max(0.01, round(float(ladder["price_step"]), 2))
    base_daily_quota = int(ladder["base_daily_quota"])
    daily_quota_step = max(1, int(ladder["daily_quota_step"]))

    normalized_price = round(float(plan_price or 0), 2)
    if normalized_price <= 0 and daily_quota is not None and daily_quota > 0:
        units = round((int(daily_quota) - base_daily_quota) / daily_quota_step)
        normalized_price = round(base_price + units * price_step, 2)

    normalized_price = min(max(normalized_price or min_price, min_price), max_price)
    units = round((normalized_price - base_price) / price_step)
    normalized_price = round(base_price + units * price_step, 2)
    normalized_price = min(max(normalized_price, min_price), max_price)
    return build_tier_for_price(normalized_price, catalog=catalog)


def build_subscription_snapshot_for_existing_values(
    plan_price: float,
    *,
    daily_quota: int | None = None,
    rate_limit_rule: Optional[str] = None,
    pricing_mode: Optional[str] = None,
) -> dict[str, Any]:
    normalized_price = round(float(plan_price or 0), 2)
    catalog = load_plan_catalog()
    catalog_version = str(catalog.get("version") or 1)
    free_plan = get_free_plan()
    free_quota = int(free_plan["daily_quota"])
    if normalized_price <= 0:
        if daily_quota is None or int(daily_quota or 0) <= free_quota:
            snapshot = build_subscription_snapshot_for_price(0, pricing_mode="free")
            if rate_limit_rule is not None:
                snapshot["rate_limit_rule"] = _normalize_rate_limit_rule(rate_limit_rule)
            return snapshot

    resolved_mode = pricing_mode or get_subscription_mode(catalog)
    if resolved_mode == SUBSCRIPTION_MODE_LADDER:
        tier = _pick_ladder_tier(normalized_price, daily_quota=daily_quota)
    else:
        tier = _pick_fixed_tier(normalized_price, daily_quota=daily_quota)

    return {
        "plan_ref": str(tier["id"]),
        "pricing_mode": resolved_mode,
        "plan_price": round(float(tier["monthly_price"]), 2),
        "daily_quota": int(tier["daily_quota"]),
        "rate_limit_rule": _resolve_subscription_rate_limit_rule(
            explicit_rate_limit_rule=rate_limit_rule,
            catalog_rate_limit_rule=tier.get("rate_limit"),
            plan_price=float(tier["monthly_price"]),
        ),
        "catalog_version": catalog_version,
    }


def build_subscription_snapshot_for_price(plan_price: float, *, pricing_mode: Optional[str] = None) -> dict[str, Any]:
    normalized_price = round(float(plan_price or 0), 2)
    catalog = load_plan_catalog()
    catalog_version = str(catalog.get("version") or 1)
    if normalized_price <= 0:
        free_plan = get_free_plan()
        return {
            "plan_ref": str(free_plan["id"]),
            "pricing_mode": pricing_mode or "free",
            "plan_price": 0.0,
            "daily_quota": int(free_plan["daily_quota"]),
            "rate_limit_rule": _resolve_subscription_rate_limit_rule(
                explicit_rate_limit_rule=None,
                catalog_rate_limit_rule=free_plan.get("rate_limit"),
                plan_price=0.0,
            ),
            "catalog_version": catalog_version,
        }

    tier = build_tier_for_price(normalized_price, catalog=catalog)
    return {
        "plan_ref": str(tier["id"]),
        "pricing_mode": pricing_mode or get_subscription_mode(catalog),
        "plan_price": normalized_price,
        "daily_quota": int(tier["daily_quota"]),
        "rate_limit_rule": _resolve_subscription_rate_limit_rule(
            explicit_rate_limit_rule=None,
            catalog_rate_limit_rule=tier.get("rate_limit"),
            plan_price=normalized_price,
        ),
        "catalog_version": catalog_version,
    }


def build_subscription_snapshot_from_limit_rule(
    limit_rule: Optional[str],
    expiration_date: Optional[datetime],
    *,
    pricing_mode: Optional[str] = None,
) -> dict[str, Any]:
    plan = resolve_plan_from_rule(limit_rule, expiration_date)
    return build_subscription_snapshot_for_existing_values(
        float(plan.get("monthly_price") or 0),
        daily_quota=int(plan.get("daily_quota") or 0) or None,
        rate_limit_rule=None,
        pricing_mode=pricing_mode or _current_pricing_mode(),
    )


def build_user_limit_rule_from_state(
    subscription_state: UserSubscriptionState,
    *,
    honor_empty_rate_limit_rule: bool = False,
) -> str:
    if honor_empty_rate_limit_rule:
        base_rule = subscription_state.rate_limit_rule or ""
    else:
        base_rule = subscription_state.rate_limit_rule or (_default_rate_limit_rule() if float(subscription_state.plan_price or 0) <= 0 else "")
    return build_limit_rule(
        base_rule,
        subscription_state.plan_ref or str(get_free_plan()["id"]),
        int(subscription_state.daily_quota or get_free_plan()["daily_quota"]),
    )


def _snapshot_source(subscription_state: UserSubscriptionState) -> str:
    raw_snapshot = getattr(subscription_state, "snapshot_json", None)
    if not raw_snapshot:
        return ""
    try:
        payload = json.loads(raw_snapshot)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("source") or "").strip().lower()


def _ensure_subscription_state_row(db: Session, user: User | V2User) -> UserSubscriptionState:
    user_uuid = (getattr(user, "user_uuid", None) or "").strip()
    record = db.query(UserSubscriptionState).filter(UserSubscriptionState.user_uuid == user_uuid).first()
    if record is None:
        record = UserSubscriptionState(
            user_uuid=user_uuid,
            username=getattr(user, "username", None),
            identity_version=int(getattr(user, "identity_version", 1) or 1),
            plan_ref=str(get_free_plan()["id"]),
            pricing_mode="free",
            plan_price=0,
            daily_quota=int(get_free_plan()["daily_quota"]),
            rate_limit_rule=_default_rate_limit_rule(),
            status="free",
            created_at=_utcnow_naive(),
            updated_at=_utcnow_naive(),
        )
        db.add(record)
        db.flush()
    return record


def _ensure_addon_wallet_row(db: Session, user: User | V2User) -> UserAddonWallet:
    user_uuid = (getattr(user, "user_uuid", None) or "").strip()
    record = db.query(UserAddonWallet).filter(UserAddonWallet.user_uuid == user_uuid).first()
    if record is None:
        record = UserAddonWallet(
            user_uuid=user_uuid,
            username=getattr(user, "username", None),
            identity_version=int(getattr(user, "identity_version", 1) or 1),
            balance=0,
            total_purchased=0,
            created_at=_utcnow_naive(),
            updated_at=_utcnow_naive(),
        )
        db.add(record)
        db.flush()
    return record


def sync_user_cache_from_entitlements(
    user: User | V2User,
    subscription_state: UserSubscriptionState,
    addon_wallet: UserAddonWallet,
) -> None:
    user.limit_rule = build_user_limit_rule_from_state(
        subscription_state,
        honor_empty_rate_limit_rule=_snapshot_source(subscription_state) == "admin_manual_correction",
    )
    user.expiration_date = subscription_state.expires_at
    user.is_active = subscription_state.status != "inactive"
    user.boost_limit_rule = None
    user.boost_expiration_date = None
    user.addon_resource_balance = int(addon_wallet.balance or 0)
    user.addon_resource_total = int(addon_wallet.total_purchased or 0)
    user.addon_expiration_date = addon_wallet.expires_at


def ensure_user_entitlement_state(db: Session, user: User | V2User) -> tuple[UserSubscriptionState, UserAddonWallet]:
    subscription_state = db.query(UserSubscriptionState).filter(UserSubscriptionState.user_uuid == user.user_uuid).first()
    addon_wallet = db.query(UserAddonWallet).filter(UserAddonWallet.user_uuid == user.user_uuid).first()
    if subscription_state is None or addon_wallet is None:
        return rebuild_user_entitlement_state_from_legacy_fields(db, user)

    subscription_state.username = getattr(user, "username", None)
    subscription_state.identity_version = int(getattr(user, "identity_version", 1) or 1)
    subscription_state.updated_at = _utcnow_naive()
    addon_wallet.username = getattr(user, "username", None)
    addon_wallet.identity_version = int(getattr(user, "identity_version", 1) or 1)
    addon_wallet.updated_at = _utcnow_naive()
    sync_user_cache_from_entitlements(user, subscription_state, addon_wallet)
    return subscription_state, addon_wallet


def rebuild_user_entitlement_state_from_legacy_fields(
    db: Session,
    user: User | V2User,
    *,
    pricing_mode: Optional[str] = None,
) -> tuple[UserSubscriptionState, UserAddonWallet]:
    subscription_state = _ensure_subscription_state_row(db, user)
    addon_wallet = _ensure_addon_wallet_row(db, user)

    now_utc = _now_utc()
    expiration_utc = _normalize_expiration_utc(getattr(user, "expiration_date", None))
    is_expired = expiration_utc is not None and expiration_utc <= now_utc
    snapshot = (
        build_subscription_snapshot_for_price(0, pricing_mode=pricing_mode)
        if is_expired
        else build_subscription_snapshot_from_limit_rule(
            getattr(user, "limit_rule", None),
            getattr(user, "expiration_date", None),
            pricing_mode=pricing_mode or _current_pricing_mode(),
        )
    )

    subscription_state.username = getattr(user, "username", None)
    subscription_state.identity_version = int(getattr(user, "identity_version", 1) or 1)
    subscription_state.plan_ref = str(snapshot["plan_ref"])
    subscription_state.pricing_mode = str(snapshot["pricing_mode"])
    subscription_state.plan_price = round(float(snapshot["plan_price"]), 2)
    subscription_state.daily_quota = int(snapshot["daily_quota"])
    subscription_state.rate_limit_rule = str(snapshot["rate_limit_rule"])
    subscription_state.status = "inactive" if not bool(getattr(user, "is_active", True)) else ("free" if subscription_state.plan_price <= 0 else "active")
    subscription_state.expires_at = None if subscription_state.plan_price <= 0 else getattr(user, "expiration_date", None)
    subscription_state.snapshot_json = _json_dumps(snapshot)
    subscription_state.updated_at = _utcnow_naive()

    addon_expiration_utc = _normalize_expiration_utc(getattr(user, "addon_expiration_date", None))
    addon_active = (
        int(getattr(user, "addon_resource_balance", 0) or 0) > 0
        and addon_expiration_utc is not None
        and addon_expiration_utc > now_utc
        and subscription_state.expires_at is not None
    )
    addon_wallet.username = getattr(user, "username", None)
    addon_wallet.identity_version = int(getattr(user, "identity_version", 1) or 1)
    addon_wallet.balance = int(getattr(user, "addon_resource_balance", 0) or 0) if addon_active else 0
    addon_wallet.total_purchased = max(
        int(getattr(user, "addon_resource_total", 0) or 0),
        addon_wallet.balance,
    )
    addon_wallet.expires_at = getattr(user, "addon_expiration_date", None) if addon_active else None
    addon_wallet.snapshot_json = _json_dumps(
        {
            "balance": addon_wallet.balance,
            "total_purchased": addon_wallet.total_purchased,
            "expires_at": addon_wallet.expires_at,
        }
    )
    addon_wallet.updated_at = _utcnow_naive()

    sync_user_cache_from_entitlements(user, subscription_state, addon_wallet)
    return subscription_state, addon_wallet


def update_user_subscription_state(
    db: Session,
    user: User | V2User,
    *,
    plan_price: float,
    expires_at: Optional[datetime],
    source_order_id: Optional[int] = None,
    pricing_mode: Optional[str] = None,
    status: str = "active",
    plan_ref: Optional[str] = None,
    daily_quota: Optional[int] = None,
    rate_limit_rule: Optional[str] = None,
    catalog_version: Optional[str] = None,
) -> tuple[UserSubscriptionState, UserAddonWallet]:
    subscription_state, addon_wallet = ensure_user_entitlement_state(db, user)
    if plan_ref or daily_quota or rate_limit_rule or catalog_version:
        snapshot = build_subscription_snapshot_for_existing_values(
            plan_price,
            daily_quota=daily_quota,
            rate_limit_rule=rate_limit_rule,
            pricing_mode=pricing_mode or _current_pricing_mode(),
        )
    else:
        snapshot = build_subscription_snapshot_for_price(plan_price, pricing_mode=pricing_mode)
    subscription_state.username = getattr(user, "username", None)
    subscription_state.identity_version = int(getattr(user, "identity_version", 1) or 1)
    subscription_state.plan_ref = str(snapshot["plan_ref"])
    subscription_state.pricing_mode = str(snapshot["pricing_mode"])
    subscription_state.plan_price = round(float(snapshot["plan_price"]), 2)
    subscription_state.daily_quota = int(snapshot["daily_quota"])
    subscription_state.rate_limit_rule = str(snapshot["rate_limit_rule"])
    subscription_state.status = status if subscription_state.plan_price > 0 else "free"
    subscription_state.expires_at = expires_at if subscription_state.plan_price > 0 else None
    subscription_state.source_order_id = source_order_id
    subscription_state.snapshot_json = _json_dumps(
        {
            **snapshot,
            "expires_at": expires_at,
            "source_order_id": source_order_id,
            "status": subscription_state.status,
        }
    )
    subscription_state.updated_at = _utcnow_naive()

    if int(addon_wallet.balance or 0) > 0:
        addon_wallet.expires_at = expires_at
        addon_wallet.updated_at = _utcnow_naive()
        addon_wallet.snapshot_json = _json_dumps(
            {
                "balance": int(addon_wallet.balance or 0),
                "total_purchased": int(addon_wallet.total_purchased or 0),
                "expires_at": addon_wallet.expires_at,
                "last_order_id": addon_wallet.last_order_id,
            }
        )

    sync_user_cache_from_entitlements(user, subscription_state, addon_wallet)
    return subscription_state, addon_wallet


def credit_user_addon_wallet(
    db: Session,
    user: User | V2User,
    *,
    addon_resource_amount: int,
    expires_at: Optional[datetime],
    source_order_id: Optional[int] = None,
) -> tuple[UserSubscriptionState, UserAddonWallet]:
    subscription_state, addon_wallet = ensure_user_entitlement_state(db, user)
    addon_wallet.username = getattr(user, "username", None)
    addon_wallet.identity_version = int(getattr(user, "identity_version", 1) or 1)
    addon_wallet.balance = int(addon_wallet.balance or 0) + max(0, int(addon_resource_amount or 0))
    addon_wallet.total_purchased = int(addon_wallet.total_purchased or 0) + max(0, int(addon_resource_amount or 0))
    addon_wallet.expires_at = expires_at
    addon_wallet.last_order_id = source_order_id
    addon_wallet.snapshot_json = _json_dumps(
        {
            "balance": addon_wallet.balance,
            "total_purchased": addon_wallet.total_purchased,
            "expires_at": addon_wallet.expires_at,
            "last_order_id": addon_wallet.last_order_id,
        }
    )
    addon_wallet.updated_at = _utcnow_naive()
    sync_user_cache_from_entitlements(user, subscription_state, addon_wallet)
    return subscription_state, addon_wallet


def reset_user_entitlements_to_free(
    db: Session,
    user: User | V2User,
    *,
    source_order_id: Optional[int] = None,
    clear_addon: bool = True,
) -> tuple[UserSubscriptionState, UserAddonWallet]:
    subscription_state, addon_wallet = ensure_user_entitlement_state(db, user)
    snapshot = build_subscription_snapshot_for_price(0, pricing_mode="free")
    subscription_state.plan_ref = str(snapshot["plan_ref"])
    subscription_state.pricing_mode = str(snapshot["pricing_mode"])
    subscription_state.plan_price = 0
    subscription_state.daily_quota = int(snapshot["daily_quota"])
    subscription_state.rate_limit_rule = str(snapshot["rate_limit_rule"] or _default_rate_limit_rule())
    subscription_state.status = "free"
    subscription_state.expires_at = None
    subscription_state.source_order_id = source_order_id
    subscription_state.snapshot_json = _json_dumps(
        {
            **snapshot,
            "expires_at": None,
            "source_order_id": source_order_id,
            "status": "free",
        }
    )
    subscription_state.updated_at = _utcnow_naive()

    if clear_addon:
        addon_wallet.balance = 0
        addon_wallet.expires_at = None
        addon_wallet.last_order_id = source_order_id
    addon_wallet.updated_at = _utcnow_naive()
    addon_wallet.snapshot_json = _json_dumps(
        {
            "balance": int(addon_wallet.balance or 0),
            "total_purchased": int(addon_wallet.total_purchased or 0),
            "expires_at": addon_wallet.expires_at,
            "last_order_id": addon_wallet.last_order_id,
        }
    )

    sync_user_cache_from_entitlements(user, subscription_state, addon_wallet)
    return subscription_state, addon_wallet


def set_payment_order_snapshot(
    order: PaymentOrder,
    *,
    action: str,
    plan_price: Optional[float],
    months: Optional[int],
    expected_amount: Optional[float],
    paid_amount: Optional[float],
    addon_resource_amount: int = 0,
    pricing_mode_snapshot: Optional[str] = None,
    plan_ref: Optional[str] = None,
    daily_quota: Optional[int] = None,
    rate_limit_rule: Optional[str] = None,
    catalog_version_snapshot: Optional[str] = None,
) -> None:
    normalized_action = str(action or "").strip().lower()
    plan_price_value = round(float(plan_price or 0), 2) if plan_price is not None else None
    expected_amount_value = round(float(expected_amount or 0), 2) if expected_amount is not None else None
    paid_amount_value = round(float(paid_amount or 0), 2) if paid_amount is not None else None
    months_value = int(months or 1) if months is not None else None
    addon_amount_value = max(0, int(addon_resource_amount or 0))

    snapshot: dict[str, Any] = {
        "action": normalized_action,
        "months": months_value,
        "expected_amount": expected_amount_value,
        "paid_amount": paid_amount_value,
        "addon_resource_amount": addon_amount_value,
    }

    order.action = normalized_action
    order.order_kind = ORDER_KIND_ADDON if normalized_action == "addon" else ORDER_KIND_SUBSCRIPTION
    order.plan_price_snapshot = plan_price_value
    order.months_snapshot = months_value
    order.expected_amount_snapshot = expected_amount_value
    order.paid_amount_snapshot = paid_amount_value
    order.addon_resource_snapshot = addon_amount_value

    if normalized_action == "addon":
        snapshot.update(
            {
                "order_kind": ORDER_KIND_ADDON,
                "pricing_mode_snapshot": pricing_mode_snapshot or ORDER_KIND_ADDON,
            }
        )
        order.pricing_mode_snapshot = pricing_mode_snapshot or ORDER_KIND_ADDON
        order.plan_ref = None
        order.daily_quota_snapshot = None
        order.rate_limit_rule_snapshot = None
        order.catalog_version_snapshot = str(load_plan_catalog().get("version") or 1)
        order.plan_id = None
    else:
        if plan_ref or daily_quota or rate_limit_rule or catalog_version_snapshot:
            entitlement = build_subscription_snapshot_for_existing_values(
                plan_price_value or 0,
                daily_quota=daily_quota,
                rate_limit_rule=rate_limit_rule,
                pricing_mode=pricing_mode_snapshot or _current_pricing_mode(),
            )
        else:
            entitlement = build_subscription_snapshot_for_price(plan_price_value or 0, pricing_mode=pricing_mode_snapshot)
        snapshot.update(
            {
                "order_kind": ORDER_KIND_SUBSCRIPTION,
                "pricing_mode_snapshot": entitlement["pricing_mode"],
                "plan_ref": entitlement["plan_ref"],
                "daily_quota": entitlement["daily_quota"],
                "rate_limit_rule": entitlement["rate_limit_rule"],
                "catalog_version": entitlement["catalog_version"],
            }
        )
        order.pricing_mode_snapshot = str(entitlement["pricing_mode"])
        order.plan_ref = str(entitlement["plan_ref"])
        order.daily_quota_snapshot = int(entitlement["daily_quota"])
        order.rate_limit_rule_snapshot = str(entitlement["rate_limit_rule"])
        order.catalog_version_snapshot = str(entitlement["catalog_version"])
        order.plan_id = order.plan_ref

    order.billing_version = max(int(getattr(order, "billing_version", 0) or 0), 3)
    order.entitlement_snapshot_json = _json_dumps(snapshot)


def get_payment_order_snapshot(order: PaymentOrder) -> dict[str, Any]:
    if not any(
        [
            getattr(order, "order_kind", None),
            getattr(order, "plan_price_snapshot", None) is not None,
            getattr(order, "addon_resource_snapshot", None),
            getattr(order, "entitlement_snapshot_json", None),
        ]
    ):
        return {}

    payload: dict[str, Any] = {}
    if getattr(order, "entitlement_snapshot_json", None):
        try:
            payload = json.loads(order.entitlement_snapshot_json)
        except Exception:
            payload = {}

    payload.update(
        {
            "order_kind": getattr(order, "order_kind", None) or payload.get("order_kind"),
            "action": getattr(order, "action", None) or payload.get("action"),
            "pricing_mode_snapshot": getattr(order, "pricing_mode_snapshot", None) or payload.get("pricing_mode_snapshot"),
            "plan_ref": getattr(order, "plan_ref", None) or payload.get("plan_ref"),
            "plan_price": getattr(order, "plan_price_snapshot", None)
            if getattr(order, "plan_price_snapshot", None) is not None
            else payload.get("plan_price"),
            "months": getattr(order, "months_snapshot", None)
            if getattr(order, "months_snapshot", None) is not None
            else payload.get("months"),
            "expected_amount": getattr(order, "expected_amount_snapshot", None)
            if getattr(order, "expected_amount_snapshot", None) is not None
            else payload.get("expected_amount"),
            "paid_amount": getattr(order, "paid_amount_snapshot", None)
            if getattr(order, "paid_amount_snapshot", None) is not None
            else payload.get("paid_amount"),
            "daily_quota": getattr(order, "daily_quota_snapshot", None)
            if getattr(order, "daily_quota_snapshot", None) is not None
            else payload.get("daily_quota"),
            "rate_limit_rule": getattr(order, "rate_limit_rule_snapshot", None) or payload.get("rate_limit_rule"),
            "addon_resource_amount": getattr(order, "addon_resource_snapshot", None)
            if getattr(order, "addon_resource_snapshot", None) is not None
            else payload.get("addon_resource_amount"),
            "catalog_version": getattr(order, "catalog_version_snapshot", None) or payload.get("catalog_version"),
        }
    )
    return payload
