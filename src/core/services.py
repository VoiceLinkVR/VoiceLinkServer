import asyncio
import httpx
import json
import os
import html
import traceback
import struct
import time
import wave
import re
import unicodedata
from contextlib import contextmanager, suppress
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Optional, Sequence, TypeVar
from urllib.parse import urlparse

from fastapi import HTTPException
from io import BytesIO
from openai import OpenAI, AsyncOpenAI
import opuslib
import emoji
from core.config import settings, WHISPER_URL
from core.gemma_translate_service import async_gemma_translate, gemma_translate, is_gemma_translation_supported
from core.logging_config import get_request_id, logger
from core.text_compressor import compress_repeated_chars
from core.update_service import get_translation_capabilities

# 延迟导入translators，避免初始化时的网络问题
translators = None

CancelCheck = Callable[[], Awaitable[None]]
T = TypeVar("T")


class ClientDisconnectedError(Exception):
    """Raised when the caller has gone away and downstream work should stop."""


ASR_QUEUE_LIMIT_ENABLED = os.getenv("ASR_QUEUE_LIMIT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
ASR_QUEUE_DEFAULT_MAX_CONCURRENCY = max(int(os.getenv("ASR_QUEUE_MAX_CONCURRENCY", "4")), 1)
ASR_QUEUE_DEFAULT_MAX_WAITING = max(int(os.getenv("ASR_QUEUE_MAX_WAITING", "4")), 0)
ASR_QUEUE_DEFAULT_TIMEOUT_SECONDS = max(float(os.getenv("ASR_QUEUE_TIMEOUT_SECONDS", "1.5")), 0.05)
ASR_QUEUE_DEFAULT_RETRY_AFTER_SECONDS = max(float(os.getenv("ASR_QUEUE_RETRY_AFTER_SECONDS", "2")), 1.0)
ASR_QUEUE_SLOW_LOG_SECONDS = max(float(os.getenv("ASR_QUEUE_SLOW_LOG_SECONDS", "15")), 0.1)
ASR_QUEUE_LATENCY_WINDOW = max(int(os.getenv("ASR_QUEUE_LATENCY_WINDOW", "64")), 4)
ASR_DOWNSTREAM_CANCEL_GRACE_SECONDS = max(float(os.getenv("ASR_DOWNSTREAM_CANCEL_GRACE_SECONDS", "1")), 0.05)
ASR_CANCEL_CHECK_INTERVAL_SECONDS = max(float(os.getenv("ASR_CANCEL_CHECK_INTERVAL_SECONDS", "0.25")), 0.05)
ASR_HTTP_CONNECT_TIMEOUT_SECONDS = max(float(os.getenv("ASR_HTTP_CONNECT_TIMEOUT_SECONDS", "3")), 0.05)
ASR_HTTP_WRITE_TIMEOUT_SECONDS = max(float(os.getenv("ASR_HTTP_WRITE_TIMEOUT_SECONDS", "10")), 0.05)
ASR_HTTP_POOL_TIMEOUT_SECONDS = max(float(os.getenv("ASR_HTTP_POOL_TIMEOUT_SECONDS", "3")), 0.05)
ASR_HTTP_CLOSE_TIMEOUT_SECONDS = max(float(os.getenv("ASR_HTTP_CLOSE_TIMEOUT_SECONDS", "1")), 0.05)
ASR_HARD_TIMEOUT_GRACE_SECONDS = max(float(os.getenv("ASR_HARD_TIMEOUT_GRACE_SECONDS", "10")), 0.0)
ASR_BACKEND_STATUS_ENABLED = os.getenv("ASR_BACKEND_STATUS_ENABLED", "false").strip().lower() not in {"0", "false", "no", "off"}
ASR_BACKEND_STATUS_TTL_SECONDS = max(float(os.getenv("ASR_BACKEND_STATUS_TTL_SECONDS", "1.5")), 0.05)
ASR_BACKEND_STATUS_TIMEOUT_SECONDS = max(float(os.getenv("ASR_BACKEND_STATUS_TIMEOUT_SECONDS", "0.6")), 0.05)
ASR_BACKEND_STATUS_OVERLOAD_ACTIVE_REQUESTS = max(int(os.getenv("ASR_BACKEND_STATUS_OVERLOAD_ACTIVE_REQUESTS", "4")), 1)
ASR_BACKEND_STATUS_OVERLOAD_OLDEST_SECONDS = max(float(os.getenv("ASR_BACKEND_STATUS_OVERLOAD_OLDEST_SECONDS", "30")), 0.1)
ASR_BACKEND_STATUS_OVERLOAD_MIN_SECONDS = max(float(os.getenv("ASR_BACKEND_STATUS_OVERLOAD_MIN_SECONDS", "12")), 0.0)
ASR_BACKEND_STATUS_RECOVERY_OLDEST_SECONDS = max(float(os.getenv("ASR_BACKEND_STATUS_RECOVERY_OLDEST_SECONDS", "12")), 0.0)
ASR_BACKEND_STATUS_RECOVERY_ACTIVE_REQUESTS = max(int(os.getenv("ASR_BACKEND_STATUS_RECOVERY_ACTIVE_REQUESTS", "2")), 0)
ASR_ENDPOINT_CIRCUIT_BREAKER_ENABLED = os.getenv("ASR_ENDPOINT_CIRCUIT_BREAKER_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
ASR_ENDPOINT_CIRCUIT_WINDOW_SECONDS = max(float(os.getenv("ASR_ENDPOINT_CIRCUIT_WINDOW_SECONDS", "60")), 1.0)
ASR_ENDPOINT_CIRCUIT_CONSECUTIVE_TIMEOUTS = max(int(os.getenv("ASR_ENDPOINT_CIRCUIT_CONSECUTIVE_TIMEOUTS", "2")), 1)
ASR_ENDPOINT_CIRCUIT_CONSECUTIVE_FAILURES = max(int(os.getenv("ASR_ENDPOINT_CIRCUIT_CONSECUTIVE_FAILURES", "3")), 1)
ASR_ENDPOINT_CIRCUIT_TIMEOUT_RATE = min(max(float(os.getenv("ASR_ENDPOINT_CIRCUIT_TIMEOUT_RATE", "0.2")), 0.0), 1.0)
ASR_ENDPOINT_CIRCUIT_MIN_SAMPLES = max(int(os.getenv("ASR_ENDPOINT_CIRCUIT_MIN_SAMPLES", "5")), 1)
ASR_ENDPOINT_CIRCUIT_OPEN_SECONDS = max(float(os.getenv("ASR_ENDPOINT_CIRCUIT_OPEN_SECONDS", "15")), 1.0)
ASR_ENDPOINT_CIRCUIT_RETRY_AFTER_SECONDS = max(float(os.getenv("ASR_ENDPOINT_CIRCUIT_RETRY_AFTER_SECONDS", "2")), 1.0)
ASR_ENDPOINT_CIRCUIT_MAX_EVENTS = max(int(os.getenv("ASR_ENDPOINT_CIRCUIT_MAX_EVENTS", "128")), 8)


@dataclass(frozen=True)
class AsrQueueConfig:
    max_concurrency: int
    max_waiting: int
    timeout_seconds: float
    retry_after_seconds: float


@dataclass
class AsrEndpointQueue:
    condition: asyncio.Condition
    active: int = 0
    waiting: int = 0
    completed_latencies: Deque[float] | None = None


@dataclass(frozen=True)
class AsrBackend:
    endpoint: str
    base_url: str


@dataclass(frozen=True)
class AsrQueuePermit:
    endpoint: str
    language: str
    queued_seconds: float
    started_at: float
    config: AsrQueueConfig
    enabled: bool = True


@dataclass(frozen=True)
class AsrBackendRuntimeStatus:
    endpoint: str
    fetched_at: float
    ok: bool
    active_requests: int = 0
    oldest_active_seconds: float = 0.0
    recent_p95_completed_seconds: float = 0.0
    recent_average_completed_seconds: float = 0.0
    recent_completed: int = 0
    recent_failed: int = 0
    recent_cancelled: int = 0
    system_load_1m: float = 0.0
    system_cpu_count: int = 1
    error: str | None = None


@dataclass
class AsrBackendProtectionState:
    overload_started_at: float | None = None
    protected: bool = False
    last_reason: str = ""


@dataclass
class AsrEndpointCircuitState:
    events: Deque[tuple[float, bool, bool]] = field(default_factory=lambda: deque(maxlen=ASR_ENDPOINT_CIRCUIT_MAX_EVENTS))
    consecutive_timeouts: int = 0
    consecutive_failures: int = 0
    opened_until: float = 0.0
    half_open_inflight: bool = False
    last_reason: str = ""


@dataclass(frozen=True)
class AsrCircuitPermit:
    endpoint: str
    group: str
    language: str
    half_open: bool = False
    enabled: bool = True


_asr_endpoint_queues: dict[str, AsrEndpointQueue] = {}
_asr_backend_rr_index: dict[str, int] = {}
_asr_backend_status_cache: dict[str, AsrBackendRuntimeStatus] = {}
_asr_backend_protection_state: dict[str, AsrBackendProtectionState] = {}
_asr_endpoint_circuits: dict[str, AsrEndpointCircuitState] = {}


def _consume_background_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("[ASR] background task finished after cancellation: %s", exc)


async def _cancel_task_with_grace(task: asyncio.Task[Any], label: str) -> None:
    if task.done():
        _consume_background_task_result(task)
        return

    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=ASR_DOWNSTREAM_CANCEL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "[%s] cancellation cleanup exceeded %.3fs; releasing caller path",
            label,
            ASR_DOWNSTREAM_CANCEL_GRACE_SECONDS,
        )
        task.add_done_callback(_consume_background_task_result)
    except asyncio.CancelledError:
        task.add_done_callback(_consume_background_task_result)
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("[%s] cancelled task finished with error during cleanup: %s", label, exc)


async def _run_cancel_check(cancel_check: Optional[CancelCheck]) -> None:
    if cancel_check is not None:
        await cancel_check()


async def _await_with_cancel(
    awaitable: Awaitable[T],
    cancel_check: Optional[CancelCheck],
    label: str,
) -> T:
    if cancel_check is None:
        return await awaitable

    await _run_cancel_check(cancel_check)
    worker = asyncio.create_task(awaitable)
    try:
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=ASR_CANCEL_CHECK_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                try:
                    await _run_cancel_check(cancel_check)
                except ClientDisconnectedError:
                    await _cancel_task_with_grace(worker, label)
                    logger.info("[%s] cancelled because client disconnected", label)
                    raise
            except ClientDisconnectedError:
                await _cancel_task_with_grace(worker, label)
                logger.info("[%s] cancelled because client disconnected", label)
                raise
    finally:
        if not worker.done():
            await _cancel_task_with_grace(worker, label)


async def _await_with_hard_timeout(
    awaitable: Awaitable[T],
    timeout_seconds: float | None,
    label: str,
) -> T:
    if timeout_seconds is None or timeout_seconds <= 0:
        return await awaitable

    worker = asyncio.create_task(awaitable)
    try:
        return await asyncio.wait_for(asyncio.shield(worker), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        await _cancel_task_with_grace(worker, label)
        if worker.done() and not worker.cancelled():
            logger.warning("[%s] hard timeout fired after worker completed; returning completed result", label)
            return worker.result()
        raise
    except asyncio.CancelledError:
        await _cancel_task_with_grace(worker, label)
        raise


async def _post_with_cancel(
    client: httpx.AsyncClient,
    url: str,
    *,
    cancel_check: Optional[CancelCheck] = None,
    label: str,
    **kwargs,
) -> httpx.Response:
    await _run_cancel_check(cancel_check)
    response = await _await_with_cancel(
        client.post(url, **kwargs),
        cancel_check,
        label,
    )
    await _run_cancel_check(cancel_check)
    return response


def _build_asr_http_timeout(total_timeout_seconds: float) -> httpx.Timeout:
    total_timeout = max(float(total_timeout_seconds or 0), 0.05)
    return httpx.Timeout(
        timeout=total_timeout,
        connect=min(ASR_HTTP_CONNECT_TIMEOUT_SECONDS, total_timeout),
        read=total_timeout,
        write=min(ASR_HTTP_WRITE_TIMEOUT_SECONDS, total_timeout),
        pool=min(ASR_HTTP_POOL_TIMEOUT_SECONDS, total_timeout),
    )


def _asr_hard_timeout_seconds(downstream_timeout_seconds: float) -> float:
    return max(float(downstream_timeout_seconds or 0), 0.05) + ASR_HTTP_CLOSE_TIMEOUT_SECONDS + ASR_HARD_TIMEOUT_GRACE_SECONDS


def _asr_no_keepalive_limits() -> httpx.Limits:
    return httpx.Limits(max_keepalive_connections=0)


def _asr_backend_root_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def _asr_backend_status_url(base_url: str) -> str:
    return f"{_asr_backend_root_url(base_url)}/api/asr/status"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_asr_backend_status(
    backend: AsrBackend,
    payload: Any,
    fetched_at: float,
) -> AsrBackendRuntimeStatus:
    data = _as_mapping(payload)
    recent = _as_mapping(data.get("recent"))
    system = _as_mapping(data.get("system"))
    return AsrBackendRuntimeStatus(
        endpoint=backend.endpoint,
        fetched_at=fetched_at,
        ok=True,
        active_requests=max(_as_int(data.get("activeRequests")), 0),
        oldest_active_seconds=max(_as_float(data.get("oldestActiveSeconds")), 0.0),
        recent_p95_completed_seconds=max(_as_float(recent.get("p95CompletedSeconds")), 0.0),
        recent_average_completed_seconds=max(_as_float(recent.get("averageCompletedSeconds")), 0.0),
        recent_completed=max(_as_int(recent.get("completed")), 0),
        recent_failed=max(_as_int(recent.get("failed")), 0),
        recent_cancelled=max(_as_int(recent.get("cancelled")), 0),
        system_load_1m=max(_as_float(system.get("loadAverage1m")), 0.0),
        system_cpu_count=max(_as_int(system.get("cpuCount"), 1), 1),
    )


async def _fetch_asr_backend_status(backend: AsrBackend) -> AsrBackendRuntimeStatus:
    fetched_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=ASR_BACKEND_STATUS_TIMEOUT_SECONDS,
            limits=_asr_no_keepalive_limits(),
            headers={"Connection": "close"},
        ) as client:
            response = await client.get(_asr_backend_status_url(backend.base_url))
        response.raise_for_status()
        return _parse_asr_backend_status(backend, response.json(), fetched_at)
    except Exception as exc:  # pylint: disable=broad-except
        return AsrBackendRuntimeStatus(
            endpoint=backend.endpoint,
            fetched_at=fetched_at,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _get_asr_backend_status(backend: AsrBackend) -> AsrBackendRuntimeStatus:
    now = time.perf_counter()
    cached = _asr_backend_status_cache.get(backend.endpoint)
    if cached is not None and now - cached.fetched_at <= ASR_BACKEND_STATUS_TTL_SECONDS:
        return cached

    status = await _fetch_asr_backend_status(backend)
    _asr_backend_status_cache[backend.endpoint] = status
    return status


def _asr_backend_status_detail(status: AsrBackendRuntimeStatus) -> dict[str, Any]:
    return {
        "endpoint": status.endpoint,
        "ok": status.ok,
        "activeRequests": status.active_requests,
        "oldestActiveSeconds": round(status.oldest_active_seconds, 3),
        "recentP95CompletedSeconds": round(status.recent_p95_completed_seconds, 3),
        "recentAverageCompletedSeconds": round(status.recent_average_completed_seconds, 3),
        "recentCompleted": status.recent_completed,
        "recentFailed": status.recent_failed,
        "recentCancelled": status.recent_cancelled,
        "systemLoad1m": round(status.system_load_1m, 3),
        "systemCpuCount": status.system_cpu_count,
        "error": status.error,
    }


def _asr_backend_status_is_overloaded(status: AsrBackendRuntimeStatus) -> bool:
    if not status.ok:
        return False
    return (
        status.active_requests >= ASR_BACKEND_STATUS_OVERLOAD_ACTIVE_REQUESTS
        and status.oldest_active_seconds >= ASR_BACKEND_STATUS_OVERLOAD_OLDEST_SECONDS
    )


def _update_asr_backend_protection(status: AsrBackendRuntimeStatus) -> tuple[bool, float, str]:
    state = _asr_backend_protection_state.setdefault(status.endpoint, AsrBackendProtectionState())
    now = time.perf_counter()

    if not _asr_backend_status_is_overloaded(status):
        if (
            status.active_requests <= ASR_BACKEND_STATUS_RECOVERY_ACTIVE_REQUESTS
            or status.oldest_active_seconds <= ASR_BACKEND_STATUS_RECOVERY_OLDEST_SECONDS
            or not status.ok
        ):
            state.overload_started_at = None
            state.protected = False
            state.last_reason = ""
        return state.protected, 0.0, state.last_reason

    if state.overload_started_at is None:
        state.overload_started_at = now
    overload_seconds = max(now - state.overload_started_at, 0.0)
    state.last_reason = (
        f"activeRequests={status.active_requests} oldestActiveSeconds={status.oldest_active_seconds:.3f}"
    )
    if overload_seconds >= ASR_BACKEND_STATUS_OVERLOAD_MIN_SECONDS:
        state.protected = True
    return state.protected, overload_seconds, state.last_reason


def _asr_backend_status_rejection(
    endpoint_group: str,
    language: str,
    protected: Sequence[tuple[AsrBackend, AsrBackendRuntimeStatus, float, str]],
) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "asr_backend_overloaded",
            "engine": endpoint_group,
            "language": language,
            "backends": [
                {
                    **_asr_backend_status_detail(status),
                    "overloadSeconds": round(overload_seconds, 3),
                    "reason": reason,
                }
                for _, status, overload_seconds, reason in protected
            ],
            "overloadActiveRequests": ASR_BACKEND_STATUS_OVERLOAD_ACTIVE_REQUESTS,
            "overloadOldestSeconds": ASR_BACKEND_STATUS_OVERLOAD_OLDEST_SECONDS,
            "overloadMinSeconds": ASR_BACKEND_STATUS_OVERLOAD_MIN_SECONDS,
        },
        headers={"Retry-After": str(max(1, int(ASR_QUEUE_DEFAULT_RETRY_AFTER_SECONDS + 0.999)))},
    )


async def _filter_asr_backends_by_runtime_status(
    endpoint_group: str,
    backends: Sequence[AsrBackend],
    language: str,
) -> tuple[list[AsrBackend], list[tuple[AsrBackend, AsrBackendRuntimeStatus, float, str]]]:
    if not ASR_BACKEND_STATUS_ENABLED or endpoint_group != "whisper" or not backends:
        return list(backends), []

    statuses = await asyncio.gather(*(_get_asr_backend_status(backend) for backend in backends))
    available: list[AsrBackend] = []
    protected: list[tuple[AsrBackend, AsrBackendRuntimeStatus, float, str]] = []
    for backend, status in zip(backends, statuses):
        protected_now, overload_seconds, reason = _update_asr_backend_protection(status)
        if protected_now:
            protected.append((backend, status, overload_seconds, reason))
        else:
            available.append(backend)

    if protected:
        logger.warning(
            "[ASR_BACKEND_STATUS] protected overloaded backend(s). group=%s language=%s protected=%s available=%s",
            endpoint_group,
            language,
            [
                {
                    "endpoint": backend.endpoint,
                    "active": status.active_requests,
                    "oldest": round(status.oldest_active_seconds, 3),
                    "overloadSeconds": round(overload_seconds, 3),
                }
                for backend, status, overload_seconds, _ in protected
            ],
            [backend.endpoint for backend in available],
        )

    if not available:
        raise _asr_backend_status_rejection(endpoint_group, language, protected)
    return available, protected


def _asr_circuit_state(endpoint: str) -> AsrEndpointCircuitState:
    state = _asr_endpoint_circuits.get(endpoint)
    if state is None:
        state = AsrEndpointCircuitState()
        _asr_endpoint_circuits[endpoint] = state
    return state


def _prune_asr_circuit_events(state: AsrEndpointCircuitState, now: float) -> None:
    cutoff = now - ASR_ENDPOINT_CIRCUIT_WINDOW_SECONDS
    while state.events and state.events[0][0] < cutoff:
        state.events.popleft()


def _asr_circuit_snapshot(endpoint: str, now: float | None = None) -> dict[str, Any]:
    current = time.perf_counter() if now is None else now
    state = _asr_circuit_state(endpoint)
    _prune_asr_circuit_events(state, current)
    total = len(state.events)
    timeout_count = sum(1 for _, _, timed_out in state.events if timed_out)
    failure_count = sum(1 for _, failed, _ in state.events if failed)
    return {
        "endpoint": endpoint,
        "state": "open" if state.opened_until > current else ("half_open" if state.opened_until > 0 else "closed"),
        "openedRemainingSeconds": round(max(state.opened_until - current, 0.0), 3),
        "halfOpenInflight": state.half_open_inflight,
        "windowSeconds": ASR_ENDPOINT_CIRCUIT_WINDOW_SECONDS,
        "samples": total,
        "timeouts": timeout_count,
        "failures": failure_count,
        "timeoutRate": round(timeout_count / total, 3) if total else 0.0,
        "consecutiveTimeouts": state.consecutive_timeouts,
        "consecutiveFailures": state.consecutive_failures,
        "lastReason": state.last_reason,
    }


def _asr_circuit_rejection(
    endpoint_group: str,
    language: str,
    endpoints: Sequence[str],
) -> HTTPException:
    now = time.perf_counter()
    return HTTPException(
        status_code=503,
        detail={
            "error": "asr_endpoint_circuit_open",
            "engine": endpoint_group,
            "language": language,
            "endpoints": [_asr_circuit_snapshot(endpoint, now) for endpoint in endpoints],
        },
        headers={"Retry-After": str(max(1, int(ASR_ENDPOINT_CIRCUIT_RETRY_AFTER_SECONDS + 0.999)))},
    )


def _asr_circuit_endpoint_available(endpoint: str, now: float | None = None) -> bool:
    if not ASR_ENDPOINT_CIRCUIT_BREAKER_ENABLED:
        return True
    current = time.perf_counter() if now is None else now
    state = _asr_circuit_state(endpoint)
    _prune_asr_circuit_events(state, current)
    if state.opened_until > current:
        return False
    if state.opened_until > 0 and state.half_open_inflight:
        return False
    return True


def _filter_asr_backends_by_circuit(
    endpoint_group: str,
    backends: Sequence[AsrBackend],
    language: str,
) -> list[AsrBackend]:
    if not ASR_ENDPOINT_CIRCUIT_BREAKER_ENABLED:
        return list(backends)

    now = time.perf_counter()
    available = [backend for backend in backends if _asr_circuit_endpoint_available(backend.endpoint, now)]
    if available:
        blocked = [backend.endpoint for backend in backends if backend not in available]
        if blocked:
            logger.warning(
                "[ASR_CIRCUIT] skipped open backend(s). group=%s language=%s blocked=%s available=%s",
                endpoint_group,
                language,
                [_asr_circuit_snapshot(endpoint, now) for endpoint in blocked],
                [backend.endpoint for backend in available],
            )
        return available

    raise _asr_circuit_rejection(endpoint_group, language, [backend.endpoint for backend in backends])


def _acquire_asr_circuit_permit(endpoint: str, endpoint_group: str, language: str) -> AsrCircuitPermit:
    if not ASR_ENDPOINT_CIRCUIT_BREAKER_ENABLED:
        return AsrCircuitPermit(endpoint=endpoint, group=endpoint_group, language=language, enabled=False)

    now = time.perf_counter()
    state = _asr_circuit_state(endpoint)
    _prune_asr_circuit_events(state, now)
    if state.opened_until > now:
        raise _asr_circuit_rejection(endpoint_group, language, [endpoint])

    half_open = state.opened_until > 0
    if half_open:
        if state.half_open_inflight:
            raise _asr_circuit_rejection(endpoint_group, language, [endpoint])
        state.half_open_inflight = True
        logger.info("[ASR_CIRCUIT] half-open probe started. endpoint=%s group=%s language=%s", endpoint, endpoint_group, language)

    return AsrCircuitPermit(endpoint=endpoint, group=endpoint_group, language=language, half_open=half_open)


def _release_asr_circuit_probe(permit: AsrCircuitPermit | None) -> None:
    if permit is None or not permit.enabled or not permit.half_open:
        return
    _asr_circuit_state(permit.endpoint).half_open_inflight = False


def _record_asr_circuit_result(
    permit: AsrCircuitPermit | None,
    *,
    success: bool,
    timed_out: bool,
    error: str | None = None,
) -> None:
    if permit is None or not permit.enabled:
        return

    now = time.perf_counter()
    state = _asr_circuit_state(permit.endpoint)
    _prune_asr_circuit_events(state, now)
    if permit.half_open:
        state.half_open_inflight = False

    if success:
        state.consecutive_timeouts = 0
        state.consecutive_failures = 0
        state.opened_until = 0.0
        state.last_reason = ""
        state.events.append((now, False, False))
        if permit.half_open:
            logger.info("[ASR_CIRCUIT] half-open probe succeeded. endpoint=%s group=%s", permit.endpoint, permit.group)
        return

    state.consecutive_failures += 1
    if timed_out:
        state.consecutive_timeouts += 1
    else:
        state.consecutive_timeouts = 0
    state.events.append((now, True, timed_out))
    _prune_asr_circuit_events(state, now)

    total = len(state.events)
    timeout_count = sum(1 for _, _, event_timed_out in state.events if event_timed_out)
    timeout_rate = timeout_count / total if total else 0.0
    should_open = (
        permit.half_open
        or state.consecutive_timeouts >= ASR_ENDPOINT_CIRCUIT_CONSECUTIVE_TIMEOUTS
        or state.consecutive_failures >= ASR_ENDPOINT_CIRCUIT_CONSECUTIVE_FAILURES
        or (
            total >= ASR_ENDPOINT_CIRCUIT_MIN_SAMPLES
            and timeout_rate >= ASR_ENDPOINT_CIRCUIT_TIMEOUT_RATE
        )
    )
    if not should_open:
        return

    reason = (
        f"error={error or 'unknown'} consecutiveTimeouts={state.consecutive_timeouts} "
        f"consecutiveFailures={state.consecutive_failures} timeoutRate={timeout_rate:.3f} samples={total}"
    )
    state.opened_until = now + ASR_ENDPOINT_CIRCUIT_OPEN_SECONDS
    state.last_reason = reason
    logger.warning(
        "[ASR_CIRCUIT] opened endpoint circuit. endpoint=%s group=%s language=%s openSeconds=%.3f reason=%s",
        permit.endpoint,
        permit.group,
        permit.language,
        ASR_ENDPOINT_CIRCUIT_OPEN_SECONDS,
        reason,
    )


async def _close_asr_resource(awaitable: Awaitable[Any], label: str) -> None:
    cleanup_task = asyncio.create_task(awaitable)
    try:
        await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=ASR_HTTP_CLOSE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "[%s] HTTP close cleanup exceeded %.3fs",
            label,
            ASR_HTTP_CLOSE_TIMEOUT_SECONDS,
        )
        cleanup_task.add_done_callback(_consume_background_task_result)
    except asyncio.CancelledError:
        cleanup_task.add_done_callback(_consume_background_task_result)
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("[%s] HTTP close cleanup failed: %s", label, exc)


async def _close_response_quietly(response: httpx.Response | None, label: str) -> None:
    if response is None:
        return
    await _close_asr_resource(response.aclose(), f"{label}.response")


async def _close_client_quietly(client: httpx.AsyncClient | None, label: str) -> None:
    if client is None:
        return
    await _close_asr_resource(client.aclose(), f"{label}.client")


async def _post_asr_form_and_extract_text(
    url: str,
    *,
    files: dict[str, Any],
    data: dict[str, Any] | None,
    timeout_seconds: float,
    cancel_check: Optional[CancelCheck],
    label: str,
    engine: str | None = None,
    endpoint_name: str | None = None,
    language: str | None = None,
) -> str:
    client: httpx.AsyncClient | None = None
    response: httpx.Response | None = None
    started_at = time.perf_counter()
    stage_started_at = started_at
    try:
        client = httpx.AsyncClient(
            timeout=_build_asr_http_timeout(timeout_seconds),
            limits=_asr_no_keepalive_limits(),
            headers={"Connection": "close"},
        )
        stage_started_at = _log_asr_http_stage(
            label=label,
            stage="request_start",
            url=url,
            started_at=started_at,
            stage_started_at=stage_started_at,
            engine=engine,
            endpoint_name=endpoint_name,
            language=language,
        )
        request = client.build_request("POST", url, files=files, data=data)
        response = await _await_with_cancel(client.send(request, stream=True), cancel_check, label)
        stage_started_at = _log_asr_http_stage(
            label=label,
            stage="headers_received",
            url=url,
            started_at=started_at,
            stage_started_at=stage_started_at,
            engine=engine,
            endpoint_name=endpoint_name,
            language=language,
            extra=f"status={response.status_code}",
        )
        response.raise_for_status()
        content = await _await_with_cancel(response.aread(), cancel_check, label)
        stage_started_at = _log_asr_http_stage(
            label=label,
            stage="body_read_finished",
            url=url,
            started_at=started_at,
            stage_started_at=stage_started_at,
            engine=engine,
            endpoint_name=endpoint_name,
            language=language,
            extra=f"bytes={len(content)}",
        )
        try:
            text = _extract_asr_response_text_from_content(response, content, label)
        except Exception as parse_exc:
            _log_asr_http_stage(
                label=label,
                stage="json_parse_failed",
                url=url,
                started_at=started_at,
                stage_started_at=stage_started_at,
                engine=engine,
                endpoint_name=endpoint_name,
                language=language,
                extra=f"error={type(parse_exc).__name__}: {parse_exc}",
            )
            raise
        _log_asr_http_stage(
            label=label,
            stage="json_parse_finished",
            url=url,
            started_at=started_at,
            stage_started_at=stage_started_at,
            engine=engine,
            endpoint_name=endpoint_name,
            language=language,
        )
        return text
    finally:
        close_started_at = time.perf_counter()
        await _close_response_quietly(response, label)
        _log_asr_http_stage(
            label=label,
            stage="response_close_finished",
            url=url,
            started_at=started_at,
            stage_started_at=close_started_at,
            engine=engine,
            endpoint_name=endpoint_name,
            language=language,
        )
        close_started_at = time.perf_counter()
        await _close_client_quietly(client, label)
        _log_asr_http_stage(
            label=label,
            stage="client_close_finished",
            url=url,
            started_at=started_at,
            stage_started_at=close_started_at,
            engine=engine,
            endpoint_name=endpoint_name,
            language=language,
        )


def _asr_endpoint_env_prefix(endpoint: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", endpoint.upper()).strip("_") or "ENDPOINT"


def _asr_endpoint_group(endpoint: str) -> str:
    if ":" in endpoint:
        return endpoint.split(":", 1)[0]
    match = re.match(r"^(.+)-\d+$", endpoint)
    return match.group(1) if match else endpoint


def _asr_endpoint_env_keys(endpoint: str, name: str) -> list[str]:
    exact_prefix = _asr_endpoint_env_prefix(endpoint)
    keys = [f"ASR_QUEUE_{exact_prefix}_{name}"]
    group_prefix = _asr_endpoint_env_prefix(_asr_endpoint_group(endpoint))
    group_key = f"ASR_QUEUE_{group_prefix}_{name}"
    if group_key not in keys:
        keys.append(group_key)
    return keys


def _asr_endpoint_int_env(endpoint: str, name: str, default_value: int) -> int:
    for key in _asr_endpoint_env_keys(endpoint, name):
        if key in os.environ:
            return max(int(os.getenv(key, str(default_value))), 0)
    return max(int(default_value), 0)


def _asr_endpoint_float_env(endpoint: str, name: str, default_value: float) -> float:
    for key in _asr_endpoint_env_keys(endpoint, name):
        if key in os.environ:
            return max(float(os.getenv(key, str(default_value))), 0.0)
    return max(float(default_value), 0.0)


def _asr_queue_config(endpoint: str) -> AsrQueueConfig:
    max_concurrency = max(_asr_endpoint_int_env(endpoint, "MAX_CONCURRENCY", ASR_QUEUE_DEFAULT_MAX_CONCURRENCY), 1)
    max_waiting = _asr_endpoint_int_env(endpoint, "MAX_WAITING", ASR_QUEUE_DEFAULT_MAX_WAITING)
    timeout_seconds = max(_asr_endpoint_float_env(endpoint, "TIMEOUT_SECONDS", ASR_QUEUE_DEFAULT_TIMEOUT_SECONDS), 0.05)
    retry_after_seconds = max(_asr_endpoint_float_env(endpoint, "RETRY_AFTER_SECONDS", ASR_QUEUE_DEFAULT_RETRY_AFTER_SECONDS), 1.0)
    return AsrQueueConfig(
        max_concurrency=max_concurrency,
        max_waiting=max_waiting,
        timeout_seconds=timeout_seconds,
        retry_after_seconds=retry_after_seconds,
    )


def _asr_endpoint_queue(endpoint: str) -> AsrEndpointQueue:
    queue = _asr_endpoint_queues.get(endpoint)
    if queue is None:
        queue = AsrEndpointQueue(
            condition=asyncio.Condition(),
            completed_latencies=deque(maxlen=ASR_QUEUE_LATENCY_WINDOW),
        )
        _asr_endpoint_queues[endpoint] = queue
    return queue


def _asr_queue_rejection(
    endpoint: str,
    language: str,
    reason: str,
    config: AsrQueueConfig,
    active: int,
    waiting: int,
) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": reason,
            "engine": endpoint,
            "language": language,
            "active": active,
            "waiting": waiting,
            "maxConcurrency": config.max_concurrency,
            "maxWaiting": config.max_waiting,
            "queueTimeoutSeconds": config.timeout_seconds,
        },
        headers={"Retry-After": str(max(1, int(config.retry_after_seconds + 0.999)))},
    )


def _asr_backend_group_rejection(
    endpoint_group: str,
    language: str,
    reason: str,
    snapshots: Sequence[tuple[AsrBackend, AsrQueueConfig, int, int]],
) -> HTTPException:
    active = sum(item[2] for item in snapshots)
    waiting = sum(item[3] for item in snapshots)
    max_concurrency = sum(item[1].max_concurrency for item in snapshots)
    max_waiting = sum(item[1].max_waiting for item in snapshots)
    retry_after = max((item[1].retry_after_seconds for item in snapshots), default=ASR_QUEUE_DEFAULT_RETRY_AFTER_SECONDS)
    return HTTPException(
        status_code=503,
        detail={
            "error": reason,
            "engine": endpoint_group,
            "language": language,
            "active": active,
            "waiting": waiting,
            "maxConcurrency": max_concurrency,
            "maxWaiting": max_waiting,
            "backends": [
                {
                    "endpoint": backend.endpoint,
                    "active": backend_active,
                    "waiting": backend_waiting,
                    "maxConcurrency": config.max_concurrency,
                    "maxWaiting": config.max_waiting,
                }
                for backend, config, backend_active, backend_waiting in snapshots
            ],
            "queueTimeoutSeconds": max(
                (item[1].timeout_seconds for item in snapshots),
                default=ASR_QUEUE_DEFAULT_TIMEOUT_SECONDS,
            ),
        },
        headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
    )


def _rotate_asr_backends(endpoint_group: str, backends: Sequence[AsrBackend]) -> list[AsrBackend]:
    if not backends:
        return []
    start_index = _asr_backend_rr_index.get(endpoint_group, 0) % len(backends)
    _asr_backend_rr_index[endpoint_group] = start_index + 1
    ordered = list(backends)
    return ordered[start_index:] + ordered[:start_index]


async def _snapshot_asr_backends(backends: Sequence[AsrBackend]) -> list[tuple[AsrBackend, AsrQueueConfig, int, int]]:
    snapshots: list[tuple[AsrBackend, AsrQueueConfig, int, int]] = []
    for backend in backends:
        config = _asr_queue_config(backend.endpoint)
        queue = _asr_endpoint_queue(backend.endpoint)
        async with queue.condition:
            snapshots.append((backend, config, queue.active, queue.waiting))
    return snapshots


def _backend_load_score(snapshot: tuple[AsrBackend, AsrQueueConfig, int, int]) -> tuple[float, int, int]:
    _, config, active, waiting = snapshot
    return (active / max(config.max_concurrency, 1), waiting, active)


async def _try_acquire_asr_backend_now(
    endpoint_group: str,
    backends: Sequence[AsrBackend],
    language: str,
    queued_at: float,
) -> tuple[AsrBackend, AsrQueuePermit] | None:
    snapshots = await _snapshot_asr_backends(backends)
    for backend, config, _, _ in sorted(snapshots, key=_backend_load_score):
        queue = _asr_endpoint_queue(backend.endpoint)
        async with queue.condition:
            if queue.active >= config.max_concurrency:
                continue
            queue.active += 1
            queued_seconds = time.perf_counter() - queued_at
            if queued_seconds >= 0.25:
                logger.info(
                    "[ASR_QUEUE_LIMIT] acquired queued backend slot. group=%s endpoint=%s language=%s queued=%.3fs active=%s waiting=%s max_concurrency=%s max_waiting=%s",
                    endpoint_group,
                    backend.endpoint,
                    language,
                    queued_seconds,
                    queue.active,
                    queue.waiting,
                    config.max_concurrency,
                    config.max_waiting,
                )
            return backend, AsrQueuePermit(
                backend.endpoint,
                language,
                queued_seconds,
                time.perf_counter(),
                config,
            )
    return None


async def _release_waiting_backend(waiting_endpoint: str | None) -> None:
    if not waiting_endpoint:
        return
    queue = _asr_endpoint_queue(waiting_endpoint)
    async with queue.condition:
        queue.waiting = max(0, queue.waiting - 1)
        queue.condition.notify(1)


async def _acquire_asr_backend_slot(
    endpoint_group: str,
    backends: Sequence[AsrBackend],
    language: str,
    cancel_check: Optional[CancelCheck],
) -> tuple[AsrBackend, AsrQueuePermit]:
    if not backends:
        raise HTTPException(
            status_code=503,
            detail={"error": "asr_backend_unavailable", "engine": endpoint_group, "language": language},
        )
    backends = tuple(_filter_asr_backends_by_circuit(endpoint_group, backends, language))
    available_backends, _ = await _filter_asr_backends_by_runtime_status(endpoint_group, backends, language)
    backends = tuple(available_backends)
    if len(backends) == 1:
        backend = backends[0]
        permit = await _acquire_asr_endpoint_slot(backend.endpoint, language, cancel_check)
        return backend, permit
    if not ASR_QUEUE_LIMIT_ENABLED:
        backend = _rotate_asr_backends(endpoint_group, backends)[0]
        return backend, AsrQueuePermit(
            endpoint=backend.endpoint,
            language=language,
            queued_seconds=0.0,
            started_at=time.perf_counter(),
            config=_asr_queue_config(backend.endpoint),
            enabled=False,
        )

    await _run_cancel_check(cancel_check)
    ordered_backends = _rotate_asr_backends(endpoint_group, backends)
    queued_at = time.perf_counter()
    waiting_endpoint: str | None = None

    try:
        while True:
            acquired = await _try_acquire_asr_backend_now(endpoint_group, ordered_backends, language, queued_at)
            if acquired is not None:
                await _release_waiting_backend(waiting_endpoint)
                waiting_endpoint = None
                return acquired

            snapshots = await _snapshot_asr_backends(ordered_backends)
            timeout_seconds = max((item[1].timeout_seconds for item in snapshots), default=ASR_QUEUE_DEFAULT_TIMEOUT_SECONDS)
            elapsed = time.perf_counter() - queued_at
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                logger.warning(
                    "[ASR_QUEUE_LIMIT] reject request because backend group queue wait timed out. group=%s language=%s elapsed=%.3fs",
                    endpoint_group,
                    language,
                    elapsed,
                )
                raise _asr_backend_group_rejection(endpoint_group, language, "asr_queue_timeout", snapshots)

            if waiting_endpoint is None:
                waiting_candidates = [
                    snapshot for snapshot in snapshots if snapshot[3] < snapshot[1].max_waiting
                ]
                if not waiting_candidates:
                    logger.warning(
                        "[ASR_QUEUE_LIMIT] reject request because backend group queue is full. group=%s language=%s",
                        endpoint_group,
                        language,
                    )
                    raise _asr_backend_group_rejection(endpoint_group, language, "asr_queue_full", snapshots)
                backend, config, _, _ = min(waiting_candidates, key=lambda item: (item[3], item[2] / max(item[1].max_concurrency, 1)))
                queue = _asr_endpoint_queue(backend.endpoint)
                async with queue.condition:
                    if queue.active < config.max_concurrency:
                        queue.active += 1
                        return backend, AsrQueuePermit(
                            backend.endpoint,
                            language,
                            time.perf_counter() - queued_at,
                            time.perf_counter(),
                            config,
                        )
                    if queue.waiting >= config.max_waiting:
                        continue
                    queue.waiting += 1
                    waiting_endpoint = backend.endpoint

            wait_queue = _asr_endpoint_queue(waiting_endpoint)
            async with wait_queue.condition:
                try:
                    await asyncio.wait_for(wait_queue.condition.wait(), timeout=min(remaining, 0.25))
                except asyncio.TimeoutError:
                    pass
            await _run_cancel_check(cancel_check)
    finally:
        await _release_waiting_backend(waiting_endpoint)


async def _acquire_asr_endpoint_slot(
    endpoint: str,
    language: str,
    cancel_check: Optional[CancelCheck],
) -> AsrQueuePermit:
    if not ASR_QUEUE_LIMIT_ENABLED:
        return AsrQueuePermit(
            endpoint=endpoint,
            language=language,
            queued_seconds=0.0,
            started_at=time.perf_counter(),
            config=_asr_queue_config(endpoint),
            enabled=False,
        )

    await _run_cancel_check(cancel_check)
    config = _asr_queue_config(endpoint)
    queue = _asr_endpoint_queue(endpoint)
    queued_at = time.perf_counter()
    counted_waiting = False

    async with queue.condition:
        if queue.active < config.max_concurrency:
            queue.active += 1
            return AsrQueuePermit(endpoint, language, 0.0, time.perf_counter(), config)

        if queue.waiting >= config.max_waiting:
            logger.warning(
                "[ASR_QUEUE_LIMIT] reject request because queue is full. endpoint=%s language=%s active=%s waiting=%s max_concurrency=%s max_waiting=%s",
                endpoint,
                language,
                queue.active,
                queue.waiting,
                config.max_concurrency,
                config.max_waiting,
            )
            raise _asr_queue_rejection(endpoint, language, "asr_queue_full", config, queue.active, queue.waiting)

        queue.waiting += 1
        counted_waiting = True

        try:
            while queue.active >= config.max_concurrency:
                remaining = config.timeout_seconds - (time.perf_counter() - queued_at)
                if remaining <= 0:
                    logger.warning(
                        "[ASR_QUEUE_LIMIT] reject request because queue wait timed out. endpoint=%s language=%s active=%s waiting=%s max_concurrency=%s max_waiting=%s timeout=%.3fs",
                        endpoint,
                        language,
                        queue.active,
                        queue.waiting,
                        config.max_concurrency,
                        config.max_waiting,
                        config.timeout_seconds,
                    )
                    raise _asr_queue_rejection(endpoint, language, "asr_queue_timeout", config, queue.active, queue.waiting)

                try:
                    await asyncio.wait_for(queue.condition.wait(), timeout=min(remaining, 0.25))
                except asyncio.TimeoutError:
                    pass
                await _run_cancel_check(cancel_check)

            queue.waiting -= 1
            counted_waiting = False
            queue.active += 1
            queued_seconds = time.perf_counter() - queued_at
            if queued_seconds >= 0.25:
                logger.info(
                    "[ASR_QUEUE_LIMIT] acquired queued slot. endpoint=%s language=%s queued=%.3fs active=%s waiting=%s max_concurrency=%s max_waiting=%s",
                    endpoint,
                    language,
                    queued_seconds,
                    queue.active,
                    queue.waiting,
                    config.max_concurrency,
                    config.max_waiting,
                )
            return AsrQueuePermit(endpoint, language, queued_seconds, time.perf_counter(), config)
        finally:
            if counted_waiting:
                queue.waiting = max(0, queue.waiting - 1)
                queue.condition.notify(1)


async def _release_asr_endpoint_slot(permit: AsrQueuePermit, *, record_latency: bool) -> None:
    if not permit.enabled:
        return

    finished_at = time.perf_counter()
    elapsed = finished_at - permit.started_at
    queue = _asr_endpoint_queue(permit.endpoint)
    async with queue.condition:
        queue.active = max(0, queue.active - 1)
        if record_latency and queue.completed_latencies is not None:
            queue.completed_latencies.append(elapsed)
        queue.condition.notify(1)

        active = queue.active
        waiting = queue.waiting

    if elapsed >= ASR_QUEUE_SLOW_LOG_SECONDS:
        logger.warning(
            "[ASR_QUEUE_LIMIT] slow ASR request completed. endpoint=%s language=%s elapsed=%.3fs queued=%.3fs active=%s waiting=%s max_concurrency=%s max_waiting=%s",
            permit.endpoint,
            permit.language,
            elapsed,
            permit.queued_seconds,
            active,
            waiting,
            permit.config.max_concurrency,
            permit.config.max_waiting,
        )


async def _run_asr_endpoint(
    endpoint: str,
    language: str,
    operation: Callable[[], Awaitable[T]],
    cancel_check: Optional[CancelCheck] = None,
    hard_timeout_seconds: float | None = None,
) -> T:
    circuit_permit: AsrCircuitPermit | None = _acquire_asr_circuit_permit(endpoint, _asr_endpoint_group(endpoint), language)
    try:
        permit = await _acquire_asr_endpoint_slot(endpoint, language, cancel_check)
    except Exception:
        _release_asr_circuit_probe(circuit_permit)
        raise
    record_latency = True
    try:
        result = await _await_with_hard_timeout(operation(), hard_timeout_seconds, f"ASR:{endpoint}")
        _record_asr_circuit_result(circuit_permit, success=True, timed_out=False)
        return result
    except ClientDisconnectedError:
        record_latency = False
        _release_asr_circuit_probe(circuit_permit)
        raise
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        _record_asr_circuit_result(
            circuit_permit,
            success=False,
            timed_out=True,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.warning(
            "[ASR_QUEUE_LIMIT] downstream ASR request timed out. endpoint=%s language=%s timeout=%.3fs queued=%.3fs",
            endpoint,
            language,
            float(hard_timeout_seconds or 0),
            permit.queued_seconds,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "asr_downstream_timeout",
                "engine": endpoint,
                "language": language,
                "timeoutSeconds": float(hard_timeout_seconds or 0),
            },
            headers={"Retry-After": str(max(1, int(permit.config.retry_after_seconds + 0.999)))},
        ) from exc
    except Exception as exc:
        _record_asr_circuit_result(
            circuit_permit,
            success=False,
            timed_out=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        await asyncio.shield(_release_asr_endpoint_slot(permit, record_latency=record_latency))


async def _run_asr_backend(
    endpoint_group: str,
    backends: Sequence[AsrBackend],
    language: str,
    operation: Callable[[AsrBackend], Awaitable[T]],
    cancel_check: Optional[CancelCheck] = None,
    hard_timeout_seconds: float | None = None,
) -> T:
    backend, permit = await _acquire_asr_backend_slot(endpoint_group, backends, language, cancel_check)
    circuit_permit: AsrCircuitPermit | None = None
    record_latency = True
    try:
        circuit_permit = _acquire_asr_circuit_permit(backend.endpoint, endpoint_group, language)
        logger.debug("[ASR] selected backend. group=%s endpoint=%s base_url=%s", endpoint_group, backend.endpoint, backend.base_url)
        result = await _await_with_hard_timeout(operation(backend), hard_timeout_seconds, f"ASR:{backend.endpoint}")
        _record_asr_circuit_result(circuit_permit, success=True, timed_out=False)
        return result
    except ClientDisconnectedError:
        record_latency = False
        _release_asr_circuit_probe(circuit_permit)
        raise
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        _record_asr_circuit_result(
            circuit_permit,
            success=False,
            timed_out=True,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.warning(
            "[ASR_QUEUE_LIMIT] downstream ASR request timed out. group=%s endpoint=%s language=%s timeout=%.3fs queued=%.3fs",
            endpoint_group,
            backend.endpoint,
            language,
            float(hard_timeout_seconds or 0),
            permit.queued_seconds,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "asr_downstream_timeout",
                "engine": endpoint_group,
                "endpoint": backend.endpoint,
                "language": language,
                "timeoutSeconds": float(hard_timeout_seconds or 0),
            },
            headers={"Retry-After": str(max(1, int(permit.config.retry_after_seconds + 0.999)))},
        ) from exc
    except Exception as exc:
        _record_asr_circuit_result(
            circuit_permit,
            success=False,
            timed_out=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        await asyncio.shield(_release_asr_endpoint_slot(permit, record_latency=record_latency))

def get_translators():
    global translators
    if translators is None:
        # 从环境变量获取地区设置，避免网络检查
        region = os.environ.get("translators_default_region", "CN")
        os.environ["translators_default_region"] = region
        import translators as _translators
        translators = _translators
    return translators


# --- 全局客户端和服务变量 ---
# 同步客户端（保留用于向后兼容）
whisperclient = OpenAI(api_key=settings.WHISPER_APIKEY, base_url=WHISPER_URL)
# 异步客户端（推荐使用，避免阻塞事件循环）
async_whisperclient = AsyncOpenAI(api_key=settings.WHISPER_APIKEY, base_url=WHISPER_URL)

glm_client = OpenAI(api_key="4c3d963619884fc69e3a02c581925691.mgFDmvFyfDWmGvub", base_url="https://open.bigmodel.cn/api/paas/v4/")
errorFilter = {}
whisperHallucinationFilterExact: dict[str, set[str]] = {}
# 硬编码的支持语言列表，不再动态从LibreTranslate获取
supportedLanguagesList = [
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "eo", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "he",
    "hi", "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb",
    "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sv", "th",
    "tl", "tr", "uk", "ur", "zh", "zt"
]

# --- 语言代码 (从 server.py 复制) ---
whisperSupportedLanguageList = ["af","am","ar","as","az","ba","be","bg","bn","bo","br","bs","ca","cs","cy","da","de","el","en","es","et","eu","fa","fi","fo","fr","gl","gu","ha","haw","he","hi","hr","ht","hu","hy","id","is","it","ja","jw","ka","kk","km","kn","ko","la","lb","ln","lo","lt","lv","mg","mi","mk","ml","mn","mr","ms","mt","my","ne","nl","nn","no","oc","pa","pl","ps","pt","ro","ru","sa","sd","si","sk","sl","sn","so","sq","sr","su","sv","sw","ta","te","tg","th","tk","tl","tr","tt","uk","ur","uz","vi","yi","yo","yue","zh"]
codeTochinese = {'af':'阿非利堪斯语','am':'阿姆哈拉语','ar':'阿拉伯语','as':'阿萨姆语','az':'阿塞拜疆语','ba':'巴什基尔语','be':'白俄罗斯语','bg':'保加利亚语','bn':'孟加拉语','bo':'藏语','br':'布列塔尼语','bs':'波斯尼亚语','ca':'加泰罗尼亚语','cs':'捷克语','cy':'威尔士语','da':'丹麦语','de':'德语','el':'希腊语','en':'英语','es':'西班牙语','et':'爱沙尼亚语','eu':'巴斯克语','fa':'波斯语','fi':'芬兰语','fo':'法罗语','fr':'法语','gl':'加利西亚语','gu':'古吉拉特语','ha':'豪萨语','haw':'夏威夷语','he':'希伯来语','hi':'印地语','hr':'克罗地亚语','ht':'海地克里奥尔语','hu':'匈牙利语','hy':'亚美尼亚语','id':'印尼语','is':'冰岛语','it':'意大利语','ja':'日语','jw':'爪哇语','ka':'格鲁吉亚语','kk':'哈萨克语','km':'高棉语','kn':'卡纳达语','ko':'韩语','la':'拉丁语','lb':'卢森堡语','ln':'林加拉语','lo':'老挝语','lt':'立陶宛语','lv':'拉脱维亚语','mg':'马达加斯加语','mi':'毛利语','mk':'马其顿语','ml':'马拉雅拉姆语','mn':'蒙古语','mr':'马拉地语','ms':'马来语','mt':'马耳他语','my':'缅甸语','ne':'尼泊尔语','nl':'荷兰语','nn':'新挪威语','no':'挪威语','oc':'奥克语','pa':'旁遮普语','pl':'波兰语','ps':'普什图语','pt':'葡萄牙语','ro':'罗马尼亚语','ru':'俄语','sa':'梵语','sd':'信德语','si':'僧伽罗语','sk':'斯洛伐克语','sl':'斯洛文尼亚语','sn':'修纳语','so':'索马里语','sq':'阿尔巴尼亚语','sr':'塞尔维亚语','su':'巽他语','sv':'瑞典语','sw':'斯瓦希里语','ta':'泰米尔语','te':'泰卢固语','tg':'塔吉克语','th':'泰语','tk':'土库曼语','tl':'他加禄语','tr':'土耳其语','tt':'鞑靼语','uk':'乌克兰语','ur':'乌尔都语','uz':'乌兹别克语','vi':'越南语','yi':'意第绪语','yo':'约鲁巴语','yue':'粤语','zh':'简体中文','zt':'繁体中文','eo':'世界语','ga':'爱尔兰语','nb':'挪威语'}
transalte_zt = {'bing':'zh-Hant','baidu':'cht','alibaba':'zh-TW','sogou':'zh-CHT','iciba':'cnt','itranslate':'zh-TW','papago':'zh-TW'}

ASR_ENGINE_ALIASES = {
    "firered": "firered",
    "fireredasr": "firered",
    "fireredasr2": "firered",
    "fireredasr2s": "firered",
    "fire-red": "firered",
    "fire-red-asr": "firered",
    "sensevoice": "sensevoice",
    "sense-voice": "sensevoice",
    "whisper": "whisper",
    "faster-whisper": "whisper",
    "faster_whisper": "whisper",
    "openai": "whisper",
    "whisper-trt": "whisper-trt",
    "whisper_trt": "whisper-trt",
    "trt": "whisper-trt",
}

CHINESE_ASR_LANGUAGES = {"zh", "zt"}
ASR_ENGINE_LANGUAGE_SUPPORT = {
    "firered": {"zh", "en"},
    "sensevoice": {"zh", "en", "ja", "ko"},
}
MULTILINGUAL_ASR_LANGUAGE = "multilingual"
FIRERED_MIN_AUDIO_SECONDS = 0.6
ASR_EMPTY_AUDIO_MARKERS = frozenset({"<sil>", "<blank>"})
ASR_SPECIAL_TOKEN_PATTERN = re.compile(r"<[^<>\s]+>")
ASR_MARKER_PADDING_PATTERN = re.compile(
    r"[\s\.,，。!！?？;；:：、'\"“”‘’()\[\]{}<>/\\|`~\-_=+]*"
)
WHISPER_HALLUCINATION_PUNCT_PATTERN = re.compile(
    r"[\s\.,，。!！?？;；:：、'\"“”‘’()\[\]{}<>/\\|`~\-_=+*@#$%^&]+"
)
WHISPER_HALLUCINATION_CJK_PATTERN = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
WHISPER_HALLUCINATION_LANG_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-tw": "zh",
    "zh-hant": "zh",
    "zt": "zh",
}
FIRERED_LID_CHINESE_LANGUAGE_TOKENS = frozenset(
    {"zh", "mandarin", "xinan", "min", "wu", "xiang", "yue", "north"}
)
FIRERED_LID_LANGUAGE_ALIASES = {
    "iw": "he",
    "zh mandarin": "zh",
    "zh north": "zh",
}
_firered_lid_semaphore: asyncio.Semaphore | None = None
FIRERED_LID_GLOBAL_LOCK_PATH = "/tmp/voicelink_firered_lid.lock"


@dataclass(frozen=True)
class FireRedLidResult:
    language: str
    raw_language: str
    confidence: float
    duration_seconds: float | None = None
    is_multilingual: bool = False
    language_count: int = 1
    languages: tuple[str, ...] = ()
    raw_languages: tuple[str, ...] = ()
    segments: tuple[dict[str, Any], ...] = ()
    language_details: tuple[dict[str, Any], ...] = ()

    @property
    def high_confidence(self) -> bool:
        return self.confidence >= float(settings.FIRERED_LID_HIGH_CONFIDENCE)

    @property
    def low_confidence(self) -> bool:
        return self.confidence < float(settings.FIRERED_LID_LOW_CONFIDENCE)


def is_asr_empty_audio_marker_text(text: str | None) -> bool:
    if not isinstance(text, str):
        return False

    normalized = text.strip()
    if not normalized:
        return False

    lowered = normalized.lower()
    special_tokens = [match.group(0).lower() for match in ASR_SPECIAL_TOKEN_PATTERN.finditer(lowered)]
    if not special_tokens:
        return False

    if any(token in ASR_EMPTY_AUDIO_MARKERS for token in special_tokens):
        return True

    without_special_tokens = ASR_SPECIAL_TOKEN_PATTERN.sub("", lowered)
    return ASR_MARKER_PADDING_PATTERN.sub("", without_special_tokens) == ""


def normalize_whisper_hallucination_text(text: str | None) -> str:
    if not isinstance(text, str):
        return ""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = WHISPER_HALLUCINATION_PUNCT_PATTERN.sub(" ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _canonical_whisper_hallucination_language(language: str | None) -> str:
    normalized = (language or "").strip().lower()
    return WHISPER_HALLUCINATION_LANG_ALIASES.get(normalized, normalized)


def _should_load_whisper_hallucination_phrase(phrase: str, count: int) -> bool:
    if count < max(1, int(settings.WHISPER_HALLUCINATION_FILTER_MIN_COUNT or 1)):
        return False

    if WHISPER_HALLUCINATION_CJK_PATTERN.search(phrase):
        return len(phrase) >= max(1, int(settings.WHISPER_HALLUCINATION_FILTER_CJK_MIN_CHARS or 1))

    return len(phrase) >= max(1, int(settings.WHISPER_HALLUCINATION_FILTER_MIN_CHARS or 1))


def is_whisper_hallucination_text(text: str | None, language: str | None = None) -> bool:
    if not settings.WHISPER_HALLUCINATION_FILTER_ENABLED:
        return False

    normalized = normalize_whisper_hallucination_text(text)
    if not normalized:
        return False

    language_key = _canonical_whisper_hallucination_language(language)
    if language_key:
        return normalized in whisperHallucinationFilterExact.get(language_key, set())

    return any(normalized in phrases for phrases in whisperHallucinationFilterExact.values())


def normalize_firered_lid_language(language: str | None) -> str:
    raw = re.sub(r"\s+", " ", (language or "").strip().lower().replace("_", " ").replace("-", " "))
    if not raw:
        return ""
    if raw in FIRERED_LID_LANGUAGE_ALIASES:
        return FIRERED_LID_LANGUAGE_ALIASES[raw]

    tokens = [token for token in raw.split(" ") if token]
    if any(token in FIRERED_LID_CHINESE_LANGUAGE_TOKENS for token in tokens):
        return "zh"

    first_token = tokens[0] if tokens else raw
    return FIRERED_LID_LANGUAGE_ALIASES.get(first_token, first_token)


def is_firered_lid_chinese_language(language: str | None) -> bool:
    return normalize_firered_lid_language(language) == "zh"


def _normalize_firered_lid_language_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    languages: list[str] = []
    for item in value:
        normalized = normalize_firered_lid_language(str(item or ""))
        if normalized and normalized not in languages:
            languages.append(normalized)
    return tuple(languages)


def _normalize_firered_lid_raw_language_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    raw_languages: list[str] = []
    for item in value:
        raw = str(item or "").strip()
        if raw and raw not in raw_languages:
            raw_languages.append(raw)
    return tuple(raw_languages)


def _safe_lid_dict_tuple(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _get_firered_lid_semaphore() -> asyncio.Semaphore:
    global _firered_lid_semaphore
    if _firered_lid_semaphore is None:
        _firered_lid_semaphore = asyncio.Semaphore(max(1, int(settings.FIRERED_LID_MAX_CONCURRENCY or 1)))
    return _firered_lid_semaphore


@contextmanager
def _firered_lid_global_gate():
    lock_file = None
    try:
        import fcntl  # Linux-only; production containers run Linux.

        lock_file = open(FIRERED_LID_GLOBAL_LOCK_PATH, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.debug("[FIRERED_LID] global detector busy; skip language detection")
            yield False
            return

        lock_file.seek(0)
        last_finished_text = lock_file.read().strip()
        try:
            last_finished_at = float(last_finished_text or "0")
        except ValueError:
            last_finished_at = 0.0

        min_interval = max(0.0, float(settings.FIRERED_LID_GLOBAL_MIN_INTERVAL or 0.0))
        elapsed = time.time() - last_finished_at
        if min_interval and elapsed < min_interval:
            logger.debug(
                "[FIRERED_LID] global cooldown active; skip language detection elapsed=%.3fs min=%.3fs",
                elapsed,
                min_interval,
            )
            yield False
            return

        try:
            yield True
        finally:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(str(time.time()))
            lock_file.flush()
            os.fsync(lock_file.fileno())
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except ImportError:
        yield True
    except Exception as exc:
        logger.warning("[FIRERED_LID] global gate failed open: %s", exc)
        yield True
    finally:
        if lock_file is not None:
            lock_file.close()


def normalize_asr_engine(engine: str | None = None) -> str:
    requested = (engine or settings.ASR_DEFAULT_ENGINE or "firered").strip().lower()
    normalized = ASR_ENGINE_ALIASES.get(requested)
    if normalized:
        return normalized
    raise ValueError(
        "unsupported ASR engine: "
        f"{engine}. supported engines: firered, sensevoice, whisper, whisper-trt"
    )


def _default_whisper_engine() -> str:
    return "whisper-trt" if settings.WHISPER_BACKEND == "whisper-trt" else "whisper"


def _is_chinese_asr_language(language: str) -> bool:
    return _canonical_lang_code(language) in CHINESE_ASR_LANGUAGES


def _asr_engine_supports_language(engine: str, language: str) -> bool:
    normalized_language = (language or "").strip().lower()
    if normalized_language == MULTILINGUAL_ASR_LANGUAGE:
        return engine == "whisper"
    supported_languages = ASR_ENGINE_LANGUAGE_SUPPORT.get(engine)
    if not supported_languages:
        return True
    return _canonical_lang_code(language) in supported_languages


def resolve_asr_engine_for_language(engine: str | None, language: str) -> str:
    selected_engine = normalize_asr_engine(engine)
    if (language or "").strip().lower() == MULTILINGUAL_ASR_LANGUAGE:
        return "whisper"
    if not _asr_engine_supports_language(selected_engine, language):
        return _default_whisper_engine()
    return selected_engine


def asr_engine_supports_direct_translation(engine: str | None = None) -> bool:
    return normalize_asr_engine(engine) == "whisper-trt"


def _whisper_trt_base_url() -> str:
    return f"http://{settings.WHISPER_TRT_HOST}:{settings.WHISPER_TRT_PORT}"


def _normalize_whisper_backend_base_url(raw_value: str) -> str:
    value = str(raw_value or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    for suffix in ("/audio/transcriptions", "/audio/translations"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid Whisper endpoint: {raw_value}")
    if not parsed.path.rstrip("/").endswith("/v1"):
        value = f"{value}/v1"
    return value.rstrip("/")


def _configured_whisper_base_urls() -> tuple[str, ...]:
    configured = str(getattr(settings, "WHISPER_ENDPOINTS", "") or "").replace(";", ",")
    raw_endpoints = [endpoint.strip() for endpoint in configured.split(",") if endpoint.strip()]
    if not raw_endpoints:
        raw_endpoints = [f"{settings.WHISPER_HOST}:{settings.WHISPER_PORT}"]

    urls: list[str] = []
    seen: set[str] = set()
    for raw_endpoint in raw_endpoints:
        base_url = _normalize_whisper_backend_base_url(raw_endpoint)
        if base_url and base_url not in seen:
            urls.append(base_url)
            seen.add(base_url)
    return tuple(urls)


def _whisper_backends() -> tuple[AsrBackend, ...]:
    base_urls = _configured_whisper_base_urls()
    if len(base_urls) <= 1:
        return tuple(AsrBackend("whisper", base_url) for base_url in base_urls)
    return tuple(
        AsrBackend(f"whisper-{index}", base_url)
        for index, base_url in enumerate(base_urls, start=1)
    )


def _whisper_base_url(base_url: str | None = None) -> str:
    if base_url:
        return _normalize_whisper_backend_base_url(base_url)
    backends = _whisper_backends()
    if backends:
        return backends[0].base_url
    return WHISPER_URL.rstrip("/")


def _whisper_transcription_url(base_url: str | None = None) -> str:
    return f"{_whisper_base_url(base_url)}/audio/transcriptions"


def _whisper_translation_url(base_url: str | None = None) -> str:
    return f"{_whisper_base_url(base_url)}/audio/translations"


def _sensevoice_transcription_url() -> str:
    return f"http://{settings.SENSEVOICE_HOST}:{settings.SENSEVOICE_PORT}/v1/audio/transcriptions"


def _firered_asr_base_url() -> str:
    return f"http://{settings.FIRERED_ASR_HOST}:{settings.FIRERED_ASR_PORT}"


def _firered_lid_base_url() -> str:
    return f"http://{settings.FIRERED_LID_HOST}:{settings.FIRERED_LID_PORT}"


def _extract_asr_text(result: dict, log_prefix: str) -> str:
    text = result.get("text", "")
    if not isinstance(text, str):
        logger.warning("[%s] Unexpected text payload type: %s", log_prefix, type(text))
        return ""
    return text


def _extract_asr_response_text_from_content(response: httpx.Response, content: bytes, log_prefix: str) -> str:
    content_type = response.headers.get("content-type", "")
    response_text = content.decode(response.encoding or "utf-8", errors="replace")
    if "application/json" in content_type:
        return _extract_asr_text(json.loads(response_text or "{}"), log_prefix)
    return response_text


def _asr_log_endpoint(url: str, endpoint_name: str | None = None) -> str:
    if endpoint_name:
        return endpoint_name
    parsed = urlparse(url)
    if parsed.netloc:
        return f"{parsed.netloc}{parsed.path}"
    return url


def _log_asr_http_stage(
    *,
    label: str,
    stage: str,
    url: str,
    started_at: float,
    stage_started_at: float,
    engine: str | None,
    endpoint_name: str | None,
    language: str | None,
    extra: str = "",
) -> float:
    now = time.perf_counter()
    logger.info(
        "[ASR_HTTP] stage=%s label=%s request_id=%s engine=%s endpoint=%s language=%s stage_elapsed=%.3fs total_elapsed=%.3fs%s",
        stage,
        label,
        get_request_id(),
        engine or "-",
        _asr_log_endpoint(url, endpoint_name),
        language or "-",
        now - stage_started_at,
        now - started_at,
        f" {extra}" if extra else "",
    )
    return now


def _lid_segments_form_value(lid_metadata: Optional[dict[str, object]]) -> str | None:
    if not lid_metadata:
        return None

    segments = lid_metadata.get("lidSegments")
    if isinstance(segments, list) and segments:
        return json.dumps(segments, ensure_ascii=False)
    return None


def _get_wav_duration_seconds(audio_file: bytes) -> float | None:
    if not audio_file:
        return 0.0

    try:
        with wave.open(BytesIO(audio_file), "rb") as wf:
            frame_count = wf.getnframes()
            sample_rate = wf.getframerate()
    except (wave.Error, EOFError):
        logger.debug("[ASYNC_TRANSCRIBE_FIRERED] Skip duration check for non-WAV payload")
        return None

    if sample_rate <= 0:
        return None
    return frame_count / sample_rate


def _raise_if_firered_audio_too_short(audio_file: bytes) -> None:
    duration_seconds = _get_wav_duration_seconds(audio_file)
    if duration_seconds is None or duration_seconds >= FIRERED_MIN_AUDIO_SECONDS:
        return

    logger.info(
        "[ASYNC_TRANSCRIBE_FIRERED] Reject short audio: %.3fs < %.3fs",
        duration_seconds,
        FIRERED_MIN_AUDIO_SECONDS,
    )
    raise HTTPException(status_code=400, detail="音频过短")


async def detect_language_firered_lid(
    audio_file: bytes,
    filename: str = "audio.wav",
    cancel_check: Optional[CancelCheck] = None,
) -> FireRedLidResult | None:
    await _run_cancel_check(cancel_check)
    if not settings.FIRERED_LID_ENABLED:
        return None

    duration_seconds = _get_wav_duration_seconds(audio_file)
    if duration_seconds is not None and duration_seconds < float(settings.FIRERED_LID_MIN_AUDIO_SECONDS):
        logger.info(
            "[FIRERED_LID] Skip short audio: %.3fs < %.3fs",
            duration_seconds,
            settings.FIRERED_LID_MIN_AUDIO_SECONDS,
        )
        return None

    with _firered_lid_global_gate() as global_gate_allowed:
        if not global_gate_allowed:
            return None

        semaphore = _get_firered_lid_semaphore()
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=max(0.001, float(settings.FIRERED_LID_QUEUE_TIMEOUT or 0.001)),
            )
        except asyncio.TimeoutError:
            logger.debug("[FIRERED_LID] detector busy; skip language detection")
            return None

        try:
            async with httpx.AsyncClient(timeout=settings.FIRERED_LID_TIMEOUT) as client:
                files = {"files": (filename or "audio.wav", audio_file, "audio/wav")}
                response = await _post_with_cancel(
                    client,
                    f"{_firered_lid_base_url()}/predict",
                    files=files,
                    cancel_check=cancel_check,
                    label="FIRERED_LID",
                )
                response.raise_for_status()
                payload = response.json()
                await _run_cancel_check(cancel_check)

            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list) or not results:
                logger.warning("[FIRERED_LID] Unexpected response payload: %s", payload)
                return None

            result = results[0]
            if not isinstance(result, dict):
                logger.warning("[FIRERED_LID] Unexpected result payload: %s", result)
                return None

            raw_language = str(result.get("lang") or "").strip()
            normalized_language = normalize_firered_lid_language(raw_language)
            confidence = float(result.get("confidence") or 0.0)
            detected_duration = result.get("dur_s")
            if detected_duration is not None:
                duration_seconds = float(detected_duration)
            detected_languages = _normalize_firered_lid_language_list(result.get("languages"))
            raw_languages = _normalize_firered_lid_raw_language_list(result.get("languages"))
            language_details = _safe_lid_dict_tuple(result.get("language_details"))
            segments = _safe_lid_dict_tuple(result.get("segments"))
            if not detected_languages and normalized_language:
                detected_languages = (normalized_language,)
            language_count = int(result.get("language_count") or len(detected_languages) or (1 if normalized_language else 0))
            is_multilingual = bool(result.get("is_multilingual")) or language_count > 1

            if not normalized_language:
                logger.info("[FIRERED_LID] Empty language result. raw=%r confidence=%.3f", raw_language, confidence)
                return None

            lid_result = FireRedLidResult(
                language=normalized_language,
                raw_language=raw_language,
                confidence=confidence,
                duration_seconds=duration_seconds,
                is_multilingual=is_multilingual,
                language_count=language_count,
                languages=detected_languages,
                raw_languages=raw_languages,
                segments=segments,
                language_details=language_details,
            )
            logger.info(
                "[FIRERED_LID] detected language=%s raw=%r confidence=%.3f duration=%s multilingual=%s languages=%s",
                lid_result.language,
                lid_result.raw_language,
                lid_result.confidence,
                lid_result.duration_seconds,
                lid_result.is_multilingual,
                list(lid_result.languages),
            )
            return lid_result
        except ClientDisconnectedError:
            raise
        except Exception as e:
            logger.warning("[FIRERED_LID] language detection failed, falling back to requested source language: %s", e)
            return None
        finally:
            semaphore.release()

LANG_CANONICAL_MAPPING = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-sg": "zh",
    "zh-hans": "zh",
    "zt": "zt",
    "zh-hant": "zt",
    "zh-tw": "zt",
    "zh-hk": "zt",
    "zh-mo": "zt",
}


def _canonical_lang_code(lang: str) -> str:
    raw = (lang or "").strip().lower()
    if not raw:
        return raw
    if raw in LANG_CANONICAL_MAPPING:
        return LANG_CANONICAL_MAPPING[raw]
    if "-" in raw:
        base = raw.split("-", 1)[0]
        return LANG_CANONICAL_MAPPING.get(base, base)
    return raw


def _is_same_translation_lang(source_lang: str, target_lang: str) -> bool:
    source = _canonical_lang_code(source_lang)
    target = _canonical_lang_code(target_lang)
    if not source or not target:
        return False
    return source == target


def _is_auto_translation_lang(lang: str) -> bool:
    return (lang or "").strip().lower() == "auto"


def _is_dynamic_source_translation_lang(lang: str) -> bool:
    return (lang or "").strip().lower() in {"auto", MULTILINGUAL_ASR_LANGUAGE}


def normalize_translation_language_code(lang: str) -> str:
    return _canonical_lang_code(lang)


def _iter_capability_targets(raw_targets):
    if isinstance(raw_targets, dict):
        return raw_targets.keys()
    if isinstance(raw_targets, (list, tuple, set)):
        return raw_targets
    if isinstance(raw_targets, str):
        return [raw_targets]
    return []


def get_runtime_translation_language_sets():
    source_languages = set()
    target_languages = set()

    try:
        capabilities = get_translation_capabilities()
        engines = capabilities.get("engines", []) if isinstance(capabilities, dict) else []

        for engine in engines:
            language_map = engine.get("languages", {}) if isinstance(engine, dict) else {}
            if not isinstance(language_map, dict):
                continue

            for raw_source, raw_targets in language_map.items():
                source = _canonical_lang_code(str(raw_source))
                if source and not _is_auto_translation_lang(source):
                    source_languages.add(source)

                for raw_target in _iter_capability_targets(raw_targets):
                    target = _canonical_lang_code(str(raw_target))
                    if target and not _is_auto_translation_lang(target):
                        target_languages.add(target)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[TRANSLATE] Failed to load runtime translation capabilities: %s", exc)

    if not source_languages:
        source_languages.update(supportedLanguagesList)
    if not target_languages:
        target_languages.update(supportedLanguagesList)

    return {
        "sources": sorted(source_languages),
        "targets": sorted(target_languages),
    }


def get_runtime_translation_source_languages():
    return get_runtime_translation_language_sets()["sources"]


def get_runtime_translation_target_languages():
    return get_runtime_translation_language_sets()["targets"]


def is_runtime_translation_source_supported(lang: str) -> bool:
    normalized = _canonical_lang_code(lang)
    return normalized in set(get_runtime_translation_source_languages())


def is_runtime_translation_target_supported(lang: str) -> bool:
    normalized = _canonical_lang_code(lang)
    return normalized in set(get_runtime_translation_target_languages())


SCRIPT_LANGUAGE_RANGES = {
    "ko": [(0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)],
    "ja": [(0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF)],
    "ru": [(0x0400, 0x04FF), (0x0500, 0x052F)],
    "ar": [(0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)],
    "hi": [(0x0900, 0x097F)],
    "th": [(0x0E00, 0x0E7F)],
    "he": [(0x0590, 0x05FF)],
    "el": [(0x0370, 0x03FF)],
}

LATIN_LANGUAGE_HINTS = {
    "en": {"the", "and", "you", "this", "that", "are", "with", "have", "what", "please", "hello"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ich", "mit", "ein", "eine", "bitte", "hallo"},
    "fr": {"le", "la", "les", "de", "des", "est", "pas", "que", "pour", "une", "bonjour", "merci"},
    "es": {"el", "la", "los", "las", "de", "que", "para", "por", "una", "gracias", "hola", "estoy"},
    "pt": {"de", "que", "para", "por", "uma", "obrigado", "ola", "voce", "estou", "nao"},
    "it": {"che", "per", "una", "grazie", "ciao", "sono", "non", "con", "del", "della"},
    "nl": {"de", "het", "een", "en", "voor", "niet", "hallo", "dank", "ik", "jij"},
}

TRADITIONAL_CHINESE_HINTS = set("體對會說這個們來時後開關讓歡國語麼嗎學習點電腦網頁輸入龍貓貝萬與")


def _count_chars_in_ranges(text: str, ranges: list[tuple[int, int]]) -> int:
    count = 0
    for ch in text:
        code = ord(ch)
        for start, end in ranges:
            if start <= code <= end:
                count += 1
                break
    return count


def _contains_cjk(text: str) -> bool:
    return any(
        0x4E00 <= ord(ch) <= 0x9FFF
        or 0x3400 <= ord(ch) <= 0x4DBF
        for ch in text
    )


def _detect_script_language(text: str) -> str | None:
    scores = {
        lang: _count_chars_in_ranges(text, ranges)
        for lang, ranges in SCRIPT_LANGUAGE_RANGES.items()
    }

    best_lang = None
    best_score = 0
    for lang, score in scores.items():
        if score > best_score:
            best_lang = lang
            best_score = score

    if best_lang and best_score > 0:
        return best_lang

    if _contains_cjk(text):
        traditional_hits = sum(1 for ch in text if ch in TRADITIONAL_CHINESE_HINTS)
        return "zt" if traditional_hits > 0 else "zh"

    return None


def _detect_latin_language(text: str) -> str | None:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ']+", text.lower())
    if not tokens:
        return None

    scores: dict[str, int] = {}
    for lang, hints in LATIN_LANGUAGE_HINTS.items():
        score = sum(1 for token in tokens if token in hints)
        if score > 0:
            scores[lang] = score

    if not scores:
        return "en"

    return max(scores.items(), key=lambda item: item[1])[0]


def detect_translation_source_language(text: str, allowed_languages: list[str] | None = None) -> str:
    allowed = {
        _canonical_lang_code(lang)
        for lang in (allowed_languages or get_runtime_translation_source_languages())
        if lang and not _is_auto_translation_lang(lang)
    }

    detected = _detect_script_language(text) or _detect_latin_language(text) or "en"
    canonical = _canonical_lang_code(detected)

    if canonical in allowed:
        return canonical

    if canonical == "zt" and "zh" in allowed:
        return "zh"

    if "en" in allowed:
        return "en"

    if "zh" in allowed:
        return "zh"

    return next(iter(allowed), "en")


def _load_whisper_hallucination_filter_config():
    if not settings.WHISPER_HALLUCINATION_FILTER_ENABLED:
        whisperHallucinationFilterExact.clear()
        logger.info("Whisper hallucination filter disabled.")
        return

    filter_path = os.path.join(os.path.dirname(__file__), '..', 'filter_whisper_hallucinations.json')
    loaded_filter: dict[str, set[str]] = {}
    total_phrases = 0

    try:
        with open(filter_path, 'r', encoding='utf-8') as f:
            local_config = json.load(f)

        exact_config = local_config.get("exact", {})
        if not isinstance(exact_config, dict):
            raise ValueError("invalid exact filter config")

        for raw_lang, entries in exact_config.items():
            language_key = _canonical_whisper_hallucination_language(raw_lang)
            if not language_key or not isinstance(entries, list):
                continue

            phrase_set = loaded_filter.setdefault(language_key, set())
            for entry in entries:
                if isinstance(entry, dict):
                    raw_phrase = entry.get("phrase")
                    count = int(entry.get("count") or 1)
                else:
                    raw_phrase = entry
                    count = 1

                normalized_phrase = normalize_whisper_hallucination_text(raw_phrase)
                if not normalized_phrase:
                    continue
                if not _should_load_whisper_hallucination_phrase(normalized_phrase, count):
                    continue
                phrase_set.add(normalized_phrase)

        loaded_filter = {lang: phrases for lang, phrases in loaded_filter.items() if phrases}
        total_phrases = sum(len(phrases) for phrases in loaded_filter.values())
        whisperHallucinationFilterExact.clear()
        whisperHallucinationFilterExact.update(loaded_filter)
        logger.info(
            "Loaded Whisper hallucination filter. languages=%s phrases=%s min_count=%s min_chars=%s cjk_min_chars=%s",
            len(whisperHallucinationFilterExact),
            total_phrases,
            settings.WHISPER_HALLUCINATION_FILTER_MIN_COUNT,
            settings.WHISPER_HALLUCINATION_FILTER_MIN_CHARS,
            settings.WHISPER_HALLUCINATION_FILTER_CJK_MIN_CHARS,
        )
    except Exception as e:
        whisperHallucinationFilterExact.clear()
        logger.error(f"Failed to load Whisper hallucination filter config: {e}")


# --- 初始化函数 ---
def load_filter_config():
    global errorFilter
    filter_path = os.path.join(os.path.dirname(__file__), '..', 'filter.json')

    try:
        # 首先加载本地配置
        with open(filter_path, 'r', encoding='utf-8') as f:
            local_config = json.load(f)
            logger.info("Loaded local filter config.")

        # 如果配置了网络URL，尝试获取网络配置并合并
        if settings.FILTER_WEB_URL:
            try:
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(settings.FILTER_WEB_URL)
                    response.raise_for_status()
                    web_config = response.json()

                    # 合并配置：网络配置优先，补充本地配置
                    merged_config = {
                        "errorResultDict": list(set(web_config.get("errorResultDict", []) + local_config.get("errorResultDict", []))),
                        "errorKeyString": list(set(web_config.get("errorKeyString", []) + local_config.get("errorKeyString", [])))
                    }
                    
                    # 不要重新赋值，而是清空并更新原始字典
                    errorFilter.clear()
                    errorFilter.update(merged_config)
                    
                    logger.info("Successfully merged filter config from web and local.")
                    _load_whisper_hallucination_filter_config()
                    return
            except Exception as e:
                logger.warning(f"Failed to load filter from web, using local config. Error: {e}")
        # 使用本地配置
        errorFilter.clear()
        errorFilter.update(local_config)
        logger.info("Using local filter config.")

    except Exception as e:
        logger.error(f"Failed to load filter config: {e}")
        # 设置默认空配置
        errorFilter = {"errorResultDict": [], "errorKeyString": []}
        logger.warning("Using empty filter config due to load failure.")

    _load_whisper_hallucination_filter_config()

def update_filter_config():
    """定时更新filter配置的函数"""
    logger.info("开始检查filter配置更新...")
    try:
        load_filter_config()
        logger.info(f"Filter配置更新完成，当前规则数: errorResultDict={len(errorFilter.get('errorResultDict', []))}, errorKeyString={len(errorFilter.get('errorKeyString', []))}")
    except Exception as e:
        logger.error(f"Filter配置更新失败: {e}")

def init_supported_languages():
    """
    初始化支持语言列表 - 现在使用硬编码的固定列表，不再动态获取
    原有server.py中是从LibreTranslate动态获取，现在改为固定列表以提高稳定性
    """
    global supportedLanguagesList
    # supportedLanguagesList 现在已经在模块级别硬编码初始化
    # 这个函数保留用于向后兼容，只记录日志
    logger.info(f"[INIT] 使用硬编码的支持语言列表，包含 {len(supportedLanguagesList)} 种语言: {supportedLanguagesList}")

# --- 业务逻辑函数 ---

# --- 异步Whisper函数 ---

async def async_transcribe(
    audio_file: bytes,
    language: str = "zh",
    filename: str = "audio.wav",
    cancel_check: Optional[CancelCheck] = None,
    lid_metadata: Optional[dict[str, object]] = None,
) -> str:
    """
    异步语音转文字（ASR）
    使用非标准 HTTP 调用 faster-whisper 后端，允许传递 LID 分段等扩展字段。

    Args:
        audio_file: 音频文件字节数据
        language: 语言代码，默认'zh'

    Returns:
        识别出的文本
    """
    async def operation(backend: AsrBackend) -> str:
        data = {
            "model": settings.WHISPER_MODEL,
            "response_format": "json",
            "without_timestamps": "true",
        }
        if (language or "").strip().lower() != MULTILINGUAL_ASR_LANGUAGE:
            data["language"] = language

        lid_segments = _lid_segments_form_value(lid_metadata)
        if lid_segments:
            data["lid_segments"] = lid_segments

        return await _post_asr_form_and_extract_text(
            _whisper_transcription_url(backend.base_url),
            files={"file": (filename or "audio.wav", audio_file, "audio/wav")},
            data=data,
            timeout_seconds=settings.WHISPER_ASR_TIMEOUT,
            cancel_check=cancel_check,
            label="ASYNC_TRANSCRIBE",
            engine="whisper",
            endpoint_name=backend.endpoint,
            language=language,
        )

    try:
        return await _run_asr_backend(
            "whisper",
            _whisper_backends(),
            language,
            operation,
            cancel_check=cancel_check,
            hard_timeout_seconds=_asr_hard_timeout_seconds(settings.WHISPER_ASR_TIMEOUT),
        )
    except ClientDisconnectedError:
        raise
    except Exception as e:
        logger.error(f"[ASYNC_TRANSCRIBE] 语音识别失败: {e}")
        raise


async def async_translate_audio(
    audio_file: bytes,
    filename: str = "audio.wav",
    cancel_check: Optional[CancelCheck] = None,
) -> str:
    """
    异步音频翻译（翻译为英文）
    使用非标准 HTTP 调用 faster-whisper 后端，避免 OpenAI SDK 字段限制。

    Args:
        audio_file: 音频文件字节数据

    Returns:
        翻译后的英文文本
    """
    async def operation(backend: AsrBackend) -> str:
        return await _post_asr_form_and_extract_text(
            _whisper_translation_url(backend.base_url),
            files={"file": (filename or "audio.wav", audio_file, "audio/wav")},
            data={
                "model": settings.WHISPER_MODEL,
                "response_format": "json",
            },
            timeout_seconds=settings.WHISPER_ASR_TIMEOUT,
            cancel_check=cancel_check,
            label="ASYNC_TRANSLATE_AUDIO",
            engine="whisper",
            endpoint_name=backend.endpoint,
            language="translate-en",
        )

    try:
        return await _run_asr_backend(
            "whisper",
            _whisper_backends(),
            "translate-en",
            operation,
            cancel_check=cancel_check,
            hard_timeout_seconds=_asr_hard_timeout_seconds(settings.WHISPER_ASR_TIMEOUT),
        )
    except ClientDisconnectedError:
        raise
    except Exception as e:
        logger.error(f"[ASYNC_TRANSLATE_AUDIO] 音频翻译失败: {e}")
        raise


async def async_transcribe_sensevoice(audio_file: bytes, filename: str = "audio.wav", cancel_check: Optional[CancelCheck] = None) -> str:
    async def operation() -> str:
        return await _post_asr_form_and_extract_text(
            _sensevoice_transcription_url(),
            files={'file': (filename or 'audio.wav', audio_file, 'audio/wav')},
            data=None,
            timeout_seconds=30.0,
            cancel_check=cancel_check,
            label="ASYNC_TRANSCRIBE_SENSEVOICE",
            engine="sensevoice",
            endpoint_name="sensevoice",
            language="zh",
        )

    try:
        return await _run_asr_endpoint(
            "sensevoice",
            "zh",
            operation,
            cancel_check=cancel_check,
            hard_timeout_seconds=_asr_hard_timeout_seconds(30.0),
        )
    except ClientDisconnectedError:
        raise
    except Exception as e:
        logger.error(f"[ASYNC_TRANSCRIBE_SENSEVOICE] SenseVoice璇煶璇嗗埆澶辫触: {e}")
        raise


async def async_transcribe_firered(audio_file: bytes, filename: str = "audio.wav", cancel_check: Optional[CancelCheck] = None) -> str:
    _raise_if_firered_audio_too_short(audio_file)

    async def operation() -> str:
        return await _post_asr_form_and_extract_text(
            f"{_firered_asr_base_url()}/transcribe",
            files={'file': (filename or 'audio.wav', audio_file, 'application/octet-stream')},
            data=None,
            timeout_seconds=settings.FIRERED_ASR_TIMEOUT,
            cancel_check=cancel_check,
            label="ASYNC_TRANSCRIBE_FIRERED",
            engine="firered",
            endpoint_name="firered",
            language="zh",
        )

    try:
        return await _run_asr_endpoint(
            "firered",
            "zh",
            operation,
            cancel_check=cancel_check,
            hard_timeout_seconds=_asr_hard_timeout_seconds(settings.FIRERED_ASR_TIMEOUT),
        )
    except HTTPException:
        raise
    except ClientDisconnectedError:
        raise
    except Exception as e:
        logger.error(f"[ASYNC_TRANSCRIBE_FIRERED] FireRedASR2S璇煶璇嗗埆澶辫触: {e}")
        raise


def _normalize_target_language_for_trt(language: str) -> str:
    normalized = (language or "").strip().lower()
    if normalized == "auto":
        raise ValueError("TRT translation no longer supports source/target language 'auto'")
    if not normalized:
        return "en"
    return normalized


async def async_transcribe_trt(audio_file: bytes, language: str = 'zh', cancel_check: Optional[CancelCheck] = None) -> str:
    """
    使用 WhisperLive-TRT 后端进行语音识别（含自动翻译）。

    TRT 的 language 参数语义是目标语言，不支持 auto。
    """
    target_language = _normalize_target_language_for_trt(language)

    async def operation() -> str:
        return await _post_asr_form_and_extract_text(
            f"{_whisper_trt_base_url()}/v1/audio/transcriptions",
            files={'file': ('audio.wav', audio_file, 'audio/wav')},
            data={
                'language': target_language,
                'response_format': 'json',
            },
            timeout_seconds=settings.FIRERED_ASR_TIMEOUT,
            cancel_check=cancel_check,
            label="ASYNC_TRANSCRIBE_TRT",
            engine="whisper-trt",
            endpoint_name="whisper-trt",
            language=target_language,
        )

    try:
        return await _run_asr_endpoint(
            "whisper-trt",
            target_language,
            operation,
            cancel_check=cancel_check,
            hard_timeout_seconds=_asr_hard_timeout_seconds(settings.FIRERED_ASR_TIMEOUT),
        )
    except ClientDisconnectedError:
        raise
    except Exception as e:
        logger.error(f"[ASYNC_TRANSCRIBE_TRT] TRT语音识别失败: {e}")
        raise


async def do_transcribe(
    audio_file: bytes,
    language: str = "zh",
    engine: str | None = None,
    filename: str = "audio.wav",
    cancel_check: Optional[CancelCheck] = None,
    lid_metadata: Optional[dict[str, object]] = None,
) -> str:
    await _run_cancel_check(cancel_check)
    requested_engine = normalize_asr_engine(engine)
    selected_engine = resolve_asr_engine_for_language(engine, language)
    if selected_engine != requested_engine:
        logger.info(
            "[ASR] engine fallback: requested=%s effective=%s language=%s",
            requested_engine,
            selected_engine,
            language,
        )
    else:
        logger.info("[ASR] engine selected: effective=%s language=%s", selected_engine, language)
    if selected_engine == "firered":
        try:
            return await async_transcribe_firered(audio_file, filename=filename, cancel_check=cancel_check)
        except HTTPException:
            raise
        except ClientDisconnectedError:
            raise
        except Exception as exc:
            logger.warning(
                "[ASR] FireRed failed; falling back to SenseVoice for this request: %s",
                exc,
            )
            return await async_transcribe_sensevoice(audio_file, filename=filename, cancel_check=cancel_check)
    if selected_engine == "sensevoice":
        return await async_transcribe_sensevoice(audio_file, filename=filename, cancel_check=cancel_check)
    if selected_engine == "whisper-trt":
        return await async_transcribe_trt(audio_file, language=language, cancel_check=cancel_check)
    return await async_transcribe(
        audio_file,
        language=language,
        filename=filename,
        cancel_check=cancel_check,
        lid_metadata=lid_metadata,
    )


async def do_translate_audio(
    audio_file: bytes,
    engine: str | None = None,
    filename: str = "audio.wav",
    cancel_check: Optional[CancelCheck] = None,
) -> str:
    await _run_cancel_check(cancel_check)
    selected_engine = normalize_asr_engine(engine)
    if selected_engine == "whisper-trt":
        return await async_transcribe_trt(audio_file, language="en", cancel_check=cancel_check)
    if selected_engine == "whisper":
        return await async_translate_audio(audio_file, filename=filename, cancel_check=cancel_check)

    text = await do_transcribe(audio_file, language="zh", engine=selected_engine, filename=filename, cancel_check=cancel_check)
    if not text:
        return ""
    detected_source = detect_translation_source_language(text)
    if _is_same_translation_lang(detected_source, "en"):
        return text
    return await async_do_translate(text, from_=detected_source, to="en", cancel_check=cancel_check)


def _do_web_translate(text: str, from_: str, to: str) -> str:
    services_list = [s.strip() for s in settings.TRANSLATOR_SERVICES_LIST.split(',')]

    translators_module = get_translators()

    # 记录开始翻译
    logger.info(f"[TRANSLATE] 开始翻译 - 原文: '{text[:50]}...', 源语言: {from_}, 目标语言: {to}")
    logger.debug(f"[TRANSLATE] 供应商列表: {services_list}, 超时时间: {settings.TRANSLATION_TIMEOUT}秒")

    # 依次尝试每个供应商
    for i, service in enumerate(services_list):
        try:
            logger.info(f"[TRANSLATE] 尝试供应商 {i+1}/{len(services_list)}: {service}")

            # 处理繁体中文映射
            to_language = transalte_zt.get(service, 'zt') if to == 'zt' else to

            # 执行翻译，使用内置timeout参数
            result = translators_module.translate_text(
                text,
                translator=service,
                from_language=from_,
                to_language=to_language,
                timeout=settings.TRANSLATION_TIMEOUT  # 使用内置超时参数
            )

            result = html.unescape(result)
            logger.info(f"[TRANSLATE] 翻译成功 - 供应商: {service}, 结果: '{result[:50]}...'")
            return result

        except Exception as e:
            logger.warning(f"[TRANSLATE] 供应商 {service} 失败: {type(e).__name__}: {e}")

            # 如果是最后一个供应商，记录错误并返回原文
            if i == len(services_list) - 1:
                logger.error(f"[TRANSLATE] 所有供应商都失败，返回原文 - 已尝试: {services_list}")
                return text

            # 继续尝试下一个供应商
            logger.info(f"[TRANSLATE] 切换到下一个供应商")
            continue

    # 所有供应商都失败，返回原文
    logger.error(f"[TRANSLATE] 翻译失败，所有供应商不可用 - 返回原文")
    return text


def do_translate(text: str, from_: str, to: str):
    """
    同步翻译函数，多供应商支持故障转移和超时控制
    使用translator库的timeout参数实现超时控制
    """
    if not text or not text.strip():
        return ""

    if _is_auto_translation_lang(to):
        raise ValueError("Translation no longer supports target language 'auto'")

    if _is_same_translation_lang(from_, to):
        logger.info(
            "[TRANSLATE] Skip translation for same language pair: source=%s, target=%s",
            from_,
            to,
        )
        return text

    if settings.GEMMA_TRANSLATE_ENABLED:
        if is_gemma_translation_supported(from_, to):
            logger.info(f"[TRANSLATE] Trying TranslateGemma first - source={from_}, target={to}")
            gemma_result = gemma_translate(text, source_lang=from_, target_lang=to)
            if gemma_result is not None:
                logger.info("[TRANSLATE] TranslateGemma succeeded")
                return gemma_result

            logger.warning("[TRANSLATE] TranslateGemma failed")
            if not settings.GEMMA_TRANSLATE_FALLBACK:
                logger.error("[TRANSLATE] Fallback disabled, returning source text")
                return text
        else:
            logger.info(
                "[TRANSLATE] Skip TranslateGemma due to unsupported language pair: source=%s, target=%s",
                from_,
                to,
            )

    web_source_lang = "auto" if _is_dynamic_source_translation_lang(from_) else from_
    return _do_web_translate(text, web_source_lang, to)


async def async_do_translate(
    text: str,
    from_: str,
    to: str,
    cancel_check: Optional[CancelCheck] = None,
) -> str:
    await _run_cancel_check(cancel_check)
    if not text or not text.strip():
        return ""

    if _is_auto_translation_lang(to):
        raise ValueError("Translation no longer supports target language 'auto'")

    if _is_same_translation_lang(from_, to):
        logger.info(
            "[TRANSLATE] Skip translation for same language pair: source=%s, target=%s",
            from_,
            to,
        )
        return text

    if settings.GEMMA_TRANSLATE_ENABLED:
        if is_gemma_translation_supported(from_, to):
            logger.info(f"[TRANSLATE] Trying async TranslateGemma first - source={from_}, target={to}")
            gemma_result = await _await_with_cancel(
                async_gemma_translate(text, source_lang=from_, target_lang=to),
                cancel_check,
                "GEMMA_TRANSLATE",
            )
            await _run_cancel_check(cancel_check)
            if gemma_result is not None:
                logger.info("[TRANSLATE] TranslateGemma succeeded")
                return gemma_result

            logger.warning("[TRANSLATE] TranslateGemma failed")
            if not settings.GEMMA_TRANSLATE_FALLBACK:
                logger.error("[TRANSLATE] Fallback disabled, returning source text")
                return text
        else:
            logger.info(
                "[TRANSLATE] Skip TranslateGemma due to unsupported language pair: source=%s, target=%s",
                from_,
                to,
            )

    web_source_lang = "auto" if _is_dynamic_source_translation_lang(from_) else from_
    result = await _await_with_cancel(
        asyncio.to_thread(_do_web_translate, text, web_source_lang, to),
        cancel_check,
        "WEB_TRANSLATE",
    )
    await _run_cancel_check(cancel_check)
    return result


def packaged_opus_stream_to_wav_bytes(packaged_opus_data: bytes, sample_rate: int) -> bytes:
    # (此函数逻辑复杂且纯粹，直接从 server.py 复制)
    channels = 1
    frame_duration = 20
    sample_width = 2
    if not packaged_opus_data: return b''
    try:
        decoder = opuslib.Decoder(sample_rate, channels)
    except opuslib.OpusError as e:
        raise RuntimeError(f"创建 Opus 解码器失败: {e}")
    samples_per_packet_frame = int(sample_rate * frame_duration / 1000)
    all_decoded_pcm = bytearray()
    offset = 0
    while offset < len(packaged_opus_data):
        if offset + 4 > len(packaged_opus_data): break
        try:
            packet_len = struct.unpack('>I', packaged_opus_data[offset:offset+4])[0]
        except struct.error: break
        offset += 4
        if offset + packet_len > len(packaged_opus_data): break
        opus_packet = packaged_opus_data[offset:offset+packet_len]
        offset += packet_len
        if not opus_packet: continue
        try:
            decoded_pcm = decoder.decode(opus_packet, samples_per_packet_frame)
            all_decoded_pcm.extend(decoded_pcm)
        except opuslib.OpusError as e:
            logger.warning(f"Opus 解码错误 (跳过包): {e}")
    if not all_decoded_pcm: return b''
    wav_buffer = BytesIO()
    with wave.open(wav_buffer, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(all_decoded_pcm))
    return wav_buffer.getvalue()
