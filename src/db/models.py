from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text

from .base import Base


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(80), nullable=False)
    is_admin = Column(Boolean, default=False)
    limit_rule = Column(String(100), nullable=True)
    expiration_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    boost_expiration_date = Column(DateTime, nullable=True)
    boost_limit_rule = Column(String(100), nullable=True)


class RequestLog(Base):
    __tablename__ = "request_log"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=True)
    ip = Column(String(45), nullable=False)
    endpoint = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    duration = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, index=True)


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
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, nullable=False)
