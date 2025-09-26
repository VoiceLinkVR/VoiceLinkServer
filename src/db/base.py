from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from core.config import settings

# 根据数据库类型动态设置连接参数
connect_args = {}
if settings.SQL_PATH.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.SQL_PATH,
    connect_args=connect_args,
    pool_size=settings.SQL_POOL_SIZE,         # 建议值：预先建立50个连接
    max_overflow=settings.SQL_MAX_OVERFLOW,     # 建议值：允许额外创建150个，总共200个
    pool_recycle=settings.SQL_POOL_RECYCLE     # 建议值：1小时回收一次空闲连接，避免MySQL主动断开
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()