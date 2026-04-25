from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.config import settings
from core.dependencies import get_effective_user_limit_rule, normalize_expiration_utc
from core.v2_request_metrics import get_subject_daily_usage_row, resolve_usage_subject_for_user
from db.models import RequestLog, User, V2User

DAY_LIMIT_RULE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*day\s*$", re.IGNORECASE)

QUOTA_SOURCE_DAILY = "daily"
QUOTA_SOURCE_ADDON = "addon"
QUOTA_SOURCE_NONE = "none"


@dataclass
class QuotaAllocation:
    username: str
    user_uuid: str
    cost: int
    requested_source: str
    client_ip: str | None = None
    quota_scope: str = "user"
    actor_type: str = "authenticated"
    final_source: str = QUOTA_SOURCE_NONE


@dataclass
class QuotaSnapshot:
    daily_limit: Optional[int]
    today_daily_used: int
    remaining_daily: Optional[int]
    addon_resource_balance: int
    addon_resource_total: int
    addon_expiration_utc: Optional[str]
    addon_is_active: bool


def is_new_quota_mode_enabled() -> bool:
    return not settings.LEGACY_CLIENT_COMPAT


def _extract_daily_limit(limit_rule: Optional[str]) -> Optional[int]:
    if not limit_rule:
        return None
    for part in (segment.strip() for segment in limit_rule.split(";") if segment.strip()):
        matched = DAY_LIMIT_RULE_PATTERN.match(part)
        if matched:
            return int(matched.group(1))
    return None


def get_daily_subscription_limit(user: User | V2User) -> Optional[int]:
    if bool(getattr(user, "is_default_test_user", False)):
        return int(settings.DEFAULT_TEST_USER_IP_DAILY_QUOTA or 0) or None
    if isinstance(user, V2User):
        return _extract_daily_limit((getattr(user, "limit_rule", None) or "").strip())
    return _extract_daily_limit(get_effective_user_limit_rule(user))


def _utc_day_range(now_utc: Optional[datetime] = None) -> tuple[datetime, datetime]:
    current = now_utc or datetime.now(timezone.utc)
    day_start = datetime.combine(current.date(), datetime.min.time()).replace(tzinfo=None)
    return day_start, day_start + timedelta(days=1)


def _addon_expiration_utc(user: User | V2User) -> Optional[datetime]:
    addon_expiration = normalize_expiration_utc(getattr(user, "addon_expiration_date", None))
    subscription_expiration = normalize_expiration_utc(getattr(user, "expiration_date", None))

    if addon_expiration is None or subscription_expiration is None:
        return None
    return min(addon_expiration, subscription_expiration)


def get_available_addon_balance(user: User | V2User, now_utc: Optional[datetime] = None) -> int:
    if bool(getattr(user, "is_default_test_user", False)):
        return 0
    current = now_utc or datetime.now(timezone.utc)
    expiration = _addon_expiration_utc(user)
    balance = int(getattr(user, "addon_resource_balance", 0) or 0)
    if balance <= 0 or expiration is None or expiration <= current:
        return 0
    return balance


def get_today_daily_resource_used(
    db: Session,
    user: User | V2User,
    now_utc: Optional[datetime] = None,
    *,
    client_ip: str | None = None,
) -> int:
    if isinstance(user, V2User):
        subject = resolve_usage_subject_for_user(user, client_ip=client_ip)
        if subject is None:
            return 0
        subject_type, subject_key = subject
        usage_row = get_subject_daily_usage_row(
            db,
            subject_type=subject_type,
            subject_key=subject_key,
            usage_date=(now_utc or datetime.now(timezone.utc)).date(),
        )
        if usage_row is None:
            return 0
        return int(getattr(usage_row, "daily_resource_used", 0) or 0)

    day_start, day_end = _utc_day_range(now_utc)
    query = (
        db.query(func.sum(RequestLog.resource_cost))
        .filter(
            RequestLog.timestamp >= day_start,
            RequestLog.timestamp < day_end,
            RequestLog.status == "success",
            RequestLog.resource_deducted == True,  # noqa: E712
            or_(
                RequestLog.quota_source == QUOTA_SOURCE_DAILY,
                RequestLog.quota_source == QUOTA_SOURCE_NONE,
                RequestLog.quota_source.is_(None),
            ),
        )
    )

    if bool(getattr(user, "is_default_test_user", False)) and client_ip:
        query = query.filter(
            RequestLog.user_uuid == str(getattr(user, "user_uuid", None) or user.username or "").strip(),
            RequestLog.ip == client_ip,
            RequestLog.quota_scope == "ip",
        )
    else:
        query = query.filter(
            or_(
                RequestLog.user_uuid == str(getattr(user, "user_uuid", None) or user.username or "").strip(),
                RequestLog.username == user.username,
            ),
            or_(RequestLog.quota_scope == "user", RequestLog.quota_scope.is_(None)),
        )

    total = query.scalar()
    return int(total or 0)


def build_quota_snapshot(
    db: Session,
    user: User | V2User,
    now_utc: Optional[datetime] = None,
    *,
    client_ip: str | None = None,
) -> QuotaSnapshot:
    current = now_utc or datetime.now(timezone.utc)
    daily_limit = get_daily_subscription_limit(user)
    today_daily_used = get_today_daily_resource_used(db, user, current, client_ip=client_ip)
    remaining_daily = None if daily_limit is None else max(0, daily_limit - today_daily_used)
    addon_balance = get_available_addon_balance(user, current)
    addon_expiration = _addon_expiration_utc(user)
    addon_expiration_utc = None
    if addon_expiration is not None:
        addon_expiration_utc = addon_expiration.isoformat().replace("+00:00", "Z")

    return QuotaSnapshot(
        daily_limit=daily_limit,
        today_daily_used=today_daily_used,
        remaining_daily=remaining_daily,
        addon_resource_balance=addon_balance,
        addon_resource_total=int(getattr(user, "addon_resource_total", 0) or 0),
        addon_expiration_utc=addon_expiration_utc,
        addon_is_active=addon_balance > 0,
    )


def _raise_quota_exceeded(
    *,
    cost: int,
    daily_limit: Optional[int],
    today_daily_used: int,
    addon_balance: int,
) -> None:
    remaining_daily = None if daily_limit is None else max(0, daily_limit - today_daily_used)
    raise HTTPException(
        status_code=430,
        detail={
            "code": "resource_quota_exceeded",
            "message": "Insufficient resource quota",
            "cost": int(cost),
            "daily_limit": daily_limit,
            "today_daily_used": int(today_daily_used),
            "remaining_daily": remaining_daily,
            "addon_resource_balance": int(addon_balance),
        },
    )


def prepare_quota_allocation(db: Session, user: User | V2User, cost: int, *, client_ip: str | None = None) -> Optional[QuotaAllocation]:
    if not is_new_quota_mode_enabled():
        return None

    normalized_cost = max(1, int(cost or 1))
    quota_scope = "ip" if bool(getattr(user, "is_default_test_user", False)) and client_ip else "user"
    actor_type = "anonymous_test" if quota_scope == "ip" else "authenticated"
    snapshot = build_quota_snapshot(db, user, client_ip=client_ip)
    if snapshot.remaining_daily is not None and snapshot.remaining_daily >= normalized_cost:
        return QuotaAllocation(
            username=user.username,
            user_uuid=user.user_uuid,
            cost=normalized_cost,
            requested_source=QUOTA_SOURCE_DAILY,
            client_ip=client_ip,
            quota_scope=quota_scope,
            actor_type=actor_type,
        )
    if snapshot.addon_resource_balance >= normalized_cost:
        return QuotaAllocation(
            username=user.username,
            user_uuid=user.user_uuid,
            cost=normalized_cost,
            requested_source=QUOTA_SOURCE_ADDON,
            client_ip=client_ip,
            quota_scope=quota_scope,
            actor_type=actor_type,
        )

    _raise_quota_exceeded(
        cost=normalized_cost,
        daily_limit=snapshot.daily_limit,
        today_daily_used=snapshot.today_daily_used,
        addon_balance=snapshot.addon_resource_balance,
    )


def commit_quota_allocation(db: Session, allocation: Optional[QuotaAllocation]) -> Optional[QuotaAllocation]:
    if allocation is None or not is_new_quota_mode_enabled():
        return allocation

    user = db.query(User).filter(User.username == allocation.username).with_for_update().first()
    if user is None:
        user = db.query(V2User).filter(V2User.user_uuid == allocation.user_uuid).with_for_update().first()
    if user is None:
        user = db.query(V2User).filter(V2User.username == allocation.username).with_for_update().first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "user_not_found", "message": "User not found"},
        )

    snapshot = build_quota_snapshot(db, user, client_ip=allocation.client_ip)
    if snapshot.remaining_daily is not None and snapshot.remaining_daily >= allocation.cost:
        allocation.final_source = QUOTA_SOURCE_DAILY
        return allocation

    if snapshot.addon_resource_balance >= allocation.cost:
        user.addon_resource_balance = max(0, int(user.addon_resource_balance or 0) - allocation.cost)
        if user.expiration_date is not None:
            user.addon_expiration_date = user.expiration_date
        allocation.final_source = QUOTA_SOURCE_ADDON
        return allocation

    _raise_quota_exceeded(
        cost=allocation.cost,
        daily_limit=snapshot.daily_limit,
        today_daily_used=snapshot.today_daily_used,
        addon_balance=snapshot.addon_resource_balance,
    )
