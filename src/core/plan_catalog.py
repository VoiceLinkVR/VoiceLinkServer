import json
import re
from pathlib import Path
from typing import Any

from core.config import settings


DAY_LIMIT_RULE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*day\s*$", re.IGNORECASE)
PLAN_ID_RULE_PATTERN = re.compile(r"^\s*plan_id\s*=\s*([A-Za-z0-9._-]+)\s*$", re.IGNORECASE)
LEGACY_PLAN_ID_PATTERN = re.compile(r"^legacy-m(?P<cents>\d+)-q(?P<quota>\d+)$", re.IGNORECASE)
SUBSCRIPTION_MODE_FIXED_TIERS = "fixed_tiers"
SUBSCRIPTION_MODE_LADDER = "ladder"
SAFE_PLAN_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
SAFE_LIMIT_RULE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789=;_./,:- ")


def _round_price(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return round(float(default), 2)


def _extract_daily_limit(limit_rule: str | None) -> int | None:
    if not limit_rule:
        return None
    for segment in (part.strip() for part in limit_rule.split(";") if part.strip()):
        matched = DAY_LIMIT_RULE_PATTERN.match(segment)
        if matched:
            return int(matched.group(1))
    return None


def extract_plan_id(limit_rule: str | None) -> str | None:
    if not limit_rule:
        return None
    for segment in (part.strip() for part in limit_rule.split(";") if part.strip()):
        matched = PLAN_ID_RULE_PATTERN.match(segment)
        if matched:
            return matched.group(1).strip()
    return None


def generate_plan_id(monthly_price: float, daily_quota: int) -> str:
    quota = max(1, int(daily_quota))
    normalized_price = round(float(monthly_price), 2)
    if normalized_price <= 0:
        return f"free-q{quota}"
    cents = int(round(normalized_price * 100))
    return f"sub-m{cents}-q{quota}"


def generate_addon_id(price: float, resource_amount: int, expires_in_days: int) -> str:
    cents = int(round(round(float(price), 2) * 100))
    resource = max(1, int(resource_amount))
    expires = max(1, int(expires_in_days))
    return f"addon-p{cents}-r{resource}-d{expires}"


def _ensure_mapping(raw: Any, key: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{key} must be an object")
    return raw


def _ensure_list(raw: Any, key: str) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list")
    return raw


def _normalize_id(value: Any, *, fallback: str, key: str) -> str:
    normalized = str(value or fallback).strip()
    if not normalized:
        raise ValueError(f"{key} must not be empty")
    if any(char not in SAFE_PLAN_ID_CHARS for char in normalized):
        raise ValueError(f"{key} contains invalid characters")
    return normalized


def _normalize_limit_rule(value: Any, *, key: str) -> str:
    normalized = str(value or "").strip()
    if any(char not in SAFE_LIMIT_RULE_CHARS for char in normalized):
        raise ValueError(f"{key} contains invalid characters")
    return normalized


def _default_unified_free_daily_quota() -> int:
    return max(1, int(settings.DEFAULT_TEST_USER_IP_DAILY_QUOTA or _extract_daily_limit(settings.FREE_PLAN_LIMIT_RULE) or 800))


def _default_unified_free_resource_quota() -> int:
    return max(0, int(settings.DEFAULT_TEST_USER_RESOURCE_QUOTA or 0))


def _default_unified_free_rate_limit() -> str:
    return _normalize_limit_rule(
        settings.DEFAULT_TEST_USER_IP_RATE_LIMIT or settings.FREE_PLAN_LIMIT_RULE,
        key="default_unified_free.rate_limit",
    )


def _build_default_catalog() -> dict[str, Any]:
    free_quota = _default_unified_free_daily_quota()
    default_test_quota = _default_unified_free_daily_quota()
    default_test_resource_quota = _default_unified_free_resource_quota()
    default_test_rate_limit = _default_unified_free_rate_limit()
    base_increment = max(1, int(settings.AFDIAN_BOOST_DAILY_INCREMENT or 1500))
    addon_increment = max(1, int(settings.AFDIAN_ADDON_RESOURCE_INCREMENT or 5000))
    addon_price = round(float(settings.AFDIAN_ADDON_PRICE_STEP or 5.0), 2)
    prices = [5.0, 10.0, 20.0, 50.0, 100.0]

    tiers = []
    for price in prices:
        units = max(1, int(round(price / 5.0)))
        daily_quota = units * base_increment
        tiers.append(
            {
                "id": generate_plan_id(price, daily_quota),
                "monthly_price": round(price, 2),
                "daily_quota": int(daily_quota),
                "resource_quota": 0,
                "rate_limit": "",
            }
        )

    return {
        "version": 2,
        "billing": {
            "subscription_mode": SUBSCRIPTION_MODE_FIXED_TIERS,
            "ladder": {
                "base_price": 5.0,
                "min_monthly_price": 5.0,
                "max_monthly_price": 100.0,
                "price_step": 5.0,
                "base_daily_quota": 3000,
                "daily_quota_step": 3000,
            },
        },
        "free_plan": {
            "id": "free",
            "monthly_price": 0.0,
            "daily_quota": int(free_quota),
            "resource_quota": default_test_resource_quota,
            "rate_limit": default_test_rate_limit,
        },
        "default_test_plan": {
            "id": "default_test",
            "monthly_price": 0.0,
            "daily_quota": default_test_quota,
            "resource_quota": default_test_resource_quota,
            "rate_limit": default_test_rate_limit,
        },
        "tiers": tiers,
        "addons": [
            {
                "id": f"addon_{int(addon_price)}",
                "price": addon_price,
                "resource_amount": addon_increment,
                "expires_in_days": 365,
            }
        ],
    }


def _catalog_path() -> Path:
    return Path(settings.PLAN_CATALOG_PATH)


def _normalize_tier(raw: dict[str, Any]) -> dict[str, Any]:
    monthly_price = round(float(raw.get("monthly_price") or 0), 2)
    daily_quota = int(raw.get("daily_quota") or 0)
    resource_quota = int(raw.get("resource_quota") or 0)
    if monthly_price <= 0:
        raise ValueError("monthly_price must be greater than 0")
    if daily_quota <= 0:
        raise ValueError("daily_quota must be greater than 0")
    if resource_quota < 0:
        raise ValueError("resource_quota must be greater than or equal to 0")
    return {
        "id": _normalize_id(
            raw.get("id"),
            fallback=generate_plan_id(monthly_price, daily_quota),
            key="tiers[].id",
        ),
        "monthly_price": monthly_price,
        "daily_quota": daily_quota,
        "resource_quota": resource_quota,
        "rate_limit": _normalize_limit_rule(raw.get("rate_limit"), key="tiers[].rate_limit"),
    }


def _normalize_addon(raw: dict[str, Any]) -> dict[str, Any]:
    price = round(float(raw.get("price") or 0), 2)
    resource_amount = int(raw.get("resource_amount") or 0)
    expires_in_days = int(raw.get("expires_in_days") or 0)
    if price <= 0:
        raise ValueError("addon price must be greater than 0")
    if resource_amount <= 0:
        raise ValueError("addon resource_amount must be greater than 0")
    if expires_in_days <= 0:
        raise ValueError("addon expires_in_days must be greater than 0")
    return {
        "id": _normalize_id(
            raw.get("id"),
            fallback=generate_addon_id(price, resource_amount, expires_in_days),
            key="addons[].id",
        ),
        "price": price,
        "resource_amount": resource_amount,
        "expires_in_days": expires_in_days,
    }


def normalize_catalog(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = _build_default_catalog()
    raw = _ensure_mapping(raw, "catalog")
    version = int(raw.get("version") or merged.get("version") or 1)
    billing_raw = _ensure_mapping(raw.get("billing"), "billing")
    ladder_default = merged["billing"]["ladder"]
    ladder_raw = _ensure_mapping(billing_raw.get("ladder"), "billing.ladder")

    subscription_mode_value = billing_raw.get("subscription_mode")
    subscription_mode = str(subscription_mode_value or merged["billing"]["subscription_mode"]).strip().lower()
    if subscription_mode_value is not None and subscription_mode not in {
        SUBSCRIPTION_MODE_FIXED_TIERS,
        SUBSCRIPTION_MODE_LADDER,
    }:
        raise ValueError("billing.subscription_mode must be fixed_tiers or ladder")
    if subscription_mode not in {SUBSCRIPTION_MODE_FIXED_TIERS, SUBSCRIPTION_MODE_LADDER}:
        subscription_mode = merged["billing"]["subscription_mode"]

    base_price = max(0.01, _round_price(ladder_raw.get("base_price"), ladder_default["base_price"]))
    min_monthly_price = max(base_price, _round_price(ladder_raw.get("min_monthly_price"), ladder_default["min_monthly_price"]))
    price_step = max(0.01, _round_price(ladder_raw.get("price_step"), ladder_default["price_step"]))
    max_monthly_price = max(min_monthly_price, _round_price(ladder_raw.get("max_monthly_price"), ladder_default["max_monthly_price"]))
    base_daily_quota = max(1, int(ladder_raw.get("base_daily_quota") or ladder_default["base_daily_quota"]))
    daily_quota_step = max(1, int(ladder_raw.get("daily_quota_step") or ladder_default["daily_quota_step"]))

    merged["billing"] = {
        "subscription_mode": subscription_mode,
        "ladder": {
            "base_price": base_price,
            "min_monthly_price": min_monthly_price,
            "max_monthly_price": max_monthly_price,
            "price_step": price_step,
            "base_daily_quota": base_daily_quota,
            "daily_quota_step": daily_quota_step,
        },
    }

    free_raw = _ensure_mapping(raw.get("free_plan"), "free_plan")
    free_quota = _default_unified_free_daily_quota()
    free_resource_quota = _default_unified_free_resource_quota()
    merged["free_plan"] = {
        "id": _normalize_id(free_raw.get("id"), fallback=merged["free_plan"]["id"], key="free_plan.id"),
        "monthly_price": 0.0,
        "daily_quota": free_quota,
        "resource_quota": free_resource_quota,
        "rate_limit": _default_unified_free_rate_limit(),
    }

    default_test_raw = _ensure_mapping(raw.get("default_test_plan"), "default_test_plan")
    default_test_daily_quota = _default_unified_free_daily_quota()
    default_test_resource_quota = _default_unified_free_resource_quota()
    merged["default_test_plan"] = {
        "id": _normalize_id(
            default_test_raw.get("id"),
            fallback=merged["default_test_plan"]["id"],
            key="default_test_plan.id",
        ),
        "monthly_price": 0.0,
        "daily_quota": default_test_daily_quota,
        "resource_quota": default_test_resource_quota,
        "rate_limit": _default_unified_free_rate_limit(),
    }

    normalized_tiers = [_normalize_tier(_ensure_mapping(item, "tiers[]")) for item in (_ensure_list(raw.get("tiers"), "tiers") or merged["tiers"])]
    normalized_tiers.sort(key=lambda item: (item["monthly_price"], item["daily_quota"], item["id"]))

    unique_prices: set[float] = set()
    unique_tier_ids: set[str] = set()
    for tier in normalized_tiers:
        if tier["monthly_price"] in unique_prices:
            raise ValueError("monthly_price must be unique across tiers")
        unique_prices.add(tier["monthly_price"])
        if tier["id"] in unique_tier_ids:
            raise ValueError("tier id must be unique")
        unique_tier_ids.add(tier["id"])

    if not normalized_tiers:
        raise ValueError("at least one paid tier is required")

    merged["tiers"] = normalized_tiers
    normalized_addons = [_normalize_addon(_ensure_mapping(item, "addons[]")) for item in (_ensure_list(raw.get("addons"), "addons") or merged.get("addons") or [])]
    normalized_addons.sort(key=lambda item: (item["price"], item["resource_amount"], item["expires_in_days"], item["id"]))
    unique_addon_ids: set[str] = set()
    for addon in normalized_addons:
        if addon["id"] in unique_addon_ids:
            raise ValueError("addon id must be unique")
        unique_addon_ids.add(addon["id"])
    merged["addons"] = normalized_addons
    merged["version"] = 2 if version >= 2 else version
    return merged


def load_plan_catalog() -> dict[str, Any]:
    path = _catalog_path()
    if not path.exists():
        catalog = normalize_catalog()
        save_plan_catalog(catalog)
        return catalog

    raw = json.loads(path.read_text(encoding="utf-8"))
    catalog = normalize_catalog(raw)
    if catalog != raw:
        save_plan_catalog(catalog)
    return catalog


def save_plan_catalog(raw: dict[str, Any]) -> dict[str, Any]:
    catalog = normalize_catalog(raw)
    path = _catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog


def get_free_plan() -> dict[str, Any]:
    return load_plan_catalog()["free_plan"]


def get_default_test_plan() -> dict[str, Any]:
    return load_plan_catalog()["default_test_plan"]


def get_paid_tiers() -> list[dict[str, Any]]:
    catalog = load_plan_catalog()
    if get_subscription_mode(catalog) == SUBSCRIPTION_MODE_LADDER:
        ladder = get_ladder_billing_config(catalog)
        tiers: list[dict[str, Any]] = []
        price = ladder["min_monthly_price"]
        step = ladder["price_step"]
        while price <= ladder["max_monthly_price"] + 0.0001:
            tiers.append(build_tier_for_price(price, catalog=catalog))
            price = round(price + step, 2)
        return tiers
    return list(catalog["tiers"])


def get_addons() -> list[dict[str, Any]]:
    return list(load_plan_catalog().get("addons", []))


def get_subscription_mode(catalog: dict[str, Any] | None = None) -> str:
    current = catalog or load_plan_catalog()
    billing = current.get("billing") or {}
    mode = str(billing.get("subscription_mode") or SUBSCRIPTION_MODE_FIXED_TIERS).strip().lower()
    if mode not in {SUBSCRIPTION_MODE_FIXED_TIERS, SUBSCRIPTION_MODE_LADDER}:
        return SUBSCRIPTION_MODE_FIXED_TIERS
    return mode


def get_ladder_billing_config(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    current = catalog or load_plan_catalog()
    return dict((current.get("billing") or {}).get("ladder") or {})


def get_first_paid_tier() -> dict[str, Any]:
    return get_paid_tiers()[0]


def get_next_paid_tier(current_price: float) -> dict[str, Any] | None:
    normalized_price = round(float(current_price or 0), 2)
    for tier in get_paid_tiers():
        if tier["monthly_price"] > normalized_price:
            return tier
    return None


def find_paid_tier_by_price(monthly_price: float) -> dict[str, Any] | None:
    normalized_price = round(float(monthly_price or 0), 2)
    for tier in get_paid_tiers():
        if round(float(tier["monthly_price"]), 2) == normalized_price:
            return tier
    return None


def build_tier_for_price(monthly_price: float, *, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_price = round(float(monthly_price or 0), 2)
    if normalized_price <= 0:
        free_plan = get_free_plan()
        return dict(free_plan)

    current = catalog or load_plan_catalog()
    if get_subscription_mode(current) == SUBSCRIPTION_MODE_FIXED_TIERS:
        tier = next(
            (item for item in current["tiers"] if round(float(item["monthly_price"]), 2) == normalized_price),
            None,
        )
        if tier is None:
            raise ValueError(f"tier not found for monthly_price={normalized_price}")
        return dict(tier)

    ladder = get_ladder_billing_config(current)
    min_price = round(float(ladder["min_monthly_price"]), 2)
    max_price = round(float(ladder["max_monthly_price"]), 2)
    price_step = round(float(ladder["price_step"]), 2)
    base_price = round(float(ladder["base_price"]), 2)
    if normalized_price < min_price - 0.0001 or normalized_price > max_price + 0.0001:
        raise ValueError(f"monthly_price out of ladder range: {normalized_price}")

    units_float = (normalized_price - base_price) / max(price_step, 0.01)
    units = int(round(units_float))
    if abs(units_float - units) > 0.0001:
        raise ValueError(f"monthly_price does not match ladder step: {normalized_price}")

    daily_quota = int(ladder["base_daily_quota"]) + units * int(ladder["daily_quota_step"])
    return {
        "id": generate_plan_id(normalized_price, daily_quota),
        "monthly_price": normalized_price,
        "daily_quota": int(daily_quota),
        "resource_quota": 0,
        "rate_limit": "",
    }


def build_limit_rule(base_rule: str | None, plan_id: str, daily_quota: int) -> str:
    desired_daily = max(1, int(daily_quota))
    parts = []
    for segment in (part.strip() for part in (base_rule or "").split(";") if part.strip()):
        if DAY_LIMIT_RULE_PATTERN.match(segment):
            continue
        if PLAN_ID_RULE_PATTERN.match(segment):
            continue
        parts.append(segment)

    next_parts = [f"plan_id={plan_id}", f"{desired_daily}/day", *parts]
    return ";".join(next_parts)


def build_limit_rule_for_price(base_rule: str | None, monthly_price: float) -> str:
    normalized_price = round(float(monthly_price or 0), 2)
    if normalized_price <= 0:
        free_plan = get_free_plan()
        return build_limit_rule(base_rule, free_plan["id"], free_plan["daily_quota"])

    tier = build_tier_for_price(normalized_price)
    return build_limit_rule(base_rule, tier["id"], tier["daily_quota"])


def resolve_plan_from_rule(limit_rule: str | None, expiration_date: Any = None) -> dict[str, Any]:
    free_plan = get_free_plan()
    paid_tiers = get_paid_tiers()
    plan_id = extract_plan_id(limit_rule)
    daily_quota = _extract_daily_limit(limit_rule)

    if plan_id == free_plan["id"]:
        return dict(free_plan)

    for tier in paid_tiers:
        if plan_id and tier["id"] == plan_id:
            return dict(tier)

    legacy_match = LEGACY_PLAN_ID_PATTERN.match(plan_id or "")
    if legacy_match:
        cents = int(legacy_match.group("cents"))
        quota = int(legacy_match.group("quota"))
        return {
            "id": plan_id,
            "monthly_price": round(cents / 100.0, 2),
            "daily_quota": max(1, quota),
            "resource_quota": 0,
            "rate_limit": _normalize_limit_rule(
                ";".join(
                    [
                        segment.strip()
                        for segment in (limit_rule or "").split(";")
                        if segment.strip() and not segment.strip().lower().startswith("plan_id=") and "/day" not in segment.lower()
                    ]
                ),
                key="legacy_plan.rate_limit",
            )
            if limit_rule
            else "",
        }

    if expiration_date is None and daily_quota == free_plan["daily_quota"]:
        return dict(free_plan)

    for tier in paid_tiers:
        if daily_quota == tier["daily_quota"]:
            return dict(tier)

    if expiration_date is None:
        return dict(free_plan)
    return dict(get_first_paid_tier())
