#!/usr/bin/env python3
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from db.base import SessionLocal
from db.models import User
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_database():
    try:
        db = SessionLocal()
        logger.info("数据库连接成功")

        # 检查表是否存在
        try:
            user_count = db.query(User).count()
            logger.info(f"用户表存在，共有 {user_count} 个用户")

            # 列出所有用户
            users = db.query(User).all()
            for user in users:
                logger.info(f"用户: username={user.username}, is_admin={user.is_admin}, is_active={user.is_active}")
                logger.info(f"密码哈希: {user.password[:30]}...")  # 显示部分哈希用于调试

        except Exception as e:
            logger.error(f"查询用户表失败: {e}")

        db.close()

    except Exception as e:
        logger.error(f"数据库连接失败: {e}")

if __name__ == "__main__":
    check_database()