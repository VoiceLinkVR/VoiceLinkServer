import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text, UniqueConstraint

from core.identity import generate_user_uuid

from .base import Base


def utcnow_naive_value() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    username = Column(String(254), nullable=True, index=True)
    password = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    limit_rule = Column(String(254), nullable=True)
    expiration_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=False)
    boost_expiration_date = Column(DateTime, nullable=True)
    boost_limit_rule = Column(String(100), nullable=True)
    addon_resource_balance = Column(Integer, nullable=True, default=0)
    addon_resource_total = Column(Integer, nullable=True, default=0)
    addon_expiration_date = Column(DateTime, nullable=True)


class V2User(Base):
    __tablename__ = "v2_user"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), unique=True, nullable=False, index=True, default=generate_user_uuid)
    legacy_user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    email_verified = Column(Boolean, default=False, nullable=False)
    is_default_test_user = Column(Boolean, default=False, nullable=False, index=True)
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
    __tablename__ = "v2_user_identity_link"

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
    __tablename__ = "v2_account_token"

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
    __tablename__ = "v2_user_session"

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
    __tablename__ = "v2_refresh_token"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    session_id = Column(String(128), nullable=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class DeviceAuthorization(Base):
    __tablename__ = "v2_device_authorization"

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


class LegacyRequestLog(Base):
    __tablename__ = "request_log"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=True)
    ip = Column(String(45), nullable=False)
    endpoint = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    duration = Column(Float, nullable=False)


class RequestLog(Base):
    __tablename__ = "v2_request_log"

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


class V2RequestAuditLog(Base):
    __tablename__ = "v2_request_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(64), nullable=False, index=True)
    username = Column(String(50), nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    actor_type = Column(String(32), nullable=True, default="authenticated", index=True)
    rate_limit_scope = Column(String(32), nullable=True)
    quota_scope = Column(String(32), nullable=True, index=True)
    ip = Column(String(45), nullable=False, index=True)
    host = Column(String(255), nullable=True, index=True)
    method = Column(String(16), nullable=True, index=True)
    endpoint = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow_naive_value, nullable=False, index=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, index=True)
    status_code = Column(Integer, nullable=True, index=True)
    resource_cost = Column(Integer, nullable=False, default=0)
    resource_deducted = Column(Boolean, nullable=False, default=False, index=True)
    quota_source = Column(String(20), nullable=False, default="none", index=True)
    extra_payload = Column(Text, nullable=True)


class V2UsageEvent(Base):
    __tablename__ = "v2_usage_event"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    subject_type = Column(String(16), nullable=False, index=True)
    subject_key = Column(String(64), nullable=False, index=True)
    username = Column(String(50), nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    actor_type = Column(String(32), nullable=True, default="authenticated", index=True)
    ip = Column(String(45), nullable=True, index=True)
    endpoint = Column(String(255), nullable=False, index=True)
    occurred_at = Column(DateTime, default=utcnow_naive_value, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    status_code = Column(Integer, nullable=True, index=True)
    resource_cost = Column(Integer, nullable=False, default=0)
    resource_deducted = Column(Boolean, nullable=False, default=False, index=True)
    quota_source = Column(String(20), nullable=False, default="none", index=True)
    extra_payload = Column(Text, nullable=True)


class V2SubjectDailyUsage(Base):
    __tablename__ = "v2_subject_daily_usage"
    __table_args__ = (
        UniqueConstraint("usage_date", "subject_type", "subject_key", name="uq_v2_subject_daily_usage"),
    )

    id = Column(Integer, primary_key=True, index=True)
    usage_date = Column(Date, nullable=False, index=True)
    subject_type = Column(String(16), nullable=False, index=True)
    subject_key = Column(String(64), nullable=False, index=True)
    username = Column(String(50), nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    rate_limited_count = Column(Integer, nullable=False, default=0)
    total_count = Column(Integer, nullable=False, default=0)
    resource_cost_sum = Column(Integer, nullable=False, default=0)
    daily_resource_used = Column(Integer, nullable=False, default=0)
    addon_resource_used = Column(Integer, nullable=False, default=0)
    deducted_request_count = Column(Integer, nullable=False, default=0)
    first_request_at = Column(DateTime, nullable=True)
    last_request_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow_naive_value, nullable=False)
    updated_at = Column(DateTime, default=utcnow_naive_value, nullable=False, index=True)


class V2EndpointHourlyStats(Base):
    __tablename__ = "v2_endpoint_hourly_stats"
    __table_args__ = (
        UniqueConstraint("stat_hour", "endpoint", "method", name="uq_v2_endpoint_hourly_stats"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stat_hour = Column(DateTime, nullable=False, index=True)
    endpoint = Column(String(255), nullable=False, index=True)
    method = Column(String(16), nullable=False, index=True)
    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    rate_limited_count = Column(Integer, nullable=False, default=0)
    total_count = Column(Integer, nullable=False, default=0)
    resource_cost_sum = Column(Integer, nullable=False, default=0)
    duration_ms_sum = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow_naive_value, nullable=False)
    updated_at = Column(DateTime, default=utcnow_naive_value, nullable=False, index=True)


class LegacyPaymentOrder(Base):
    __tablename__ = "payment_order"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(128), nullable=True, unique=True, index=True)
    out_trade_no = Column(String(128), nullable=True, unique=True, index=True)
    custom_id = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)
    username = Column(String(50), nullable=True, index=True)
    action = Column(String(32), nullable=True)
    amount = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    activated_at = Column(DateTime, nullable=True)
    activation_source = Column(String(64), nullable=True, index=True)
    activation_error = Column(Text, nullable=True)
    activation_error_at = Column(DateTime, nullable=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, nullable=False)


class PaymentOrder(Base):
    __tablename__ = "v2_payment_order"

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


class SupportTicket(Base):
    __tablename__ = "v2_support_ticket"

    id = Column(Integer, primary_key=True, index=True)
    ticket_no = Column(String(32), unique=True, nullable=False, index=True)
    order_id = Column(Integer, nullable=True, index=True)
    order_no = Column(String(128), nullable=True, index=True)
    out_trade_no = Column(String(128), nullable=True, index=True)
    user_uuid = Column(String(36), nullable=True, index=True)
    username = Column(String(50), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    category = Column(String(32), nullable=False, default="order_activation", index=True)
    subject = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="open", index=True)
    priority = Column(String(16), nullable=False, default="normal", index=True)
    public_reply = Column(Text, nullable=True)
    internal_note = Column(Text, nullable=True)
    last_event_at = Column(DateTime, nullable=True, index=True)
    last_notified_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class SupportTicketEvent(Base):
    __tablename__ = "v2_support_ticket_event"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, nullable=False, index=True)
    actor_type = Column(String(32), nullable=False, default="system", index=True)
    actor_username = Column(String(50), nullable=True, index=True)
    action = Column(String(32), nullable=False, index=True)
    old_status = Column(String(32), nullable=True)
    new_status = Column(String(32), nullable=True, index=True)
    message = Column(Text, nullable=True)
    email_sent = Column(Boolean, nullable=False, default=False)
    email_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class SupportTicketAttachment(Base):
    __tablename__ = "v2_support_ticket_attachment"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, nullable=False, index=True)
    event_id = Column(Integer, nullable=True, index=True)
    storage_path = Column(String(512), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    uploaded_by_user_uuid = Column(String(36), nullable=True, index=True)
    uploaded_by_username = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class UserSubscriptionState(Base):
    __tablename__ = "v2_user_subscription_state"

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
    __tablename__ = "v2_user_addon_wallet"

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
    __tablename__ = "v2_announcement_entry"

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
    __tablename__ = "v2_faq_entry"

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
    __tablename__ = "v2_site_config"

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
    __tablename__ = "v2_site_config_audit_log"

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
