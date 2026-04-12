from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from core.config import settings
from core.dependencies import hash_password, utcnow_naive
from core.identity import (
    build_default_display_name,
    generate_stable_user_uuid,
    generate_user_uuid,
    has_bound_email,
    is_v2_identity_ready,
)
from core.logging_config import logger
from core.plan_catalog import build_limit_rule, get_default_test_plan, get_free_plan
from db.base import Base, engine
from db.models import (
    AccountToken,
    DeviceAuthorization,
    PaymentOrder,
    RefreshToken,
    RequestLog,
    User,
    UserIdentityLink,
    UserSession,
    V2RequestLog,
    V2User,
)


def _add_column_if_missing(db: Session, columns: list[str], column_name: str, ddl: str) -> None:
    if column_name in columns:
        return
    db.execute(text(ddl))
    columns.append(column_name)
    logger.info("Added column %s", column_name)


def _ensure_table_columns(db: Session) -> None:
    inspector = inspect(engine)

    if "user" in inspector.get_table_names():
        user_columns = [col["name"] for col in inspector.get_columns("user")]
        _add_column_if_missing(db, user_columns, "user_uuid", "ALTER TABLE user ADD COLUMN user_uuid VARCHAR(36)")
        _add_column_if_missing(db, user_columns, "email", "ALTER TABLE user ADD COLUMN email VARCHAR(255)")
        _add_column_if_missing(db, user_columns, "email_verified", "ALTER TABLE user ADD COLUMN email_verified BOOLEAN DEFAULT 0")
        _add_column_if_missing(db, user_columns, "display_name", "ALTER TABLE user ADD COLUMN display_name VARCHAR(100)")
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

    if "v2_user" in inspector.get_table_names():
        v2_user_columns = [col["name"] for col in inspector.get_columns("v2_user")]
        _add_column_if_missing(db, v2_user_columns, "legacy_user_id", "ALTER TABLE v2_user ADD COLUMN legacy_user_id INTEGER")
        _add_column_if_missing(db, v2_user_columns, "username", "ALTER TABLE v2_user ADD COLUMN username VARCHAR(50)")
        _add_column_if_missing(db, v2_user_columns, "password", "ALTER TABLE v2_user ADD COLUMN password VARCHAR(255)")
        _add_column_if_missing(db, v2_user_columns, "email", "ALTER TABLE v2_user ADD COLUMN email VARCHAR(255)")
        _add_column_if_missing(db, v2_user_columns, "email_verified", "ALTER TABLE v2_user ADD COLUMN email_verified BOOLEAN DEFAULT 0")
        _add_column_if_missing(db, v2_user_columns, "display_name", "ALTER TABLE v2_user ADD COLUMN display_name VARCHAR(100)")
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
    ]:
        if table_name not in inspector.get_table_names():
            continue
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        _add_column_if_missing(db, columns, column_name, ddl)


def _backfill_users(db: Session) -> None:
    free_plan = get_free_plan()
    for user in db.query(User).all():
        if not (user.user_uuid or "").strip():
            user.user_uuid = generate_stable_user_uuid(user.id, user.username)
        if not (getattr(user, "display_name", None) or "").strip():
            user.display_name = build_default_display_name(user_uuid=user.user_uuid, user_id=user.id)
        if bool(getattr(user, "is_default_test_user", False)):
            user.identity_version = 1
        elif has_bound_email(getattr(user, "email", None)) and int(getattr(user, "identity_version", 1) or 1) < 2:
            user.identity_version = 2
        elif getattr(user, "identity_version", None) in (None, 0):
            user.identity_version = 2 if is_v2_identity_ready(email=user.email, identity_version=user.identity_version) else 1
        if user.limit_rule is None:
            user.limit_rule = build_limit_rule(settings.FREE_PLAN_LIMIT_RULE, free_plan["id"], free_plan["daily_quota"])
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
                display_name=(getattr(legacy_user, "display_name", None) or "").strip() or build_default_display_name(user_uuid=user_uuid, user_id=legacy_user.id),
                identity_version=2,
                is_admin=bool(getattr(legacy_user, "is_admin", False)),
                limit_rule=legacy_user.limit_rule or build_limit_rule(settings.FREE_PLAN_LIMIT_RULE, free_plan["id"], free_plan["daily_quota"]),
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
        if not (v2_user.display_name or "").strip():
            v2_user.display_name = (getattr(legacy_user, "display_name", None) or "").strip() or build_default_display_name(user_uuid=user_uuid, user_id=legacy_user.id)
        v2_user.email_verified = bool(v2_user.email_verified or getattr(legacy_user, "email_verified", False))
        v2_user.identity_version = 2
        if not v2_user.limit_rule:
            v2_user.limit_rule = legacy_user.limit_rule or build_limit_rule(settings.FREE_PLAN_LIMIT_RULE, free_plan["id"], free_plan["daily_quota"])
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

    for record in db.query(RequestLog).all():
        if not record.user_uuid and record.username in users_by_username:
            user = users_by_username[record.username]
            record.user_uuid = user.user_uuid
        if not record.actor_type:
            record.actor_type = "authenticated"
        if not record.rate_limit_scope:
            record.rate_limit_scope = "user"
        if not record.quota_scope:
            record.quota_scope = "user"

    for record in db.query(PaymentOrder).all():
        user = users_by_username.get(record.username or "")
        if user is not None and not record.user_uuid:
            record.user_uuid = user.user_uuid
        if user is not None and not record.email and user.email:
            record.email = user.email
        if not record.billing_version:
            record.billing_version = 1

    v2_users_by_uuid = {user.user_uuid: user for user in db.query(V2User).all()}

    for record in db.query(V2RequestLog).all():
        if not record.user_uuid and record.username:
            user = next((item for item in v2_users_by_uuid.values() if item.username == record.username), None)
            if user is not None:
                record.user_uuid = user.user_uuid
        if not record.actor_type:
            record.actor_type = "authenticated"
        if not record.rate_limit_scope:
            record.rate_limit_scope = "user"
        if not record.quota_scope:
            record.quota_scope = "user"


def _ensure_default_test_user(db: Session) -> None:
    if not settings.DEFAULT_TEST_USER_ENABLED:
        return

    plan = get_default_test_plan()
    user = db.query(User).filter(User.user_uuid == settings.DEFAULT_TEST_USER_UUID).first()
    if user is None and settings.DEFAULT_TEST_USER_USERNAME:
        user = db.query(User).filter(User.username == settings.DEFAULT_TEST_USER_USERNAME).first()

    if user is None:
        password = settings.DEFAULT_TEST_USER_PASSWORD or generate_user_uuid()
        user = User(
            user_uuid=settings.DEFAULT_TEST_USER_UUID,
            username=settings.DEFAULT_TEST_USER_USERNAME,
            password=hash_password(password),
            email=settings.DEFAULT_TEST_USER_EMAIL,
            email_verified=False,
            display_name=settings.DEFAULT_TEST_USER_DISPLAY_NAME,
            is_default_test_user=True,
            identity_version=1,
            is_active=bool(settings.DEFAULT_TEST_USER_ACTIVE),
            limit_rule=build_limit_rule(settings.FREE_PLAN_LIMIT_RULE, plan["id"], plan["daily_quota"]),
            created_at=utcnow_naive(),
        )
        db.add(user)
        db.flush()
        logger.info("Created default test user %s", user.user_uuid)
    else:
        user.user_uuid = settings.DEFAULT_TEST_USER_UUID
        user.username = settings.DEFAULT_TEST_USER_USERNAME or user.username
        user.email = user.email or settings.DEFAULT_TEST_USER_EMAIL
        user.display_name = user.display_name or settings.DEFAULT_TEST_USER_DISPLAY_NAME
        user.is_default_test_user = True
        user.identity_version = 1
        user.is_active = bool(settings.DEFAULT_TEST_USER_ACTIVE)
        user.limit_rule = build_limit_rule(settings.FREE_PLAN_LIMIT_RULE, plan["id"], plan["daily_quota"])


def run_schema_upgrade() -> None:
    Base.metadata.create_all(bind=engine)
    db = Session(bind=engine)
    try:
        legacy_user_count_before = db.query(User).count()
        v2_user_count_before = db.query(V2User).count()
        _ensure_table_columns(db)
        db.flush()
        _backfill_users(db)
        _backfill_v2_users(db)
        _ensure_upgraded_user_identity_links(db)
        _backfill_related_tables(db)
        _ensure_default_test_user(db)
        db.commit()
        legacy_user_count_after = db.query(User).count()
        v2_user_count_after = db.query(V2User).count()
        logger.info(
            "Schema upgrade completed. legacy_users=%s->%s v2_users=%s->%s",
            legacy_user_count_before,
            legacy_user_count_after,
            v2_user_count_before,
            v2_user_count_after,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
