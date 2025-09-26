from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from .base import Base
from datetime import datetime

class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password = Column(String(80), nullable=False)
    is_admin = Column(Boolean, default=False)
    limit_rule = Column(String(100), nullable=True)
    expiration_date = Column(DateTime, nullable=True)  # 有效期字段
    is_active = Column(Boolean, default=True)  # 激活状态字段

class RequestLog(Base):
    __tablename__ = "request_log"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=True)
    ip = Column(String(45), nullable=False)
    endpoint = Column(String(100), nullable=False)
    timestamp = Column(DateTime, default=datetime.now)
    duration = Column(Float, nullable=False)
    status = Column(String(20), nullable=False)  # 状态字段