from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from db.models import V2EndpointHourlyStats, V2RequestAuditLog, V2SubjectDailyUsage, V2UsageEvent

V2_METRIC_EXCLUDED_ENDPOINTS = {
    "/v2/api/latestVersionInfo",
    "/v2/api/updates/check",
    "/v2/api/models/list",
    "/v2/api/translations/profile",
    "/v2/api/translators/runtime",
    "/v2/api/translations/capabilities",
}

V2_METRIC_EXCLUDED_PREFIXES = (
    "/v2/auth",
    "/v2/api/account",
    "/v2/api/billing",
    "/v2/api/content",
    "/v2/adminapi",
)

SUBJECT_TYPE_USER = "user"
SUBJECT_TYPE_IP = "ip"


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def truncate_to_utc_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def is_v2_metric_endpoint(path: str) -> bool:
    normalized_path = (path or "").strip()
    if not normalized_path:
        return False
    if normalized_path in V2_METRIC_EXCLUDED_ENDPOINTS:
        return False
    return not any(normalized_path.startswith(prefix) for prefix in V2_METRIC_EXCLUDED_PREFIXES)


def resolve_usage_subject(
    *,
    user_uuid: Optional[str],
    username: Optional[str],
    client_ip: Optional[str],
    actor_type: Optional[str],
    is_default_test_user: bool = False,
) -> tuple[str, str] | None:
    normalized_user_uuid = (user_uuid or "").strip()
    normalized_username = (username or "").strip()
    normalized_ip = (client_ip or "").strip()

    if is_default_test_user and normalized_ip:
        return SUBJECT_TYPE_IP, normalized_ip
    if str(actor_type or "").strip().lower() == "anonymous_test" and normalized_ip:
        return SUBJECT_TYPE_IP, normalized_ip
    if normalized_user_uuid:
        return SUBJECT_TYPE_USER, normalized_user_uuid
    if normalized_username:
        return SUBJECT_TYPE_USER, normalized_username
    if normalized_ip:
        return SUBJECT_TYPE_IP, normalized_ip
    return None


def resolve_usage_subject_for_user(user, *, client_ip: str | None = None) -> tuple[str, str] | None:
    return resolve_usage_subject(
        user_uuid=getattr(user, "user_uuid", None),
        username=getattr(user, "username", None),
        client_ip=client_ip,
        actor_type="anonymous_test" if bool(getattr(user, "is_default_test_user", False)) and client_ip else "authenticated",
        is_default_test_user=bool(getattr(user, "is_default_test_user", False)),
    )


def list_subject_daily_usage_rows(
    db: Session,
    *,
    subject_type: str,
    subject_key: str,
    start_date: date,
    end_date: date,
) -> list[V2SubjectDailyUsage]:
    return (
        db.query(V2SubjectDailyUsage)
        .filter(
            V2SubjectDailyUsage.subject_type == subject_type,
            V2SubjectDailyUsage.subject_key == subject_key,
            V2SubjectDailyUsage.usage_date >= start_date,
            V2SubjectDailyUsage.usage_date <= end_date,
        )
        .order_by(V2SubjectDailyUsage.usage_date.asc())
        .all()
    )


def get_subject_daily_usage_row(
    db: Session,
    *,
    subject_type: str,
    subject_key: str,
    usage_date: date,
) -> V2SubjectDailyUsage | None:
    return (
        db.query(V2SubjectDailyUsage)
        .filter(
            V2SubjectDailyUsage.subject_type == subject_type,
            V2SubjectDailyUsage.subject_key == subject_key,
            V2SubjectDailyUsage.usage_date == usage_date,
        )
        .first()
    )


@dataclass
class V2RequestMetricRecord:
    request_id: str
    created_at: datetime
    username: str | None
    user_uuid: str | None
    actor_type: str | None
    rate_limit_scope: str | None
    quota_scope: str | None
    ip: str
    host: str | None
    method: str
    endpoint: str
    duration_ms: int
    status: str
    status_code: int | None
    resource_cost: int = 0
    resource_deducted: bool = False
    quota_source: str = "none"
    is_default_test_user: bool = False
    extra_payload: object | None = None


def _resource_cost_breakdown(resource_cost: int, quota_source: str) -> tuple[int, int]:
    normalized_cost = max(0, int(resource_cost or 0))
    normalized_source = str(quota_source or "none").strip().lower()
    if normalized_source == "addon":
        return 0, normalized_cost
    return normalized_cost, 0


def _event_payload(record: V2RequestMetricRecord, *, subject_type: str, subject_key: str) -> dict:
    return {
        "request_id": record.request_id,
        "subject_type": subject_type,
        "subject_key": subject_key,
        "username": record.username,
        "user_uuid": record.user_uuid,
        "actor_type": record.actor_type,
        "ip": record.ip,
        "endpoint": record.endpoint,
        "occurred_at": record.created_at,
        "status": record.status,
        "status_code": record.status_code,
        "resource_cost": int(record.resource_cost or 0),
        "resource_deducted": bool(record.resource_deducted),
        "quota_source": record.quota_source or "none",
    }


def _daily_usage_insert_payload(record: V2RequestMetricRecord, *, subject_type: str, subject_key: str) -> dict:
    daily_resource_used, addon_resource_used = _resource_cost_breakdown(record.resource_cost, record.quota_source)
    return {
        "usage_date": record.created_at.date(),
        "subject_type": subject_type,
        "subject_key": subject_key,
        "username": record.username,
        "user_uuid": record.user_uuid,
        "success_count": 1 if record.status == "success" else 0,
        "failed_count": 1 if record.status == "failed" else 0,
        "rate_limited_count": 1 if record.status == "rate_limited" else 0,
        "total_count": 1,
        "resource_cost_sum": int(record.resource_cost or 0),
        "daily_resource_used": daily_resource_used,
        "addon_resource_used": addon_resource_used,
        "deducted_request_count": 1 if record.resource_deducted else 0,
        "first_request_at": record.created_at,
        "last_request_at": record.created_at,
        "created_at": record.created_at,
        "updated_at": record.created_at,
    }


def _hourly_stats_insert_payload(record: V2RequestMetricRecord) -> dict:
    return {
        "stat_hour": truncate_to_utc_hour(record.created_at),
        "endpoint": record.endpoint,
        "method": (record.method or "").strip().upper() or "GET",
        "success_count": 1 if record.status == "success" else 0,
        "failed_count": 1 if record.status == "failed" else 0,
        "rate_limited_count": 1 if record.status == "rate_limited" else 0,
        "total_count": 1,
        "resource_cost_sum": int(record.resource_cost or 0),
        "duration_ms_sum": max(0, int(record.duration_ms or 0)),
        "created_at": record.created_at,
        "updated_at": record.created_at,
    }


def _is_mysql_like(db: Session) -> bool:
    return bool(getattr(getattr(db, "bind", None), "dialect", None)) and db.bind.dialect.name == "mysql"


def _is_sqlite(db: Session) -> bool:
    return bool(getattr(getattr(db, "bind", None), "dialect", None)) and db.bind.dialect.name == "sqlite"


def _upsert_subject_daily_usage(db: Session, payload: dict) -> None:
    if _is_mysql_like(db):
        stmt = mysql_insert(V2SubjectDailyUsage).values(**payload)
        db.execute(
            stmt.on_duplicate_key_update(
                username=stmt.inserted.username,
                user_uuid=stmt.inserted.user_uuid,
                success_count=V2SubjectDailyUsage.success_count + stmt.inserted.success_count,
                failed_count=V2SubjectDailyUsage.failed_count + stmt.inserted.failed_count,
                rate_limited_count=V2SubjectDailyUsage.rate_limited_count + stmt.inserted.rate_limited_count,
                total_count=V2SubjectDailyUsage.total_count + stmt.inserted.total_count,
                resource_cost_sum=V2SubjectDailyUsage.resource_cost_sum + stmt.inserted.resource_cost_sum,
                daily_resource_used=V2SubjectDailyUsage.daily_resource_used + stmt.inserted.daily_resource_used,
                addon_resource_used=V2SubjectDailyUsage.addon_resource_used + stmt.inserted.addon_resource_used,
                deducted_request_count=V2SubjectDailyUsage.deducted_request_count + stmt.inserted.deducted_request_count,
                last_request_at=stmt.inserted.last_request_at,
                updated_at=stmt.inserted.updated_at,
            )
        )
        return

    if _is_sqlite(db):
        stmt = sqlite_insert(V2SubjectDailyUsage).values(**payload)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["usage_date", "subject_type", "subject_key"],
                set_={
                    "username": stmt.excluded.username,
                    "user_uuid": stmt.excluded.user_uuid,
                    "success_count": V2SubjectDailyUsage.success_count + stmt.excluded.success_count,
                    "failed_count": V2SubjectDailyUsage.failed_count + stmt.excluded.failed_count,
                    "rate_limited_count": V2SubjectDailyUsage.rate_limited_count + stmt.excluded.rate_limited_count,
                    "total_count": V2SubjectDailyUsage.total_count + stmt.excluded.total_count,
                    "resource_cost_sum": V2SubjectDailyUsage.resource_cost_sum + stmt.excluded.resource_cost_sum,
                    "daily_resource_used": V2SubjectDailyUsage.daily_resource_used + stmt.excluded.daily_resource_used,
                    "addon_resource_used": V2SubjectDailyUsage.addon_resource_used + stmt.excluded.addon_resource_used,
                    "deducted_request_count": V2SubjectDailyUsage.deducted_request_count + stmt.excluded.deducted_request_count,
                    "last_request_at": stmt.excluded.last_request_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )
        return

    existing = (
        db.query(V2SubjectDailyUsage)
        .filter(
            V2SubjectDailyUsage.usage_date == payload["usage_date"],
            V2SubjectDailyUsage.subject_type == payload["subject_type"],
            V2SubjectDailyUsage.subject_key == payload["subject_key"],
        )
        .first()
    )
    if existing is None:
        db.add(V2SubjectDailyUsage(**payload))
        return

    existing.username = payload["username"]
    existing.user_uuid = payload["user_uuid"]
    existing.success_count += payload["success_count"]
    existing.failed_count += payload["failed_count"]
    existing.rate_limited_count += payload["rate_limited_count"]
    existing.total_count += payload["total_count"]
    existing.resource_cost_sum += payload["resource_cost_sum"]
    existing.daily_resource_used += payload["daily_resource_used"]
    existing.addon_resource_used += payload["addon_resource_used"]
    existing.deducted_request_count += payload["deducted_request_count"]
    existing.last_request_at = payload["last_request_at"]
    existing.updated_at = payload["updated_at"]


def _upsert_endpoint_hourly_stats(db: Session, payload: dict) -> None:
    if _is_mysql_like(db):
        stmt = mysql_insert(V2EndpointHourlyStats).values(**payload)
        db.execute(
            stmt.on_duplicate_key_update(
                success_count=V2EndpointHourlyStats.success_count + stmt.inserted.success_count,
                failed_count=V2EndpointHourlyStats.failed_count + stmt.inserted.failed_count,
                rate_limited_count=V2EndpointHourlyStats.rate_limited_count + stmt.inserted.rate_limited_count,
                total_count=V2EndpointHourlyStats.total_count + stmt.inserted.total_count,
                resource_cost_sum=V2EndpointHourlyStats.resource_cost_sum + stmt.inserted.resource_cost_sum,
                duration_ms_sum=V2EndpointHourlyStats.duration_ms_sum + stmt.inserted.duration_ms_sum,
                updated_at=stmt.inserted.updated_at,
            )
        )
        return

    if _is_sqlite(db):
        stmt = sqlite_insert(V2EndpointHourlyStats).values(**payload)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["stat_hour", "endpoint", "method"],
                set_={
                    "success_count": V2EndpointHourlyStats.success_count + stmt.excluded.success_count,
                    "failed_count": V2EndpointHourlyStats.failed_count + stmt.excluded.failed_count,
                    "rate_limited_count": V2EndpointHourlyStats.rate_limited_count + stmt.excluded.rate_limited_count,
                    "total_count": V2EndpointHourlyStats.total_count + stmt.excluded.total_count,
                    "resource_cost_sum": V2EndpointHourlyStats.resource_cost_sum + stmt.excluded.resource_cost_sum,
                    "duration_ms_sum": V2EndpointHourlyStats.duration_ms_sum + stmt.excluded.duration_ms_sum,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )
        return

    existing = (
        db.query(V2EndpointHourlyStats)
        .filter(
            V2EndpointHourlyStats.stat_hour == payload["stat_hour"],
            V2EndpointHourlyStats.endpoint == payload["endpoint"],
            V2EndpointHourlyStats.method == payload["method"],
        )
        .first()
    )
    if existing is None:
        db.add(V2EndpointHourlyStats(**payload))
        return

    existing.success_count += payload["success_count"]
    existing.failed_count += payload["failed_count"]
    existing.rate_limited_count += payload["rate_limited_count"]
    existing.total_count += payload["total_count"]
    existing.resource_cost_sum += payload["resource_cost_sum"]
    existing.duration_ms_sum += payload["duration_ms_sum"]
    existing.updated_at = payload["updated_at"]


def should_write_usage_event(record: V2RequestMetricRecord) -> bool:
    if record.resource_deducted:
        return True
    if record.status == "rate_limited":
        return True
    if int(record.status_code or 0) >= 500:
        return True
    return False


def record_v2_request_metrics(db: Session, record: V2RequestMetricRecord) -> None:
    extra_payload = record.extra_payload
    if isinstance(extra_payload, (dict, list)):
        extra_payload = json.dumps(extra_payload, ensure_ascii=False, sort_keys=True, default=str)
    elif extra_payload is not None:
        extra_payload = str(extra_payload)

    db.add(
        V2RequestAuditLog(
            request_id=record.request_id,
            username=record.username,
            user_uuid=record.user_uuid,
            actor_type=record.actor_type,
            rate_limit_scope=record.rate_limit_scope,
            quota_scope=record.quota_scope,
            ip=record.ip,
            host=record.host,
            method=record.method,
            endpoint=record.endpoint,
            created_at=record.created_at,
            duration_ms=record.duration_ms,
            status=record.status,
            status_code=record.status_code,
            resource_cost=int(record.resource_cost or 0),
            resource_deducted=bool(record.resource_deducted),
            quota_source=record.quota_source or "none",
            extra_payload=extra_payload,
        )
    )

    subject = resolve_usage_subject(
        user_uuid=record.user_uuid,
        username=record.username,
        client_ip=record.ip,
        actor_type=record.actor_type,
        is_default_test_user=record.is_default_test_user,
    )
    if subject and should_write_usage_event(record):
        subject_type, subject_key = subject
        db.add(V2UsageEvent(**_event_payload(record, subject_type=subject_type, subject_key=subject_key)))

    if not is_v2_metric_endpoint(record.endpoint):
        return

    _upsert_endpoint_hourly_stats(db, _hourly_stats_insert_payload(record))
    if subject:
        subject_type, subject_key = subject
        _upsert_subject_daily_usage(db, _daily_usage_insert_payload(record, subject_type=subject_type, subject_key=subject_key))
