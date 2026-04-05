import json
import re
from pathlib import Path
from typing import Any

from core.config import settings


DAY_LIMIT_RULE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*day\s*$", re.IGNORECASE)
PLAN_ID_RULE_PATTERN = re.compile(r"^\s*plan_id\s*=\s*([A-Za-z0-9._-]+)\s*$", re.IGNORECASE)


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


def _build_default_catalog() -> dict[str, Any]:
    free_quota = _extract_daily_limit(settings.FREE_PLAN_LIMIT_RULE) or 800
    base_increment = max(1, int(settings.AFDIAN_BOOST_DAILY_INCREMENT or 1500))
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
            }
        )

    return {
        "free_plan": {
            "id": generate_plan_id(0, free_quota),
            "monthly_price": 0.0,
            "daily_quota": int(free_quota),
        },
        "tiers": tiers,
    }


def _catalog_path() -> Path:
    return Path(settings.PLAN_CATALOG_PATH)


def _normalize_tier(raw: dict[str, Any]) -> dict[str, Any]:
    monthly_price = round(float(raw.get("monthly_price") or 0), 2)
    daily_quota = int(raw.get("daily_quota") or 0)
    if monthly_price <= 0:
        raise ValueError("monthly_price must be greater than 0")
    if daily_quota <= 0:
        raise ValueError("daily_quota must be greater than 0")
    return {
        "id": str(raw.get("id") or generate_plan_id(monthly_price, daily_quota)),
        "monthly_price": monthly_price,
        "daily_quota": daily_quota,
    }


def normalize_catalog(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = _build_default_catalog()
    raw = raw or {}

    free_raw = raw.get("free_plan") or {}
    free_quota = int(free_raw.get("daily_quota") or merged["free_plan"]["daily_quota"])
    merged["free_plan"] = {
        "id": str(free_raw.get("id") or generate_plan_id(0, free_quota)),
        "monthly_price": 0.0,
        "daily_quota": free_quota,
    }

    normalized_tiers = [_normalize_tier(item) for item in (raw.get("tiers") or merged["tiers"])]
    normalized_tiers.sort(key=lambda item: (item["monthly_price"], item["daily_quota"], item["id"]))

    unique_prices: set[float] = set()
    for tier in normalized_tiers:
        if tier["monthly_price"] in unique_prices:
            raise ValueError("monthly_price must be unique across tiers")
        unique_prices.add(tier["monthly_price"])

    if not normalized_tiers:
        raise ValueError("at least one paid tier is required")

    merged["tiers"] = normalized_tiers
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


def get_paid_tiers() -> list[dict[str, Any]]:
    return list(load_plan_catalog()["tiers"])


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

    tier = find_paid_tier_by_price(normalized_price)
    if tier is None:
        raise ValueError(f"tier not found for monthly_price={normalized_price}")
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

    if expiration_date is None and daily_quota == free_plan["daily_quota"]:
        return dict(free_plan)

    for tier in paid_tiers:
        if daily_quota == tier["daily_quota"]:
            return dict(tier)

    if expiration_date is None:
        return dict(free_plan)
    return dict(get_first_paid_tier())
