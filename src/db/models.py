import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text

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
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    email_verified = Column(Boolean, default=False, nullable=False)
    display_name = Column(String(100), nullable=True)
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


class AccountToken(Base):
    __tablename__ = "account_token"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(50), nullable=False, index=True)
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
    ip = Column(String(45), nullable=False)
    endpoint = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    duration = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, index=True)
    resource_cost = Column(Integer, nullable=False, default=0, index=True)
    resource_deducted = Column(Boolean, nullable=False, default=False, index=True)
    quota_source = Column(String(20), nullable=False, default="none", index=True)


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
