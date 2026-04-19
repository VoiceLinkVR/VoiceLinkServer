import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text

from core.identity import generate_user_uuid

from .base import Base


def normalize_payment_lookup_value(value: Optional[str]) -> Optional[str]:
    normalized = (value or "").strip()
    return normalized or None


def payment_lookup_digest(value: Optional[str]) -> Optional[str]:
    normalized = normalize_payment_lookup_value(value)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def set_payment_order_custom_id(order: "PaymentOrder", custom_id: Optional[str]) -> None:
    normalized = normalize_payment_lookup_value(custom_id)
    order.custom_id = normalized
    order.custom_id_digest = payment_lookup_digest(normalized)


def set_payment_order_remark(order: "PaymentOrder", remark: Optional[str]) -> None:
    normalized = normalize_payment_lookup_value(remark)
    order.remark = normalized
    order.remark_digest = payment_lookup_digest(normalized)


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), unique=True, nullable=False, index=True, default=generate_user_uuid)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    email_verified = Column(Boolean, default=False, nullable=False)
    is_default_test_user = Column(Boolean, default=False, nullable=False, index=True)
    identity_version = Column(Integer, default=1, nullable=False)
    is_admin = Column(Boolean, default=False)
    limit_rule = Column(String(100), nullable=True)
    expiration_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    boost_expiration_date = Column(DateTime, nullable=True)
    boost_limit_rule = Column(String(100), nullable=True)
    addon_resource_balance = Column(Integer, nullable=False, default=0)
    addon_resource_total = Column(Integer, nullable=False, default=0)
    addon_expiration_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=True)
    last_login_at = Column(DateTime, nullable=True)


class V2User(Base):
    __tablename__ = "v2_user"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), unique=True, nullable=False, index=True, default=generate_user_uuid)
    legacy_user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    email_verified = Column(Boolean, default=False, nullable=False)
    identity_version = Column(Integer, default=2, nullable=False)
    is_admin = Column(Boolean, default=False)
    limit_rule = Column(String(100), nullable=True)
    expiration_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    boost_expiration_date = Column(DateTime, nullable=True)
    boost_limit_rule = Column(String(100), nullable=True)
    addon_resource_balance = Column(Integer, nullable=False, default=0)
    addon_resource_total = Column(Integer, nullable=False, default=0)
    addon_expiration_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=True)
    last_login_at = Column(DateTime, nullable=True)


class UserIdentityLink(Base):
    __tablename__ = "user_identity_link"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), nullable=False, index=True)
    legacy_user_id = Column(Integer, nullable=True, index=True)
    legacy_username = Column(String(50), nullable=True, index=True)
    legacy_email = Column(String(255), nullable=True)
    link_type = Column(String(32), nullable=False, default="legacy_password_bind")
    status = Column(String(32), nullable=False, default="linked", index=True)
    linked_at = Column(DateTime, default=datetime.now, nullable=False)
    linked_by_ip = Column(String(64), nullable=True)
    audit_note = Column(Text, nullable=True)


class AccountToken(Base):
    __tablename__ = "account_token"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(50), nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    purpose = Column(String(32), nullable=False, index=True)
    code = Column(String(16), nullable=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class UserSession(Base):
    __tablename__ = "user_session"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    session_type = Column(String(32), nullable=False, default="portal", index=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.now, nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    session_id = Column(String(128), nullable=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class DeviceAuthorization(Base):
    __tablename__ = "device_authorization"

    id = Column(Integer, primary_key=True, index=True)
    device_code = Column(String(128), unique=True, nullable=False, index=True)
    user_code = Column(String(32), unique=True, nullable=False, index=True)
    client_name = Column(String(128), nullable=True)
    requested_scope = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    user_id = Column(Integer, nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    approved_at = Column(DateTime, nullable=True)
    denied_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_polled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class RequestLog(Base):
    __tablename__ = "request_log"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    actor_type = Column(String(32), nullable=True, default="authenticated")
    rate_limit_scope = Column(String(32), nullable=True)
    quota_scope = Column(String(32), nullable=True)
    ip = Column(String(45), nullable=False)
    endpoint = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    duration = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, index=True)
    resource_cost = Column(Integer, nullable=False, default=0, index=True)
    resource_deducted = Column(Boolean, nullable=False, default=False, index=True)
    quota_source = Column(String(20), nullable=False, default="none", index=True)


class V2RequestLog(Base):
    __tablename__ = "v2_request_log"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    username = Column(String(50), nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    actor_type = Column(String(32), nullable=True, default="authenticated")
    rate_limit_scope = Column(String(32), nullable=True)
    quota_scope = Column(String(32), nullable=True)
    ip = Column(String(45), nullable=False, index=True)
    host = Column(String(255), nullable=True, index=True)
    method = Column(String(16), nullable=True, index=True)
    endpoint = Column(String(255), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    duration = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, index=True)
    status_code = Column(Integer, nullable=True, index=True)
    resource_cost = Column(Integer, nullable=False, default=0, index=True)
    resource_deducted = Column(Boolean, nullable=False, default=False, index=True)
    quota_source = Column(String(20), nullable=False, default="none", index=True)
    extra_payload = Column(Text, nullable=True)


class PaymentOrder(Base):
    __tablename__ = "payment_order"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(128), nullable=True, unique=True, index=True)
    out_trade_no = Column(String(128), nullable=True, unique=True, index=True)
    custom_id = Column(Text, nullable=True)
    custom_id_digest = Column(String(64), nullable=True, unique=True, index=True)
    remark = Column(Text, nullable=True)
    remark_digest = Column(String(64), nullable=True, index=True)
    username = Column(String(50), nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    email = Column(String(255), nullable=True)
    plan_id = Column(String(64), nullable=True, index=True)
    billing_version = Column(Integer, nullable=False, default=1)
    action = Column(String(32), nullable=True)
    order_kind = Column(String(32), nullable=True, index=True)
    pricing_mode_snapshot = Column(String(32), nullable=True)
    plan_ref = Column(String(100), nullable=True, index=True)
    plan_price_snapshot = Column(Float, nullable=True)
    months_snapshot = Column(Integer, nullable=True)
    expected_amount_snapshot = Column(Float, nullable=True)
    paid_amount_snapshot = Column(Float, nullable=True)
    daily_quota_snapshot = Column(Integer, nullable=True)
    rate_limit_rule_snapshot = Column(String(255), nullable=True)
    addon_resource_snapshot = Column(Integer, nullable=True)
    catalog_version_snapshot = Column(String(64), nullable=True)
    entitlement_snapshot_json = Column(Text, nullable=True)
    amount = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    activated_at = Column(DateTime, nullable=True)
    activation_source = Column(String(64), nullable=True, index=True)
    activation_error = Column(Text, nullable=True)
    activation_error_at = Column(DateTime, nullable=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, nullable=False)


class UserSubscriptionState(Base):
    __tablename__ = "user_subscription_state"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), nullable=False, unique=True, index=True)
    username = Column(String(50), nullable=True, index=True)
    identity_version = Column(Integer, nullable=True)
    plan_ref = Column(String(100), nullable=False, index=True)
    pricing_mode = Column(String(32), nullable=False, default="free")
    plan_price = Column(Float, nullable=False, default=0)
    daily_quota = Column(Integer, nullable=False, default=0)
    rate_limit_rule = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="free", index=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    source_order_id = Column(Integer, nullable=True, index=True)
    snapshot_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, nullable=False)


class UserAddonWallet(Base):
    __tablename__ = "user_addon_wallet"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), nullable=False, unique=True, index=True)
    username = Column(String(50), nullable=True, index=True)
    identity_version = Column(Integer, nullable=True)
    balance = Column(Integer, nullable=False, default=0)
    total_purchased = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=True, index=True)
    last_order_id = Column(Integer, nullable=True, index=True)
    snapshot_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, nullable=False)


class AnnouncementEntry(Base):
    __tablename__ = "announcement_entry"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    display_mode = Column(String(16), nullable=False, default="banner", index=True)
    show_in_overview = Column(Boolean, nullable=False, default=False, index=True)
    sort_order = Column(Integer, nullable=False, default=100, index=True)
    is_published = Column(Boolean, nullable=False, default=False, index=True)
    published_at = Column(DateTime, nullable=True, index=True)
    updated_by_user_id = Column(Integer, nullable=True, index=True)
    updated_by_username = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class FaqEntry(Base):
    __tablename__ = "faq_entry"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False, default=100, index=True)
    is_published = Column(Boolean, nullable=False, default=False, index=True)
    published_at = Column(DateTime, nullable=True, index=True)
    updated_by_user_id = Column(Integer, nullable=True, index=True)
    updated_by_username = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class SiteConfig(Base):
    __tablename__ = "site_config"

    id = Column(Integer, primary_key=True, index=True)
    config_key = Column(String(100), unique=True, nullable=False, index=True)
    config_group = Column(String(64), nullable=False, index=True)
    value_json = Column(Text, nullable=False)
    value_type = Column(String(32), nullable=False)
    is_secret = Column(Boolean, nullable=False, default=False)
    requires_restart = Column(Boolean, nullable=False, default=False)
    value_fingerprint = Column(String(64), nullable=False, index=True)
    updated_by_user_id = Column(Integer, nullable=True, index=True)
    updated_by_username = Column(String(50), nullable=True, index=True)
    updated_from_ip = Column(String(64), nullable=True)
    updated_note = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class SiteConfigAuditLog(Base):
    __tablename__ = "site_config_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    config_key = Column(String(100), nullable=False, index=True)
    config_group = Column(String(64), nullable=False, index=True)
    operation = Column(String(32), nullable=False, index=True)
    is_secret = Column(Boolean, nullable=False, default=False)
    requires_restart = Column(Boolean, nullable=False, default=False)
    old_value_json = Column(Text, nullable=True)
    new_value_json = Column(Text, nullable=True)
    old_value_summary = Column(Text, nullable=True)
    new_value_summary = Column(Text, nullable=True)
    old_value_fingerprint = Column(String(64), nullable=True, index=True)
    new_value_fingerprint = Column(String(64), nullable=True, index=True)
    changed_by_user_id = Column(Integer, nullable=True, index=True)
    changed_by_username = Column(String(50), nullable=True, index=True)
    changed_from_ip = Column(String(64), nullable=True)
    change_note = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
