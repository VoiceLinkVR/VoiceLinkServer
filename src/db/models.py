from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text

from .base import Base


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    limit_rule = Column(String(100), nullable=True)
    expiration_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    boost_expiration_date = Column(DateTime, nullable=True)
    boost_limit_rule = Column(String(100), nullable=True)
    addon_resource_balance = Column(Integer, nullable=False, default=0)
    addon_resource_total = Column(Integer, nullable=False, default=0)
    addon_expiration_date = Column(DateTime, nullable=True)


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
    custom_id = Column(Text, nullable=True, unique=True)
    remark = Column(Text, nullable=True, index=True)
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
