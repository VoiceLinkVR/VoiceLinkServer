from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from fastapi import Depends, Request
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter
from sqlalchemy.orm import Session

from core.config import settings
from core.dependencies import get_current_user, get_db, get_effective_user_limit_rule
from core.logging_config import logger
from core.quota_service import prepare_quota_allocation
from db.models import User

DEFAULT_GLOBAL_LIMIT = "3000/hour"
FALLBACK_USER_LIMIT = "800/day;8/minute"
SHARED_LIMIT_SCOPE = "shared_limit_scope"
USER_CENTER_LIMIT = "8/minute"
USER_CENTER_SCOPE = "user_center"
AUTH_LIMIT_STATUS_MAX = 499


def _utc_day_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RateLimitExceeded(Exception):
    def __init__(self, limit: str, scope: Optional[str], key: str, triggered_limit: str):
        super().__init__("Too many request")
        self.limit = limit
        self.triggered_limit = triggered_limit
        self.scope = scope or ""
        self.key = key


def parse_limit_string(limit_string: str) -> List:
    limits: List = []
    for raw in [part.strip() for part in limit_string.split(";") if part.strip()]:
        try:
            limits.append(parse(raw))
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("[RATE-LIMITER] Failed to parse limit rule '%s': %s", raw, exc)
    return limits


def _strip_day_limits(limit_string: str) -> str:
    kept_parts: List[str] = []
    for part in [segment.strip() for segment in limit_string.split(";") if segment.strip()]:
        if "/day" in part.lower():
            continue
        kept_parts.append(part)
    return ";".join(kept_parts)


def get_client_ip(request: Request) -> str:
    x_real_ip = request.headers.get("x-real-ip")
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_real_ip:
        return x_real_ip.split(",")[0].strip()
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@dataclass
class RateLimitContext:
    limiter: "RateLimiter"
    limit_string: str
    limit_items: List
    key: str
    scope: Optional[str] = None
    context_type: str = "generic"
    cost: int = 1
    deduct_when: Callable[[int], bool] = lambda status: True
    checked: bool = field(default=False, init=False)

    @property
    def composite_key(self) -> str:
        return f"{self.scope}:{self.key}" if self.scope else self.key

    @property
    def normalized_cost(self) -> int:
        return max(1, int(self.cost or 1))

    def check(self) -> None:
        if not self.limiter.enabled or not self.limit_items:
            return
        for item in self.limit_items:
            if not self.limiter.strategy.test(item, self.composite_key, cost=self.normalized_cost):
                raise RateLimitExceeded(
                    limit=self.limit_string,
                    scope=self.scope,
                    key=self.composite_key,
                    triggered_limit=str(item),
                )
        self.checked = True

    def commit(self) -> None:
        if not self.limiter.enabled or not self.checked:
            return
        for item in self.limit_items:
            try:
                self.limiter.strategy.hit(item, self.composite_key, cost=self.normalized_cost)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("[RATE-LIMITER] Failed to commit hit (key=%s, limit=%s): %s", self.composite_key, item, exc)

    def should_deduct(self, status_code: int) -> bool:
        try:
            return bool(self.deduct_when(status_code))
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[RATE-LIMITER] deduct_when callback failed, defaulting to deduct: %s", exc)
            return True

    def set_cost(self, cost: int) -> None:
        self.cost = max(1, int(cost or 1))


class RateLimiter:
    def __init__(self, storage_uri: Optional[str]):
        self.enabled = settings.LIMIT_ENABLE
        self.storage_uri = storage_uri or "memory://"
        self.storage = self._init_storage(self.storage_uri)
        self.strategy = MovingWindowRateLimiter(self.storage)

        if self.enabled:
            logger.info("[RATE-LIMITER] Enabled with storage: %s", self.storage_uri)
        else:
            logger.info("[RATE-LIMITER] Disabled (LIMIT_ENABLE=False)")

    @staticmethod
    def _init_storage(uri: str):
        try:
            return storage_from_string(uri)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("[RATE-LIMITER] Failed to initialize storage '%s', falling back to memory: %s", uri, exc)
            return storage_from_string("memory://")

    def create_context(
        self,
        limit_string: str,
        key: str,
        scope: Optional[str] = None,
        context_type: str = "generic",
        cost: int = 1,
        deduct_when: Optional[Callable[[int], bool]] = None,
    ) -> Optional[RateLimitContext]:
        if not self.enabled:
            return None
        limit_items = parse_limit_string(limit_string)
        if not limit_items:
            logger.warning("[RATE-LIMITER] No valid limit rules were parsed from '%s'", limit_string)
            return None
        return RateLimitContext(
            limiter=self,
            limit_string=limit_string,
            limit_items=limit_items,
            key=key,
            scope=scope,
            context_type=context_type,
            cost=cost,
            deduct_when=deduct_when or (lambda status: True),
        )

    def create_default_context(self, request: Request) -> Optional[RateLimitContext]:
        key = get_client_ip(request)
        return self.create_context(
            limit_string=DEFAULT_GLOBAL_LIMIT,
            key=key,
            scope=SHARED_LIMIT_SCOPE,
            context_type="global",
            deduct_when=lambda status: True,
        )


rate_limiter = RateLimiter(settings.LIMITER_REDIS_URL)


async def enforce_user_rate_limit(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    client_ip = get_client_ip(request)
    request.state.quota_user = current_user
    request.state.quota_db = db
    request.state.quota_username = current_user.username
    request.state.quota_client_ip = client_ip
    request.state.pending_quota_allocation = None

    if not rate_limiter.enabled:
        return current_user

    limit_rule = get_effective_user_limit_rule(current_user) or FALLBACK_USER_LIMIT
    is_default_test_user = bool(getattr(current_user, "is_default_test_user", False))

    if is_default_test_user:
        limit_rule = settings.DEFAULT_TEST_USER_IP_RATE_LIMIT or limit_rule
        context = rate_limiter.create_context(
            limit_string=limit_rule,
            key=f"{client_ip}:{_utc_day_bucket()}",
            scope=f"anonymous_ip:{client_ip}",
            context_type="user",
            deduct_when=lambda status: status == 200,
        )
    else:
        if not settings.LEGACY_CLIENT_COMPAT:
            limit_rule = _strip_day_limits(limit_rule)
        context = rate_limiter.create_context(
            limit_string=limit_rule,
            key=f"{current_user.user_uuid or current_user.username}:{_utc_day_bucket()}",
            scope=f"user:{current_user.user_uuid or current_user.username}",
            context_type="user",
            deduct_when=lambda status: status == 200,
        )

    if context:
        context.check()
        pending = getattr(request.state, "rate_limit_contexts", None)
        if pending is None:
            pending = []
            request.state.rate_limit_contexts = pending
        pending.append(context)

    return current_user


def set_user_context_cost(request: Request, cost: int) -> None:
    desired_cost = max(1, int(cost or 1))
    if rate_limiter.enabled:
        for context in getattr(request.state, "rate_limit_contexts", []):
            if getattr(context, "context_type", "") != "user":
                continue
            context.set_cost(desired_cost)
            context.check()

    if settings.LEGACY_CLIENT_COMPAT:
        return

    db = getattr(request.state, "quota_db", None)
    current_user = getattr(request.state, "quota_user", None)
    client_ip = getattr(request.state, "quota_client_ip", None)
    if db is None or current_user is None:
        return

    request.state.pending_quota_allocation = prepare_quota_allocation(db, current_user, desired_cost, client_ip=client_ip)


def enforce_auth_attempt_rate_limit(
    request: Request,
    *,
    scope_prefix: str,
    ip_limit: str,
    identity: Optional[str] = None,
    identity_limit: Optional[str] = None,
) -> None:
    if not rate_limiter.enabled:
        return

    contexts: list[RateLimitContext] = []
    client_ip = get_client_ip(request)
    ip_context = rate_limiter.create_context(
        limit_string=ip_limit,
        key=client_ip,
        scope=f"{scope_prefix}:ip",
        context_type="auth",
        deduct_when=lambda status: 100 <= int(status or 0) <= AUTH_LIMIT_STATUS_MAX,
    )
    if ip_context is not None:
        ip_context.check()
        contexts.append(ip_context)

    normalized_identity = str(identity or "").strip().lower()
    normalized_identity_limit = str(identity_limit or "").strip()
    if normalized_identity and normalized_identity_limit:
        identity_context = rate_limiter.create_context(
            limit_string=normalized_identity_limit,
            key=normalized_identity,
            scope=f"{scope_prefix}:identity",
            context_type="auth",
            deduct_when=lambda status: 100 <= int(status or 0) <= AUTH_LIMIT_STATUS_MAX,
        )
        if identity_context is not None:
            identity_context.check()
            contexts.append(identity_context)

    if not contexts:
        return
    pending = getattr(request.state, "rate_limit_contexts", None)
    if pending is None:
        pending = []
        request.state.rate_limit_contexts = pending
    pending.extend(contexts)


async def enforce_user_center_rate_limit(request: Request) -> None:
    if not rate_limiter.enabled:
        return

    key = get_client_ip(request)
    context = rate_limiter.create_context(
        limit_string=USER_CENTER_LIMIT,
        key=key,
        scope=USER_CENTER_SCOPE,
        context_type="user_center",
        deduct_when=lambda status: True,
    )

    if context:
        context.check()
        pending = getattr(request.state, "rate_limit_contexts", None)
        if pending is None:
            pending = []
            request.state.rate_limit_contexts = pending
        pending.append(context)
