# src/core/rate_limiter.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from fastapi import Depends, Request
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter

from core.config import settings
from core.dependencies import get_current_user
from core.logging_config import logger
from db.models import User

DEFAULT_GLOBAL_LIMIT = "400/hour"
FALLBACK_USER_LIMIT = "500/day;400/hour"
SHARED_LIMIT_SCOPE = "shared_limit_scope"


class RateLimitExceeded(Exception):
    """统一的限速异常，保持与旧版 server.py 一致的返回结构"""

    def __init__(self, limit: str, scope: Optional[str], key: str, triggered_limit: str):
        super().__init__("Too many request")
        self.limit = limit              # 原始规则字符串，例如 "10/day;1/minute"
        self.triggered_limit = triggered_limit  # 真正超限的那一条，例如 "1 per 1 minute"
        self.scope = scope or ""
        self.key = key


def parse_limit_string(limit_string: str) -> List:
    limits: List = []
    for raw in [part.strip() for part in limit_string.split(";") if part.strip()]:
        try:
            limits.append(parse(raw))
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"[RATE-LIMITER] 无法解析限速规则 '{raw}'，跳过。错误: {exc}")
    return limits


def get_client_ip(request: Request) -> str:
    """与旧版 limit_key_func 行为保持一致的 IP 解析顺序"""
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
    deduct_when: Callable[[int], bool] = lambda status: True
    checked: bool = field(default=False, init=False)

    @property
    def composite_key(self) -> str:
        return f"{self.scope}:{self.key}" if self.scope else self.key

    def check(self) -> None:
        if not self.limiter.enabled or not self.limit_items:
            return
        for item in self.limit_items:
            if not self.limiter.strategy.test(item, self.composite_key):
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
                self.limiter.strategy.hit(item, self.composite_key)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(f"[RATE-LIMITER] 提交限速计数失败 (key={self.composite_key}, limit={item}): {exc}")

    def should_deduct(self, status_code: int) -> bool:
        try:
            return bool(self.deduct_when(status_code))
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(f"[RATE-LIMITER] deduct_when 回调异常，默认扣减。错误: {exc}")
            return True


class RateLimiter:
    def __init__(self, storage_uri: Optional[str]):
        self.enabled = settings.LIMIT_ENABLE
        self.storage_uri = storage_uri or "memory://"
        self.storage = self._init_storage(self.storage_uri)
        self.strategy = MovingWindowRateLimiter(self.storage)

        if self.enabled:
            logger.info(f"[RATE-LIMITER] 已启用，存储: {self.storage_uri}")
        else:
            logger.info("[RATE-LIMITER] 已禁用（LIMIT_ENABLE=False）")

    @staticmethod
    def _init_storage(uri: str):
        try:
            return storage_from_string(uri)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"[RATE-LIMITER] 初始化存储 '{uri}' 失败，回退至内存存储。错误: {exc}")
            return storage_from_string("memory://")

    def create_context(
        self,
        limit_string: str,
        key: str,
        scope: Optional[str] = None,
        deduct_when: Optional[Callable[[int], bool]] = None,
    ) -> Optional[RateLimitContext]:
        if not self.enabled:
            return None
        limit_items = parse_limit_string(limit_string)
        if not limit_items:
            logger.warning(f"[RATE-LIMITER] '{limit_string}' 未解析出有效规则，跳过限速。")
            return None
        return RateLimitContext(
            limiter=self,
            limit_string=limit_string,
            limit_items=limit_items,
            key=key,
            scope=scope,
            deduct_when=deduct_when or (lambda status: True),
        )

    def create_default_context(self, request: Request) -> Optional[RateLimitContext]:
        key = get_client_ip(request)
        return self.create_context(
            limit_string=DEFAULT_GLOBAL_LIMIT,
            key=key,
            scope=SHARED_LIMIT_SCOPE,
            deduct_when=lambda status: True,
        )


rate_limiter = RateLimiter(settings.LIMITER_REDIS_URL)


async def enforce_user_rate_limit(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    """
    FastAPI 端每次调用时执行与旧版 dynamic_limit 相同的逻辑。
    """

    if not rate_limiter.enabled:
        return current_user

    limit_rule = (current_user.limit_rule or "").strip() or FALLBACK_USER_LIMIT

    is_public_test_user = (
        settings.LIMIT_PUBLIC_TEST_USER is not None
        and current_user.username == settings.LIMIT_PUBLIC_TEST_USER
    )

    if is_public_test_user:
        context = rate_limiter.create_context(
            limit_string=limit_rule,
            key=get_client_ip(request),
            scope=SHARED_LIMIT_SCOPE,
            deduct_when=lambda status: True,
        )
    else:
        daily_key = f"{current_user.username}:{datetime.now().strftime('%Y-%m-%d')}"
        context = rate_limiter.create_context(
            limit_string=limit_rule,
            key=daily_key,
            scope=f"user:{current_user.username}",
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