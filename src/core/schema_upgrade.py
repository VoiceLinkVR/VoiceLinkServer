from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from core.afdian_service import afdian_service
from core.config import settings
from core.dependencies import hash_password, normalize_expiration_utc, utcnow_naive
from core.entitlement_service import (
    get_payment_order_snapshot,
    rebuild_user_entitlement_state_from_legacy_fields,
    set_payment_order_snapshot,
    update_user_subscription_state,
)
from core.identity import generate_stable_user_uuid, generate_user_uuid, has_bound_email, is_v2_identity_ready
from core.logging_config import logger
from core.plan_catalog import build_limit_rule, get_addons, get_default_test_plan, get_free_plan
from db.base import Base, engine
from db.models import (
    AccountToken,
    DeviceAuthorization,
    PaymentOrder,
    RefreshToken,
    User,
    UserAddonWallet,
    UserIdentityLink,
    UserSubscriptionState,
    UserSession,
    V2User,
)


def _add_column_if_missing(db: Session, columns: list[str], column_name: str, ddl: str) -> None:
    if column_name in columns:
        return
    db.execute(text(ddl))
    columns.append(column_name)
    logger.info("Added column %s", column_name)


def _drop_column_if_present(db: Session, columns: list[str], table_name: str, column_name: str) -> None:
    if column_name not in columns:
        return
    try:
        db.execute(text(f"ALTER TABLE `{table_name}` DROP COLUMN `{column_name}`"))
        columns.remove(column_name)
        logger.info("Dropped legacy column %s.%s", table_name, column_name)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to drop legacy column %s.%s: %s", table_name, column_name, exc)


def _table_names(db: Session) -> list[str]:
    inspector = inspect(engine)
    try:
        return inspector.get_table_names()
    except Exception:
        rows = db.execute(text("SHOW TABLES")).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]


def _table_columns(db: Session, table_name: str) -> list[str]:
    try:
        rows = db.execute(text(f"SHOW COLUMNS FROM `{table_name}`")).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]
    except Exception:
        inspector = inspect(engine)
        return [str(col["name"]) for col in inspector.get_columns(table_name)]


def _ensure_table_columns(db: Session) -> None:
    table_names = set(_table_names(db))

    if "user" in table_names:
        user_columns = _table_columns(db, "user")
        _drop_column_if_present(db, user_columns, "user", "display_name")
        _add_column_if_missing(db, user_columns, "user_uuid", "ALTER TABLE user ADD COLUMN user_uuid VARCHAR(36)")
        _add_column_if_missing(db, user_columns, "email", "ALTER TABLE user ADD COLUMN email VARCHAR(255)")
        _add_column_if_missing(db, user_columns, "email_verified", "ALTER TABLE user ADD COLUMN email_verified BOOLEAN DEFAULT 0")
        _add_column_if_missing(db, user_columns, "is_default_test_user", "ALTER TABLE user ADD COLUMN is_default_test_user BOOLEAN DEFAULT 0")
        _add_column_if_missing(db, user_columns, "identity_version", "ALTER TABLE user ADD COLUMN identity_version INTEGER DEFAULT 1")
        _add_column_if_missing(db, user_columns, "expiration_date", "ALTER TABLE user ADD COLUMN expiration_date DATETIME")
        _add_column_if_missing(db, user_columns, "is_active", "ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1")
        _add_column_if_missing(db, user_columns, "created_at", "ALTER TABLE user ADD COLUMN created_at DATETIME")
        _add_column_if_missing(db, user_columns, "last_login_at", "ALTER TABLE user ADD COLUMN last_login_at DATETIME")
        _add_column_if_missing(db, user_columns, "boost_expiration_date", "ALTER TABLE user ADD COLUMN boost_expiration_date DATETIME")
        _add_column_if_missing(db, user_columns, "boost_limit_rule", "ALTER TABLE user ADD COLUMN boost_limit_rule VARCHAR(100)")
        _add_column_if_missing(db, user_columns, "addon_resource_balance", "ALTER TABLE user ADD COLUMN addon_resource_balance INTEGER DEFAULT 0")
        _add_column_if_missing(db, user_columns, "addon_resource_total", "ALTER TABLE user ADD COLUMN addon_resource_total INTEGER DEFAULT 0")
        _add_column_if_missing(db, user_columns, "addon_expiration_date", "ALTER TABLE user ADD COLUMN addon_expiration_date DATETIME")

    if "v2_user" in table_names:
        v2_user_columns = _table_columns(db, "v2_user")
        _drop_column_if_present(db, v2_user_columns, "v2_user", "display_name")
        _add_column_if_missing(db, v2_user_columns, "legacy_user_id", "ALTER TABLE v2_user ADD COLUMN legacy_user_id INTEGER")
        _add_column_if_missing(db, v2_user_columns, "username", "ALTER TABLE v2_user ADD COLUMN username VARCHAR(50)")
        _add_column_if_missing(db, v2_user_columns, "password", "ALTER TABLE v2_user ADD COLUMN password VARCHAR(255)")
        _add_column_if_missing(db, v2_user_columns, "email", "ALTER TABLE v2_user ADD COLUMN email VARCHAR(255)")
        _add_column_if_missing(db, v2_user_columns, "email_verified", "ALTER TABLE v2_user ADD COLUMN email_verified BOOLEAN DEFAULT 0")
        _add_column_if_missing(db, v2_user_columns, "identity_version", "ALTER TABLE v2_user ADD COLUMN identity_version INTEGER DEFAULT 2")
        _add_column_if_missing(db, v2_user_columns, "is_admin", "ALTER TABLE v2_user ADD COLUMN is_admin BOOLEAN DEFAULT 0")
        _add_column_if_missing(db, v2_user_columns, "limit_rule", "ALTER TABLE v2_user ADD COLUMN limit_rule VARCHAR(100)")
        _add_column_if_missing(db, v2_user_columns, "expiration_date", "ALTER TABLE v2_user ADD COLUMN expiration_date DATETIME")
        _add_column_if_missing(db, v2_user_columns, "is_active", "ALTER TABLE v2_user ADD COLUMN is_active BOOLEAN DEFAULT 1")
        _add_column_if_missing(db, v2_user_columns, "boost_expiration_date", "ALTER TABLE v2_user ADD COLUMN boost_expiration_date DATETIME")
        _add_column_if_missing(db, v2_user_columns, "boost_limit_rule", "ALTER TABLE v2_user ADD COLUMN boost_limit_rule VARCHAR(100)")
        _add_column_if_missing(db, v2_user_columns, "addon_resource_balance", "ALTER TABLE v2_user ADD COLUMN addon_resource_balance INTEGER DEFAULT 0")
        _add_column_if_missing(db, v2_user_columns, "addon_resource_total", "ALTER TABLE v2_user ADD COLUMN addon_resource_total INTEGER DEFAULT 0")
        _add_column_if_missing(db, v2_user_columns, "addon_expiration_date", "ALTER TABLE v2_user ADD COLUMN addon_expiration_date DATETIME")
        _add_column_if_missing(db, v2_user_columns, "created_at", "ALTER TABLE v2_user ADD COLUMN created_at DATETIME")
        _add_column_if_missing(db, v2_user_columns, "last_login_at", "ALTER TABLE v2_user ADD COLUMN last_login_at DATETIME")

    for table_name, column_name, ddl in [
        ("account_token", "code", "ALTER TABLE account_token ADD COLUMN code VARCHAR(16)"),
        ("account_token", "user_uuid", "ALTER TABLE account_token ADD COLUMN user_uuid VARCHAR(36)"),
        ("user_session", "user_uuid", "ALTER TABLE user_session ADD COLUMN user_uuid VARCHAR(36)"),
        ("refresh_token", "user_uuid", "ALTER TABLE refresh_token ADD COLUMN user_uuid VARCHAR(36)"),
        ("device_authorization", "user_uuid", "ALTER TABLE device_authorization ADD COLUMN user_uuid VARCHAR(36)"),
        ("request_log", "status", "ALTER TABLE request_log ADD COLUMN status VARCHAR(20) DEFAULT 'pending'"),
        ("request_log", "resource_cost", "ALTER TABLE request_log ADD COLUMN resource_cost INTEGER DEFAULT 0"),
        ("request_log", "resource_deducted", "ALTER TABLE request_log ADD COLUMN resource_deducted BOOLEAN DEFAULT 0"),
        ("request_log", "quota_source", "ALTER TABLE request_log ADD COLUMN quota_source VARCHAR(20) DEFAULT 'none'"),
        ("request_log", "user_uuid", "ALTER TABLE request_log ADD COLUMN user_uuid VARCHAR(36)"),
        ("request_log", "actor_type", "ALTER TABLE request_log ADD COLUMN actor_type VARCHAR(32)"),
        ("request_log", "rate_limit_scope", "ALTER TABLE request_log ADD COLUMN rate_limit_scope VARCHAR(32)"),
        ("request_log", "quota_scope", "ALTER TABLE request_log ADD COLUMN quota_scope VARCHAR(32)"),
        ("payment_order", "custom_id_digest", "ALTER TABLE payment_order ADD COLUMN custom_id_digest VARCHAR(64)"),
        ("payment_order", "remark_digest", "ALTER TABLE payment_order ADD COLUMN remark_digest VARCHAR(64)"),
        ("payment_order", "activation_source", "ALTER TABLE payment_order ADD COLUMN activation_source VARCHAR(64)"),
        ("payment_order", "activation_error", "ALTER TABLE payment_order ADD COLUMN activation_error TEXT"),
        ("payment_order", "activation_error_at", "ALTER TABLE payment_order ADD COLUMN activation_error_at DATETIME"),
        ("payment_order", "user_uuid", "ALTER TABLE payment_order ADD COLUMN user_uuid VARCHAR(36)"),
        ("payment_order", "email", "ALTER TABLE payment_order ADD COLUMN email VARCHAR(255)"),
        ("payment_order", "plan_id", "ALTER TABLE payment_order ADD COLUMN plan_id VARCHAR(64)"),
        ("payment_order", "billing_version", "ALTER TABLE payment_order ADD COLUMN billing_version INTEGER DEFAULT 1"),
        ("payment_order", "order_kind", "ALTER TABLE payment_order ADD COLUMN order_kind VARCHAR(32)"),
        ("payment_order", "pricing_mode_snapshot", "ALTER TABLE payment_order ADD COLUMN pricing_mode_snapshot VARCHAR(32)"),
        ("payment_order", "plan_ref", "ALTER TABLE payment_order ADD COLUMN plan_ref VARCHAR(100)"),
        ("payment_order", "plan_price_snapshot", "ALTER TABLE payment_order ADD COLUMN plan_price_snapshot FLOAT"),
        ("payment_order", "months_snapshot", "ALTER TABLE payment_order ADD COLUMN months_snapshot INTEGER"),
        ("payment_order", "expected_amount_snapshot", "ALTER TABLE payment_order ADD COLUMN expected_amount_snapshot FLOAT"),
        ("payment_order", "paid_amount_snapshot", "ALTER TABLE payment_order ADD COLUMN paid_amount_snapshot FLOAT"),
        ("payment_order", "daily_quota_snapshot", "ALTER TABLE payment_order ADD COLUMN daily_quota_snapshot INTEGER"),
        ("payment_order", "rate_limit_rule_snapshot", "ALTER TABLE payment_order ADD COLUMN rate_limit_rule_snapshot VARCHAR(255)"),
        ("payment_order", "addon_resource_snapshot", "ALTER TABLE payment_order ADD COLUMN addon_resource_snapshot INTEGER"),
        ("payment_order", "catalog_version_snapshot", "ALTER TABLE payment_order ADD COLUMN catalog_version_snapshot VARCHAR(64)"),
        ("payment_order", "entitlement_snapshot_json", "ALTER TABLE payment_order ADD COLUMN entitlement_snapshot_json TEXT"),
        ("v2_request_log", "request_id", "ALTER TABLE v2_request_log ADD COLUMN request_id VARCHAR(64)"),
        ("v2_request_log", "username", "ALTER TABLE v2_request_log ADD COLUMN username VARCHAR(50)"),
        ("v2_request_log", "user_uuid", "ALTER TABLE v2_request_log ADD COLUMN user_uuid VARCHAR(36)"),
        ("v2_request_log", "actor_type", "ALTER TABLE v2_request_log ADD COLUMN actor_type VARCHAR(32)"),
        ("v2_request_log", "rate_limit_scope", "ALTER TABLE v2_request_log ADD COLUMN rate_limit_scope VARCHAR(32)"),
        ("v2_request_log", "quota_scope", "ALTER TABLE v2_request_log ADD COLUMN quota_scope VARCHAR(32)"),
        ("v2_request_log", "host", "ALTER TABLE v2_request_log ADD COLUMN host VARCHAR(255)"),
        ("v2_request_log", "method", "ALTER TABLE v2_request_log ADD COLUMN method VARCHAR(16)"),
        ("v2_request_log", "status", "ALTER TABLE v2_request_log ADD COLUMN status VARCHAR(20) DEFAULT 'pending'"),
        ("v2_request_log", "status_code", "ALTER TABLE v2_request_log ADD COLUMN status_code INTEGER"),
        ("v2_request_log", "resource_cost", "ALTER TABLE v2_request_log ADD COLUMN resource_cost INTEGER DEFAULT 0"),
        ("v2_request_log", "resource_deducted", "ALTER TABLE v2_request_log ADD COLUMN resource_deducted BOOLEAN DEFAULT 0"),
        ("v2_request_log", "quota_source", "ALTER TABLE v2_request_log ADD COLUMN quota_source VARCHAR(20) DEFAULT 'none'"),
        ("v2_request_log", "extra_payload", "ALTER TABLE v2_request_log ADD COLUMN extra_payload TEXT"),
        ("announcement_entry", "show_in_overview", "ALTER TABLE announcement_entry ADD COLUMN show_in_overview BOOLEAN DEFAULT 0"),
    ]:
        if table_name not in table_names:
            continue
        columns = _table_columns(db, table_name)
        _add_column_if_missing(db, columns, column_name, ddl)


def _ensure_support_ticket_columns(db: Session) -> None:
    table_names = set(_table_names(db))
    if "v2_support_ticket_attachment" not in table_names:
        return
    columns = _table_columns(db, "v2_support_ticket_attachment")
    _add_column_if_missing(
        db,
        columns,
        "event_id",
        "ALTER TABLE v2_support_ticket_attachment ADD COLUMN event_id INTEGER",
    )


def _backfill_users(db: Session) -> None:
    free_plan = get_free_plan()
    for user in db.query(User).all():
        if not (user.user_uuid or "").strip():
            user.user_uuid = generate_stable_user_uuid(user.id, user.username)
        if bool(getattr(user, "is_default_test_user", False)):
            user.identity_version = 1
        elif has_bound_email(getattr(user, "email", None)) and int(getattr(user, "identity_version", 1) or 1) < 2:
            user.identity_version = 2
        elif getattr(user, "identity_version", None) in (None, 0):
            user.identity_version = 2 if is_v2_identity_ready(email=user.email, identity_version=user.identity_version) else 1
        if user.limit_rule is None:
            user.limit_rule = build_limit_rule(free_plan.get("rate_limit"), free_plan["id"], free_plan["daily_quota"])
        if user.is_active is None:
            user.is_active = True
        if user.created_at is None:
            user.created_at = utcnow_naive()


def _build_v2_username(users_by_username: set[str], email: str, fallback_username: str, user_uuid: str) -> str:
    local_part = (email or "").split("@", 1)[0].strip()
    if not local_part:
        local_part = (fallback_username or "").strip()
    normalized = "".join(char if char.isalnum() or char in "._-" else "_" for char in local_part).strip("._-") or "user"
    normalized = normalized[:32]
    candidate = normalized
    counter = 1
    while candidate in users_by_username:
        suffix = f"_{counter}"
        candidate = f"{normalized[: max(1, 50 - len(suffix))]}{suffix}"
        counter += 1
    users_by_username.add(candidate)
    return candidate


def _backfill_v2_users(db: Session) -> None:
    free_plan = get_free_plan()
    existing_v2_users = {user.user_uuid: user for user in db.query(V2User).all() if (user.user_uuid or "").strip()}
    used_usernames = {(user.username or "").strip() for user in db.query(V2User).all() if (user.username or "").strip()}

    for legacy_user in db.query(User).all():
        if bool(getattr(legacy_user, "is_default_test_user", False)):
            continue
        if not has_bound_email(getattr(legacy_user, "email", None)):
            continue

        user_uuid = (legacy_user.user_uuid or "").strip() or generate_stable_user_uuid(legacy_user.id, legacy_user.username)
        v2_user = existing_v2_users.get(user_uuid)
        if v2_user is None:
            username = _build_v2_username(used_usernames, legacy_user.email or "", legacy_user.username or "", user_uuid)
            v2_user = V2User(
                user_uuid=user_uuid,
                legacy_user_id=legacy_user.id,
                username=username,
                password=legacy_user.password,
                email=legacy_user.email,
                email_verified=bool(getattr(legacy_user, "email_verified", False)),
                identity_version=2,
                is_admin=bool(getattr(legacy_user, "is_admin", False)),
                limit_rule=legacy_user.limit_rule or build_limit_rule(free_plan.get("rate_limit"), free_plan["id"], free_plan["daily_quota"]),
                expiration_date=legacy_user.expiration_date,
                is_active=True if legacy_user.is_active is None else bool(legacy_user.is_active),
                boost_expiration_date=legacy_user.boost_expiration_date,
                boost_limit_rule=legacy_user.boost_limit_rule,
                addon_resource_balance=int(getattr(legacy_user, "addon_resource_balance", 0) or 0),
                addon_resource_total=int(getattr(legacy_user, "addon_resource_total", 0) or 0),
                addon_expiration_date=legacy_user.addon_expiration_date,
                created_at=legacy_user.created_at or utcnow_naive(),
                last_login_at=legacy_user.last_login_at,
            )
            db.add(v2_user)
            db.flush()
            existing_v2_users[user_uuid] = v2_user
            continue

        v2_user.legacy_user_id = v2_user.legacy_user_id or legacy_user.id
        v2_user.password = v2_user.password or legacy_user.password
        v2_user.email = v2_user.email or legacy_user.email
        v2_user.email_verified = bool(v2_user.email_verified or getattr(legacy_user, "email_verified", False))
        v2_user.identity_version = 2
        if not v2_user.limit_rule:
            v2_user.limit_rule = legacy_user.limit_rule or build_limit_rule(free_plan.get("rate_limit"), free_plan["id"], free_plan["daily_quota"])
        if v2_user.expiration_date is None or (legacy_user.expiration_date and legacy_user.expiration_date > v2_user.expiration_date):
            v2_user.expiration_date = legacy_user.expiration_date
        v2_user.is_active = True if v2_user.is_active is None else bool(v2_user.is_active)
        v2_user.boost_expiration_date = v2_user.boost_expiration_date or legacy_user.boost_expiration_date
        v2_user.boost_limit_rule = v2_user.boost_limit_rule or legacy_user.boost_limit_rule
        v2_user.addon_resource_balance = max(int(v2_user.addon_resource_balance or 0), int(getattr(legacy_user, "addon_resource_balance", 0) or 0))
        v2_user.addon_resource_total = max(int(v2_user.addon_resource_total or 0), int(getattr(legacy_user, "addon_resource_total", 0) or 0))
        if v2_user.addon_expiration_date is None or (legacy_user.addon_expiration_date and legacy_user.addon_expiration_date > v2_user.addon_expiration_date):
            v2_user.addon_expiration_date = legacy_user.addon_expiration_date
        v2_user.created_at = v2_user.created_at or legacy_user.created_at or utcnow_naive()
        v2_user.last_login_at = v2_user.last_login_at or legacy_user.last_login_at


def _ensure_upgraded_user_identity_links(db: Session) -> None:
    linked_usernames = {
        (record.legacy_username or "").strip()
        for record in db.query(UserIdentityLink).filter(UserIdentityLink.status == "linked").all()
        if (record.legacy_username or "").strip()
    }

    for user in db.query(User).all():
        if bool(getattr(user, "is_default_test_user", False)):
            continue
        if not has_bound_email(getattr(user, "email", None)):
            continue
        legacy_username = (user.username or "").strip()
        if not legacy_username or legacy_username in linked_usernames:
            continue

        db.add(
            UserIdentityLink(
                user_uuid=user.user_uuid,
                legacy_user_id=user.id,
                legacy_username=legacy_username,
                legacy_email=user.email,
                link_type="legacy_password_bind",
                status="linked",
                linked_at=utcnow_naive(),
                audit_note="schema_upgrade_email_bound",
            )
        )
        linked_usernames.add(legacy_username)


def _backfill_related_tables(db: Session) -> None:
    users_by_username = {user.username: user for user in db.query(User).all()}
    users_by_id = {user.id: user for user in db.query(User).all()}

    for record in db.query(AccountToken).all():
        if not record.user_uuid and record.username in users_by_username:
            user = users_by_username[record.username]
            record.user_uuid = user.user_uuid
            if not record.email and user.email:
                record.email = user.email

    for record in db.query(UserSession).all():
        if not record.user_uuid and record.user_id in users_by_id:
            record.user_uuid = users_by_id[record.user_id].user_uuid

    for record in db.query(RefreshToken).all():
        if not record.user_uuid and record.user_id in users_by_id:
            record.user_uuid = users_by_id[record.user_id].user_uuid

    for record in db.query(DeviceAuthorization).all():
        if not record.user_uuid and record.user_id in users_by_id:
            record.user_uuid = users_by_id[record.user_id].user_uuid

    for record in db.query(PaymentOrder).all():
        user = users_by_username.get(record.username or "")
        if user is not None and not record.user_uuid:
            record.user_uuid = user.user_uuid
        if user is not None and not record.email and user.email:
            record.email = user.email
        if not record.billing_version:
            record.billing_version = 1


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_legacy_custom_payload(order: PaymentOrder) -> dict:
    if order.custom_id:
        try:
            parsed = afdian_service.parse_custom_id(order.custom_id)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    raw_payload = getattr(order, "raw_payload", None)
    if not raw_payload:
        return {}
    try:
        parsed = json.loads(raw_payload)
    except Exception:
        return {}

    if isinstance(parsed, dict):
        custom_payload = parsed.get("custom_payload")
        if isinstance(custom_payload, dict):
            merged = dict(parsed)
            merged.update(custom_payload)
            return merged
        return parsed
    return {}


def _calculate_migrated_addon_resource_amount(amount: float | None) -> int:
    addons = get_addons()
    if not addons:
        return 0
    anchor = addons[0]
    anchor_price = _safe_float(anchor.get("price")) or 0
    anchor_resource = _safe_int(anchor.get("resource_amount")) or 0
    if anchor_price <= 0 or anchor_resource <= 0 or amount is None or amount <= 0:
        return 0
    units = int(round(amount / anchor_price))
    if units <= 0:
        return 0
    return units * anchor_resource


def _extract_daily_limit_from_rule(limit_rule: str | None) -> int | None:
    for part in (segment.strip() for segment in str(limit_rule or "").split(";") if segment.strip()):
        lowered = part.lower()
        if lowered.endswith("/day"):
            value = part.split("/", 1)[0].strip()
            return _safe_int(value)
    return None


def _latest_subscription_order_map(db: Session) -> dict[str, PaymentOrder]:
    result: dict[str, PaymentOrder] = {}
    orders = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.status == "activated")
        .order_by(PaymentOrder.activated_at.desc(), PaymentOrder.updated_at.desc(), PaymentOrder.id.desc())
        .all()
    )
    for order in orders:
        snapshot = get_payment_order_snapshot(order)
        if str(snapshot.get("order_kind") or "").strip().lower() != "subscription":
            continue
        keys = []
        if (order.user_uuid or "").strip():
            keys.append(order.user_uuid.strip())
        if (order.username or "").strip():
            keys.append(order.username.strip())
        for key in keys:
            result.setdefault(key, order)
    return result


def _backfill_entitlement_state_tables(db: Session) -> None:
    latest_orders = _latest_subscription_order_map(db)
    for user in db.query(User).all():
        rebuild_user_entitlement_state_from_legacy_fields(db, user)
        expires_at_utc = normalize_expiration_utc(getattr(user, "expiration_date", None))
        if expires_at_utc is not None and expires_at_utc <= datetime.now(timezone.utc):
            continue
        latest_order = latest_orders.get((user.user_uuid or "").strip()) or latest_orders.get((user.username or "").strip())
        if latest_order is None:
            continue
        snapshot = get_payment_order_snapshot(latest_order)
        plan_price = _safe_float(snapshot.get("plan_price"))
        if plan_price is None or plan_price <= 0:
            continue
        daily_quota = _safe_int(snapshot.get("daily_quota"))
        if daily_quota is None:
            daily_quota = _extract_daily_limit_from_rule(getattr(user, "limit_rule", None)) or 0
        update_user_subscription_state(
            db,
            user,
            plan_price=plan_price,
            expires_at=getattr(user, "expiration_date", None),
            source_order_id=latest_order.id,
            pricing_mode=str(snapshot.get("pricing_mode_snapshot") or "") or None,
            status="active" if bool(getattr(user, "is_active", True)) else "inactive",
            plan_ref=str(snapshot.get("plan_ref") or ""),
            daily_quota=daily_quota,
            rate_limit_rule=str(snapshot.get("rate_limit_rule") or ""),
            catalog_version=str(snapshot.get("catalog_version") or ""),
        )
    for user in db.query(V2User).all():
        rebuild_user_entitlement_state_from_legacy_fields(db, user)
        expires_at_utc = normalize_expiration_utc(getattr(user, "expiration_date", None))
        if expires_at_utc is not None and expires_at_utc <= datetime.now(timezone.utc):
            continue
        latest_order = latest_orders.get((user.user_uuid or "").strip()) or latest_orders.get((user.username or "").strip())
        if latest_order is None:
            continue
        snapshot = get_payment_order_snapshot(latest_order)
        plan_price = _safe_float(snapshot.get("plan_price"))
        if plan_price is None or plan_price <= 0:
            continue
        daily_quota = _safe_int(snapshot.get("daily_quota"))
        if daily_quota is None:
            daily_quota = _extract_daily_limit_from_rule(getattr(user, "limit_rule", None)) or 0
        update_user_subscription_state(
            db,
            user,
            plan_price=plan_price,
            expires_at=getattr(user, "expiration_date", None),
            source_order_id=latest_order.id,
            pricing_mode=str(snapshot.get("pricing_mode_snapshot") or "") or None,
            status="active" if bool(getattr(user, "is_active", True)) else "inactive",
            plan_ref=str(snapshot.get("plan_ref") or ""),
            daily_quota=daily_quota,
            rate_limit_rule=str(snapshot.get("rate_limit_rule") or ""),
            catalog_version=str(snapshot.get("catalog_version") or ""),
        )


def _backfill_payment_order_snapshots(db: Session) -> None:
    for order in db.query(PaymentOrder).all():
        has_snapshot = getattr(order, "plan_price_snapshot", None) is not None and getattr(order, "order_kind", None)
        needs_normalization = str(getattr(order, "plan_ref", "") or "").startswith("legacy-") or str(getattr(order, "pricing_mode_snapshot", "") or "").strip().lower() == "legacy_migrated"
        if has_snapshot and not needs_normalization:
            if getattr(order, "paid_amount_snapshot", None) is None and order.amount is not None:
                order.paid_amount_snapshot = _safe_float(order.amount)
            continue

        custom_payload = _extract_legacy_custom_payload(order)
        action = str(custom_payload.get("action") or order.action or "").strip().lower()
        if not action:
            continue

        months = _safe_int(custom_payload.get("months")) or 1
        raw_plan_price = custom_payload.get("plan_price")
        if raw_plan_price is None:
            raw_plan_price = custom_payload.get("target_monthly_price")
        if raw_plan_price is None and order.amount is not None:
            raw_plan_price = order.amount if action == "addon" else (float(order.amount) / max(1, months))
        plan_price = _safe_float(raw_plan_price)
        expected_amount = _safe_float(custom_payload.get("requested_amount"))
        if expected_amount is None:
            expected_amount = _safe_float(order.amount)
        paid_amount = _safe_float(order.amount)
        limit_rule = str(custom_payload.get("limit_rule") or "").strip() or None
        daily_quota = _extract_daily_limit_from_rule(limit_rule)
        rate_limit_rule = None
        if limit_rule:
            rate_limit_rule = ";".join(
                [
                    part
                    for part in (segment.strip() for segment in limit_rule.split(";") if segment.strip())
                    if not part.lower().startswith("plan_id=") and not part.lower().endswith("/day")
                ]
            )

        addon_resource_amount = _safe_int(custom_payload.get("addon_resource_amount")) or 0
        if action == "addon" and addon_resource_amount <= 0:
            addon_resource_amount = _calculate_migrated_addon_resource_amount(plan_price)

        try:
            set_payment_order_snapshot(
                order,
                action=action,
                plan_price=plan_price,
                months=months,
                expected_amount=expected_amount,
                paid_amount=paid_amount,
                addon_resource_amount=addon_resource_amount,
                pricing_mode_snapshot=None,
                daily_quota=daily_quota,
                rate_limit_rule=rate_limit_rule,
                catalog_version_snapshot=None,
            )
        except Exception:
            logger.exception("Failed to backfill payment order snapshot. order_id=%s", order.id)


def _ensure_default_test_v2_user(db: Session) -> None:
    if not settings.DEFAULT_TEST_USER_ENABLED:
        return

    plan = get_default_test_plan()
    generated_password = generate_user_uuid()
    user = db.query(V2User).filter(V2User.user_uuid == settings.DEFAULT_TEST_USER_UUID).first()
    if user is None and settings.DEFAULT_TEST_USER_USERNAME:
        user = db.query(V2User).filter(V2User.username == settings.DEFAULT_TEST_USER_USERNAME).first()

    if user is None:
        user = V2User(
            user_uuid=settings.DEFAULT_TEST_USER_UUID,
            username=settings.DEFAULT_TEST_USER_USERNAME,
            password=hash_password(generated_password),
            email=settings.DEFAULT_TEST_USER_EMAIL,
            email_verified=False,
            is_default_test_user=True,
            identity_version=2,
            is_admin=False,
            is_active=bool(settings.DEFAULT_TEST_USER_ACTIVE),
            limit_rule=build_limit_rule(plan.get("rate_limit"), plan["id"], plan["daily_quota"]),
            created_at=utcnow_naive(),
        )
        db.add(user)
        db.flush()
        logger.info("Created v2 internal guest principal %s", user.user_uuid)
    else:
        user.user_uuid = settings.DEFAULT_TEST_USER_UUID
        user.username = settings.DEFAULT_TEST_USER_USERNAME or user.username
        user.password = hash_password(generated_password)
        user.email = settings.DEFAULT_TEST_USER_EMAIL
        user.email_verified = False
        user.is_default_test_user = True
        user.identity_version = 2
        user.is_admin = False
        user.is_active = bool(settings.DEFAULT_TEST_USER_ACTIVE)
        user.limit_rule = build_limit_rule(plan.get("rate_limit"), plan["id"], plan["daily_quota"])

    rebuild_user_entitlement_state_from_legacy_fields(db, user)


def run_schema_upgrade() -> None:
    Base.metadata.create_all(bind=engine)
    db = Session(bind=engine)
    try:
        _ensure_support_ticket_columns(db)
        _ensure_default_test_v2_user(db)
        db.commit()
        logger.info(
            "Schema upgrade completed. v2_users=%s v2_orders=%s",
            db.query(V2User).count(),
            db.query(PaymentOrder).count(),
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
